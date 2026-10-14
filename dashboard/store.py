"""SQLite persistence; collection updates never overwrite application tracking."""
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, parse_qs, urlencode
from .locations import classify_location, normalize_location
from .duplicates import posting_link, similar_groups

STAGES = ('discovered', 'saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn')
JOB_FIELDS = ('title', 'company', 'location', 'description', 'url', 'direct_url', 'posted_date',
              'work_model', 'job_type', 'salary_min', 'salary_max', 'currency', 'salary_interval',
              'industry', 'job_level', 'job_function', 'applicants', 'availability', 'source', 'raw_json',
              'location_status', 'location_note')
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
        CREATE TABLE IF NOT EXISTS job_links (
          url TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          source TEXT NOT NULL DEFAULT 'manual');
        CREATE INDEX IF NOT EXISTS job_links_job ON job_links(job_id);
        CREATE INDEX IF NOT EXISTS jobs_stage ON jobs(stage);
        CREATE INDEX IF NOT EXISTS jobs_follow_up ON jobs(follow_up);
        CREATE TABLE IF NOT EXISTS workflows (
          id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL DEFAULT 'running');
        CREATE TABLE IF NOT EXISTS workflow_jobs (
          workflow_id INTEGER REFERENCES workflows(id), job_id TEXT REFERENCES jobs(id),
          created INTEGER NOT NULL DEFAULT 0, initial_hash TEXT DEFAULT '',
          PRIMARY KEY(workflow_id,job_id));
        CREATE TABLE IF NOT EXISTS run_jobs (
          run_id INTEGER REFERENCES runs(id), job_id TEXT REFERENCES jobs(id),
          PRIMARY KEY(run_id,job_id));
        ''')
        # Additive migrations preserve existing workspaces and application records.
        migrations = {
            'workflow_jobs': {'collected': 'INTEGER DEFAULT 0'},
            'jobs': {'rating_status': "TEXT DEFAULT 'pending'", 'rating_hash': "TEXT DEFAULT ''",
                     'rating_error': "TEXT DEFAULT ''", 'rating_model': "TEXT DEFAULT ''", 'rated_at': "TEXT DEFAULT ''",
                     'location_status': "TEXT DEFAULT 'unclear'", 'location_note': "TEXT DEFAULT ''",
                     'duplicate_of': "TEXT DEFAULT ''"},
            'runs': {'phase': "TEXT DEFAULT 'collecting'", 'kind': "TEXT DEFAULT 'collection'",
                     'rated': 'INTEGER DEFAULT 0', 'cached': 'INTEGER DEFAULT 0', 'unrated': 'INTEGER DEFAULT 0',
                     'rating_error': "TEXT DEFAULT ''", 'pages': 'INTEGER DEFAULT 0',
                     'scanned': 'INTEGER DEFAULT 0', 'heartbeat': "TEXT DEFAULT ''",
                     'collection_note': "TEXT DEFAULT ''", 'provider_blocked': 'INTEGER DEFAULT 0',
                     'workflow_id': 'INTEGER REFERENCES workflows(id)', 'attempts': 'INTEGER DEFAULT 0',
                     'retry_at': "TEXT DEFAULT ''", 'error_kind': "TEXT DEFAULT ''",
                     'checkpoint': "TEXT DEFAULT '{}'", 'collection_done': 'INTEGER DEFAULT 0',
                     'worker_pid': 'INTEGER'},
        }
        for table, fields in migrations.items():
            existing = {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
            for name, definition in fields.items():
                if name not in existing:
                    db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)
        # Existing records predate location labels; keep stages/notes intact.
        for row in db.execute("SELECT id,title,location,work_model FROM jobs WHERE location_note='' OR location_note IS NULL").fetchall():
            evidence = classify_location(dict(row))
            db.execute('UPDATE jobs SET location_status=?,location_note=? WHERE id=?', (*evidence.values(), row['id']))

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
    host = p.hostname.lower()
    query = parse_qs(p.query)
    if (host == 'indeed.com' or host.endswith('.indeed.com')) and query.get('jk'):
        return 'https://www.indeed.com/viewjob?' + urlencode({'jk': query['jk'][0]})
    if (host == 'glassdoor.com' or host.endswith('.glassdoor.com') or host == 'glassdoor.ie' or host.endswith('.glassdoor.ie')):
        jid = (query.get('jl') or query.get('jobListingId') or [None])[0]
        if jid:
            return 'https://www.glassdoor.com/partner/jobListing.htm?' + urlencode({'jobListingId': jid})
    path, netloc = p.path, p.netloc.lower()
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io'):
        netloc = 'job-boards.greenhouse.io'
        match = re.search(r'^/([^/]+)/jobs/(\d+)', path)
        if match:
            return 'https://'+netloc+'/'+match[1]+'/jobs/'+match[2]
    if host in ('jobs.lever.co', 'jobs.eu.lever.co'):
        path = re.sub(r'/apply/?$', '', path).rstrip('/')
    tracking = {'gclid','fbclid','msclkid','mc_cid','mc_eid'}
    params = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
              if not k.lower().startswith('utm_') and k.lower() not in tracking]
    return urlunsplit((p.scheme, netloc, path, urlencode(sorted(params)), ''))


def remember_links(db, jid, data):
    links = [{'url': data.get('url'), 'source': data.get('source') or 'manual'}]
    if posting_link(data.get('direct_url')):
        links.append({'url': data['direct_url'], 'source': data.get('source') or 'manual'})
    if 'listings' in data:
        if not isinstance(data['listings'], list) or len(data['listings'])>200:
            raise ValueError('Source links must be an array of up to 200 links.')
        for link in data['listings']:
            if not isinstance(link, dict) or not isinstance(link.get('source', 'manual'), str):
                raise ValueError('Source links need a URL and platform name.')
            links.append({'url': link.get('url'), 'source': str(link.get('source') or 'manual')[:100]})
    for link in links:
        url = canonical_url(link['url'])
        if url:
            db.execute('INSERT OR IGNORE INTO job_links(url,job_id,source) VALUES(?,?,?)', (url, jid, link['source']))


def library(db):
    """One visible row per confirmed posting, with every original source link."""
    jobs = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE duplicate_of='' ORDER BY created_at DESC")]
    links = {}
    for row in db.execute('SELECT * FROM job_links ORDER BY source,url'):
        links.setdefault(row['job_id'], []).append({'url': row['url'], 'source': row['source']})
    similar = similar_groups(jobs)
    for job in jobs:
        normalized = normalize_location(job)
        job.update(normalized)
        job.update(classify_location(job, normalized))
        job['listings'] = links.get(job['id'], [])
        job['similar_jobs'] = similar.get(job['id'], [])
    return jobs


def reconcile_duplicates(db):
    """Collapse confirmed link matches without deleting original records."""
    collapsed = 0
    known = {}
    rows = db.execute("SELECT * FROM jobs WHERE duplicate_of='' ORDER BY CASE WHEN stage!='discovered' OR notes!='' OR follow_up!='' OR contact!='' OR applied_date!='' THEN 0 ELSE 1 END,created_at,id").fetchall()
    for row in rows:
        data = dict(row)
        urls = [canonical_url(data.get('url'))]
        if posting_link(data.get('direct_url')):
            urls.append(canonical_url(data['direct_url']))
        urls.extend(r['url'] for r in db.execute('SELECT url FROM job_links WHERE job_id=?', (data['id'],)))
        owner = None
        for url in urls:
            if url and url in known:
                owner = db.execute('SELECT * FROM jobs WHERE id=?', (known[url],)).fetchone()
                if owner:
                    break
        if owner:
            # Never hide a separately tracked application or discard its notes.
            separately_tracked = any(data[k] and (k!='stage' or data[k]!='discovered') and data[k]!=owner[k] for k in TRACK_FIELDS)
            if not separately_tracked:
                for key in JOB_FIELDS:
                    if not owner[key] and data.get(key):
                        db.execute('UPDATE jobs SET '+key+'=? WHERE id=?', (data[key],owner['id']))
                        if key in FIT_FIELDS:
                            db.execute("UPDATE jobs SET rating_status='pending',rating_error='Additional posting details found; waiting for a fresh rating.' WHERE id=?", (owner['id'],))
                db.execute('UPDATE jobs SET duplicate_of=? WHERE id=?', (owner['id'],data['id']))
                db.execute('UPDATE job_links SET job_id=? WHERE job_id=?', (owner['id'],data['id']))
                db.execute('UPDATE events SET job_id=? WHERE job_id=?', (owner['id'],data['id']))
                remember_links(db, owner['id'], data)
                for url in urls:
                    if url:
                        known[url] = owner['id']
                db.execute('INSERT INTO events(job_id,message,created_at) VALUES(?,?,?)', (owner['id'],'Confirmed duplicate source link grouped; original record retained: '+data['id'],now()))
                collapsed += 1
                continue
        remember_links(db, data['id'], data)
        for url in urls:
            if url:
                known.setdefault(url, data['id'])
    return collapsed

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
    if existing and existing['duplicate_of']:
        existing = db.execute('SELECT * FROM jobs WHERE id=?', (existing['duplicate_of'],)).fetchone()
    if not existing and not tracking:
        for url in (data['url'], data.get('direct_url') if posting_link(data.get('direct_url')) else None):
            if url:
                existing = db.execute('SELECT j.* FROM job_links l JOIN jobs j ON j.id=l.job_id WHERE l.url=?', (url,)).fetchone()
                if existing:
                    break
    values = {k: data[k] for k in JOB_FIELDS if k in data}
    values['updated_at'] = now()
    if tracking:
        values.update(validate_tracking(data))
    if existing:
        incoming_id = job_id
        job_id = existing['id']
        remember_links(db, job_id, data)
        if not tracking:
            # Keep the original platform URL/provenance when another board
            # points to the same employer posting. Aliases retain the new links.
            for key in ('url', 'source', 'direct_url'):
                if existing[key]:
                    values.pop(key, None)
            if str(data.get('source') or '') != existing['source'] and len(str(values.get('description') or '')) <= len(existing['description'] or ''):
                values.pop('description', None)
        # Preserve richer details when a refresh returns missing fields.
        values = {k: v for k, v in values.items() if tracking or v not in (None, '') or k in TRACK_FIELDS}
        if tracking and incoming_id != job_id:
            for key in TRACK_FIELDS:
                if existing[key] and (not values.get(key) or key=='stage' and values.get(key)=='discovered'):
                    values.pop(key, None)
        if any(k in values and values[k] != existing[k] for k in FIT_FIELDS):
            values.update(rating_status='pending', rating_error='Job details changed; waiting for a fresh rating.')
        db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                   [*values.values(), job_id])
        return job_id, False
    values.update(id=job_id, created_at=now())
    db.execute('INSERT INTO jobs (' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', list(values.values()))
    remember_links(db, job_id, data)
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
