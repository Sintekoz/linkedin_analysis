"""SQLite persistence; collection updates never overwrite application tracking."""
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

STAGES = ('discovered', 'saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn')
JOB_FIELDS = ('title', 'company', 'location', 'description', 'url', 'direct_url', 'posted_date',
              'work_model', 'job_type', 'salary_min', 'salary_max', 'currency', 'salary_interval',
              'industry', 'job_level', 'job_function', 'applicants', 'availability', 'source', 'raw_json')
TRACK_FIELDS = ('stage', 'notes', 'follow_up', 'contact', 'applied_date')
FIT_FIELDS = ('title', 'company', 'location', 'description', 'work_model', 'job_type',
              'salary_min', 'salary_max', 'currency', 'salary_interval', 'industry', 'job_level', 'job_function')

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '',
          location TEXT DEFAULT '', description TEXT DEFAULT '', url TEXT UNIQUE,
          direct_url TEXT DEFAULT '', posted_date TEXT DEFAULT '', work_model TEXT DEFAULT '',
          job_type TEXT DEFAULT '', salary_min REAL, salary_max REAL, currency TEXT DEFAULT '',
          salary_interval TEXT DEFAULT '', industry TEXT DEFAULT '', job_level TEXT DEFAULT '',
          job_function TEXT DEFAULT '', applicants TEXT DEFAULT '', availability TEXT DEFAULT 'unknown',
          source TEXT DEFAULT 'manual', raw_json TEXT DEFAULT '{}',
          stage TEXT DEFAULT 'discovered', notes TEXT DEFAULT '', follow_up TEXT DEFAULT '',
          contact TEXT DEFAULT '', applied_date TEXT DEFAULT '', analysis TEXT DEFAULT '',
          cover_letter TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
          message TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
          name TEXT NOT NULL, config TEXT NOT NULL, status TEXT NOT NULL,
          found INTEGER DEFAULT 0, added INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
          message TEXT DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_stage ON jobs(stage);
        CREATE INDEX IF NOT EXISTS jobs_follow_up ON jobs(follow_up);
        ''')
        # Additive migrations preserve existing workspaces and application records.
        migrations = {
            'jobs': {'rating_status': "TEXT DEFAULT 'pending'", 'rating_hash': "TEXT DEFAULT ''",
                     'rating_error': "TEXT DEFAULT ''", 'rating_model': "TEXT DEFAULT ''", 'rated_at': "TEXT DEFAULT ''"},
            'runs': {'phase': "TEXT DEFAULT 'collecting'", 'kind': "TEXT DEFAULT 'collection'",
                     'rated': 'INTEGER DEFAULT 0', 'cached': 'INTEGER DEFAULT 0', 'unrated': 'INTEGER DEFAULT 0'},
        }
        for table, fields in migrations.items():
            existing = {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
            for name, definition in fields.items():
                if name not in existing:
                    db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)

def canonical_url(value):
    value = str(value or '').strip()
    if not value:
        return None
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Job links must be valid http or https URLs.')
    if (p.hostname.lower() == 'linkedin.com' or p.hostname.lower().endswith('.linkedin.com')):
        m = re.search(r'/jobs/view/(?:[^/]*-)?(\d+)', p.path)
        if m:
            return 'https://www.linkedin.com/jobs/view/' + m.group(1) + '/'
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, p.query, ''))

def validate_tracking(data):
    if 'stage' in data and data['stage'] not in STAGES:
        raise ValueError('Unknown application stage.')
    for key in ('follow_up', 'applied_date'):
        if data.get(key):
            try:
                datetime.strptime(data[key], '%Y-%m-%d')
            except (ValueError, TypeError):
                raise ValueError('Dates must use YYYY-MM-DD.')
    return {k: str(data[k] or '') for k in TRACK_FIELDS if k in data}

def upsert(db, data, tracking=False):
    data = dict(data)
    title = str(data.get('title') or '').strip()
    if not title:
        raise ValueError('A job title is required.')
    for field in ('salary_min', 'salary_max'):
        if data.get(field) is not None:
            try:
                number = float(data[field])
            except (ValueError, TypeError):
                raise ValueError('Salary values must be valid numbers.')
            if not math.isfinite(number) or number < 0:
                raise ValueError('Salary values must be finite, non-negative numbers.')
            data[field] = number
    if data.get('salary_min') is not None and data.get('salary_max') is not None and data['salary_min'] > data['salary_max']:
        raise ValueError('Minimum salary cannot exceed maximum salary.')
    data['title'] = title
    data['url'] = canonical_url(data.get('url'))
    if data.get('direct_url'):
        data['direct_url'] = canonical_url(data['direct_url'])
    job_id = str(data.get('id') or hashlib.sha256((data['url'] or '|'.join(
        str(data.get(k, '')) for k in ('source', 'title', 'company', 'location'))).encode()).hexdigest()[:24])
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', job_id):
        raise ValueError('Job IDs must use letters, numbers, dots, colons, underscores or hyphens.')
    existing = db.execute('SELECT * FROM jobs WHERE id=? OR (url IS NOT NULL AND url=?)',
                          (job_id, data['url'])).fetchone()
    values = {k: data[k] for k in JOB_FIELDS if k in data}
    values['updated_at'] = now()
    if tracking:
        values.update(validate_tracking(data))
    if existing:
        job_id = existing['id']
        # Preserve richer details when a refresh returns missing fields.
        values = {k: v for k, v in values.items() if tracking or v not in (None, '') or k in TRACK_FIELDS}
        if any(k in values and values[k] != existing[k] for k in FIT_FIELDS):
            values.update(rating_status='pending', rating_error='Job details changed; waiting for a fresh rating.')
        db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                   [*values.values(), job_id])
        return job_id, False
    values.update(id=job_id, created_at=now())
    db.execute('INSERT INTO jobs (' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', list(values.values()))
    db.execute('INSERT INTO events(job_id,message,created_at) VALUES(?,?,?)', (job_id, 'Job added to workspace', now()))
    return job_id, True

def update_tracking(path, job_id, data):
    values = validate_tracking(data)
    with connect(path) as db:
        old = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not old:
            raise LookupError('Job not found.')
        if values.get('stage') == 'applied' and not old['applied_date'] and not values.get('applied_date'):
            values['applied_date'] = datetime.now().date().isoformat()
        if values:
            db.execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in values) + ',updated_at=? WHERE id=?', [*values.values(), now(), job_id])
            changes = [k.replace('_', ' ') for k, v in values.items() if old[k] != v]
            if changes:
                db.execute('INSERT INTO events(job_id,message,created_at) VALUES(?,?,?)',
                           (job_id, 'Updated ' + ', '.join(changes) + (': '+values['stage'] if 'stage' in changes else ''), now()))

def import_legacy(path, legacy):
    """Read-only import of the original database; repeatable without losing tracking."""
    legacy = Path(legacy).resolve()
    if not legacy.is_file():
        return 0
    old = sqlite3.connect(legacy.as_uri() + '?mode=ro', uri=True)
    old.row_factory = sqlite3.Row
    try:
        rows = old.execute('SELECT ji.*, jd.* FROM job_ids ji LEFT JOIN job_description jd USING(job_id)').fetchall()
        tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        analyses = {str(r[0]): r[1] for r in old.execute('SELECT job_id,chatgpt_message FROM chatgpt_analysis')} if 'chatgpt_analysis' in tables else {}
        with connect(path) as db:
            for row in rows:
                r = dict(row)
                jid, _ = upsert(db, dict(title=r.get('title_name') or 'Untitled LinkedIn job', company=r.get('company_name') or '',
                    url=r.get('job_url') or 'https://www.linkedin.com/jobs/view/'+str(r['job_id'])+'/',
                    description=r.get('job_description') or '', location=r.get('location') or '', posted_date=r.get('posted_date') or '',
                    applicants=r.get('number_applicants') or '', work_model=r.get('work_model') or '', source='linkedin',
                    availability=r.get('status') or 'unknown'))
                analysis = analyses.get(r['job_id'], analyses.get(str(r['job_id']), ''))
                if analysis:
                    db.execute("UPDATE jobs SET analysis=? WHERE id=? AND analysis=''", (json.dumps({'legacy_message': analysis}), jid))
        return len(rows)
    finally:
        old.close()
