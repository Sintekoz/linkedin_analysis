"""Loopback-only HTTP server for the local HTML dashboard."""
import argparse
import csv
import importlib.util
import io
import json
import mimetypes
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from . import store
from .collectors import validate_config, PROVIDERS
from . import rating
from .locations import classify_location

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'dashboard' / 'static'

class App:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.token = secrets.token_urlsafe(32)
        self.processes = {}
        self.lock = threading.RLock()
        self.rating_info = None
        self.rating_checked = 0
        store.init(self.path)
        with store.connect(self.path) as db:
            db.execute("UPDATE runs SET status='interrupted',message='Dashboard restarted before collection or rating finished. Saved jobs are retained.',finished_at=? WHERE status IN ('running','queued')", (store.now(),))

    def snapshot(self):
        self.reap()
        if self.rating_info is None or time.monotonic() - self.rating_checked > 60:
            self.rating_info = rating.cli_status()
            self.rating_checked = time.monotonic()
        with store.connect(self.path) as db:
            return dict(jobs=[dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY created_at DESC')],
                sources=[dict(r) | {'config': json.loads(r['config'])} for r in db.execute('SELECT * FROM sources ORDER BY id DESC')],
                runs=[dict(r) for r in db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 100')],
                settings={r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')},
                stages=store.STAGES, providers=PROVIDERS, jobspy=bool(importlib.util.find_spec('jobspy')), rating=self.rating_info, token=self.token)

    def reap(self):
        with self.lock:
            for rid, process in list(self.processes.items()):
                code = process.poll()
                if code is not None:
                    with store.connect(self.path) as db:
                        db.execute("UPDATE runs SET status='failed',message='Background process exited unexpectedly.',finished_at=? WHERE id=? AND status='running'", (store.now(), rid))
                        outcome = db.execute('SELECT rating_error FROM runs WHERE id=?', (rid,)).fetchone()
                        if outcome and outcome['rating_error']:
                            db.execute("UPDATE runs SET status='cancelled',message=?,finished_at=? WHERE status='queued'",
                                       ('Workflow stopped because ratings need attention: '+outcome['rating_error'], store.now()))
                    del self.processes[rid]
                else:
                    with store.connect(self.path) as db:
                        phase = db.execute('SELECT phase FROM runs WHERE id=?', (rid,)).fetchone()[0]
                    if phase != process.phase:
                        process.phase, process.phase_started = phase, time.monotonic()
                    limit = 1800 if phase == 'rating' else 600
                    if time.monotonic() - process.phase_started > limit:
                        self.stop(rid, 'timed_out', 'Stopped after the '+('30 minute rating' if phase == 'rating' else '10 minute collection')+' limit. Saved jobs and ratings are retained.')
            self.launch_next()

    def launch_next(self):
        if self.processes:
            return
        with store.connect(self.path) as db:
            row = db.execute("SELECT id FROM runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return
            rid = row['id']
            db.execute("UPDATE runs SET status='running',phase='collecting',started_at=?,message='Collecting jobs; ratings will follow automatically.' WHERE id=?", (store.now(), rid))
        try:
            self.launch(rid)
        except Exception:
            with store.connect(self.path) as db:
                db.execute("UPDATE runs SET status='cancelled',message='Workflow stopped: a background worker could not start.',finished_at=? WHERE status='queued'", (store.now(),))

    def launch(self, rid, phase='collecting'):
        try:
            process = subprocess.Popen([sys.executable, '-m', 'dashboard.worker', '--db', self.path, '--run', str(rid)], cwd=ROOT,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            process.started = process.phase_started = time.monotonic()
            process.phase = phase
            self.processes[rid] = process
        except Exception:
            with store.connect(self.path) as db:
                db.execute("UPDATE runs SET status='failed',message='Could not launch background worker.',finished_at=? WHERE id=?", (store.now(), rid))
            raise
        return {'id': rid}

    def start(self, source_id):
        with self.lock:
            self.reap()
            if self.processes:
                raise ValueError('A collection or rating is already running. Wait or stop it before starting another.')
            with store.connect(self.path) as db:
                source = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
                if not source:
                    raise LookupError('Search not found.')
                rid = db.execute("INSERT INTO runs(source_id,name,config,status,message,started_at) VALUES(?,?,?,'running',?,?)",
                    (source_id, source['name'], source['config'], 'Fetching jobs. Full descriptions can take a few minutes.', store.now())).lastrowid
            return self.launch(rid)

    def start_all(self, data):
        with self.lock:
            self.reap()
            if self.processes:
                raise ValueError('A workflow is already running. Wait or stop it first.')
            override = data.get('limit')
            if override is not None and (type(override) is not int or not 1 <= override <= 100):
                raise ValueError('The per-search limit must be an integer from 1 to 100.')
            requested = data.get('source_ids')
            if requested is not None and (not isinstance(requested, list) or not requested or
                    len(requested) > 100 or any(type(i) is not int for i in requested)):
                raise ValueError('Choose up to 100 source IDs.')
            with store.connect(self.path) as db:
                sources = db.execute('SELECT * FROM sources ORDER BY id').fetchall()
                if requested is not None:
                    if not set(requested) <= {s['id'] for s in sources}:
                        raise LookupError('A selected search was not found.')
                    sources = [s for s in sources if s['id'] in requested]
                if not sources or len(sources) > 100:
                    raise ValueError('Add at least one search; a workflow can run up to 100 searches.')
                ids = []
                for source in sources:
                    config = validate_config(json.loads(source['config']))
                    if override is not None:
                        config['limit'] = min(config['limit'], override)
                    ids.append(db.execute("INSERT INTO runs(source_id,name,config,status,phase,message,started_at) VALUES(?,?,?,'queued','queued',?,?)",
                        (source['id'], source['name'], json.dumps(config), 'Waiting for the previous search and ratings to finish.', store.now())).lastrowid)
            self.launch_next()
            return {'ids': ids, 'queued': len(ids)}

    def stop_all(self, status='cancelled', message='Workflow stopped. Saved jobs and ratings are retained.'):
        with self.lock:
            with store.connect(self.path) as db:
                db.execute('UPDATE runs SET status=?,message=?,finished_at=? WHERE status=?', (status, message, store.now(), 'queued'))
            for rid in list(self.processes):
                self.stop(rid, status, message)
            return {'ok': True}

    def start_ratings(self):
        with self.lock:
            self.reap()
            if self.processes:
                raise ValueError('A collection or rating is already running. Wait or stop it first.')
            with store.connect(self.path) as db:
                ids = [r['id'] for r in db.execute("SELECT id FROM jobs WHERE rating_status!='complete' ORDER BY created_at DESC LIMIT 100")]
                if not ids:
                    raise ValueError('No jobs are waiting for a rating.')
                rid = db.execute("INSERT INTO runs(name,config,status,kind,phase,message,started_at) VALUES(?,?,'running','rating','rating',?,?)",
                                 ('Waiting job ratings', json.dumps({'job_ids': ids}), 'Checking waiting jobs with Luna…', store.now())).lastrowid
            return self.launch(rid, 'rating')

    def stop(self, rid, status='cancelled', message='Stopped by you. Already saved jobs are retained.'):
        with self.lock:
            process = self.processes.get(rid)
            if process:
                def terminate(hard=False):
                    try:
                        if os.name == 'posix':
                            os.killpg(process.pid, signal.SIGKILL if hard else signal.SIGTERM)
                        elif os.name == 'nt':
                            # Windows has no POSIX process groups; stop the entire tree.
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                        elif hard:
                            process.kill()
                        else:
                            process.terminate()
                    except ProcessLookupError:
                        pass
                terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    terminate(hard=True)
                    process.wait()
                self.processes.pop(rid, None)
            with store.connect(self.path) as db:
                db.execute("UPDATE runs SET status=?,message=?,finished_at=? WHERE id=? AND status IN ('running','queued')", (status, message, store.now(), rid))
            return {'ok': True}

    def mutate(self, route, data):
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object.')
        bits = route.strip('/').split('/')
        if route == '/api/jobs':
            with store.connect(self.path) as db:
                jid, created = store.upsert(db, data, tracking=True)
                job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
                location = classify_location(job)
                db.execute('UPDATE jobs SET location_status=?,location_note=? WHERE id=?', (*location.values(), jid))
            return {'id': jid, 'created': created}
        if len(bits) == 3 and bits[:2] == ['api', 'jobs']:
            store.update_tracking(self.path, bits[2], data)
            return {'ok': True}
        if route == '/api/sources/run-all':
            return self.start_all(data)
        if route == '/api/runs/stop-all':
            return self.stop_all()
        if route == '/api/sources' or (len(bits) == 3 and bits[:2] == ['api', 'sources']):
            name = str(data.get('name') or '').strip()
            if not name:
                raise ValueError('Give the search a name.')
            config = json.dumps(validate_config(data.get('config', {})))
            with store.connect(self.path) as db:
                if route == '/api/sources':
                    sid = db.execute('INSERT INTO sources(name,config,created_at) VALUES(?,?,?)', (name, config, store.now())).lastrowid
                else:
                    sid = int(bits[2])
                    if db.execute('UPDATE sources SET name=?,config=? WHERE id=?', (name, config, sid)).rowcount == 0:
                        raise LookupError('Search not found.')
            return {'id': sid}
        if len(bits) == 4 and bits[:2] == ['api', 'sources'] and bits[3] == 'run':
            return self.start(int(bits[2]))
        if len(bits) == 4 and bits[:2] == ['api', 'runs'] and bits[3] == 'stop':
            return self.stop(int(bits[2]))
        if route == '/api/settings':
            allowed = set(rating.PROFILE_FIELDS)
            with store.connect(self.path) as db:
                before = rating.settings_from(db)
                for key in allowed & data.keys():
                    db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, json.dumps(str(data[key])[:100000])))
                if any(before.get(k, '') != str(data[k])[:100000] for k in allowed & data.keys()):
                    db.execute("UPDATE jobs SET rating_status='pending',rating_error='Profile changed; waiting for a fresh rating.'")
            return {'ok': True}
        if route == '/api/ratings/run':
            return self.start_ratings()
        if route == '/api/import':
            rows = data.get('jobs')
            if not isinstance(rows, list) or len(rows) > 5000:
                raise ValueError('Import must contain a jobs array with up to 5,000 jobs.')
            added = 0
            with store.connect(self.path) as db:
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError('Every imported job must be an object.')
                    jid, created = store.upsert(db, row, tracking=True)
                    added += int(created)
                    for field in ('analysis', 'cover_letter'):
                        if row.get(field):
                            db.execute('UPDATE jobs SET '+field+'=? WHERE id=?', (str(row[field]), jid))
                            if field == 'analysis':
                                db.execute("UPDATE jobs SET rating_status='pending',rating_hash='',rating_error='Imported review needs verification against your current profile.' WHERE id=?", (jid,))
            return {'added': added, 'updated': len(rows)-added}
        if len(bits) == 4 and bits[:2] == ['api', 'jobs'] and bits[3] in ('analyze', 'letter'):
            return self.ai(bits[2], bits[3])
        raise LookupError('Route not found.')

    def ai(self, jid, action):
        with store.connect(self.path) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            settings = rating.settings_from(db)
        if not row:
            raise LookupError('Job not found.')
        if action == 'analyze':
            rating.rate_jobs(self.path, [jid])
            with store.connect(self.path) as db:
                outcome = db.execute('SELECT rating_status,rating_error FROM jobs WHERE id=?', (jid,)).fetchone()
            if outcome['rating_status'] != 'complete':
                raise ValueError(outcome['rating_error'])
            return {'ok': True}
        result = rating.cover_letter(dict(row), settings)
        with store.connect(self.path) as db:
            db.execute('UPDATE jobs SET cover_letter=?,updated_at=? WHERE id=?', (result, store.now(), jid))
            db.execute('INSERT INTO events(job_id,message,created_at) VALUES(?,?,?)', (jid, 'AI '+('fit analysis' if action == 'analyze' else 'cover letter')+' generated', store.now()))
        return {'ok': True}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def guard(self, mutate=False):
        expected = {'127.0.0.1:'+str(self.server.server_port), 'localhost:'+str(self.server.server_port)}
        if self.headers.get('Host') not in expected:
            self.reply(403, {'error': 'Invalid host.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://'+h for h in expected}:
            self.reply(403, {'error': 'Invalid origin.'})
            return False
        if mutate and not secrets.compare_digest(self.headers.get('X-Workspace-Token', ''), self.server.app.token):
            self.reply(403, {'error': 'Reload the dashboard before making changes.'})
            return False
        return True

    def reply(self, code, value, kind='application/json; charset=utf-8', disposition=None):
        body = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if disposition:
            self.send_header('Content-Disposition', disposition)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.guard():
            return
        route = urlsplit(self.path).path
        app = self.server.app
        if route == '/api/state':
            self.reply(200, app.snapshot())
        elif route == '/api/export':
            with store.connect(app.path) as db:
                jobs = [dict(r) for r in db.execute('SELECT * FROM jobs')]
            self.reply(200, {'version': 1, 'exported_at': store.now(), 'jobs': jobs}, disposition='attachment; filename="job-search-backup.json"')
        elif route == '/api/export.csv':
            with store.connect(app.path) as db:
                rows = [dict(r) for r in db.execute('SELECT * FROM jobs')]
            output = io.StringIO()
            fields = ('id', 'title', 'company', 'location', 'url', 'stage', 'follow_up', 'applied_date', 'notes')
            writer = csv.DictWriter(output, fields, extrasaction='ignore')
            writer.writeheader()
            for r in rows:
                # Prevent spreadsheet formula execution on export.
                writer.writerow({k: "'"+str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v for k,v in r.items()})
            self.reply(200, output.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8', 'attachment; filename="applications.csv"')
        elif route.startswith('/api/jobs/') and route.endswith('/events'):
            jid = route.split('/')[3]
            with store.connect(app.path) as db:
                events = [dict(r) for r in db.execute('SELECT * FROM events WHERE job_id=? ORDER BY id DESC', (jid,))]
            self.reply(200, events)
        else:
            file = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(route)
            if file:
                self.reply(200, (STATIC/file).read_bytes(), mimetypes.guess_type(file)[0]+'; charset=utf-8')
            else:
                self.reply(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.guard(mutate=True):
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected application/json.')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 10_000_000:
                raise ValueError('Request must be between 1 byte and 10 MB.')
            data = json.loads(self.rfile.read(size))
            result = self.server.app.mutate(urlsplit(self.path).path, data)
            self.reply(200, result)
        except LookupError as error:
            self.reply(404, {'error': str(error)})
        except (ValueError, TypeError, sqlite3.IntegrityError) as error:
            self.reply(400, {'error': str(error)})
        except Exception:
            self.reply(500, {'error': 'The action failed. Check your connection and configuration, then try again.'})

    def do_DELETE(self):
        if not self.guard(mutate=True):
            return
        route = urlsplit(self.path).path.strip('/').split('/')
        if len(route) == 3 and route[:2] == ['api', 'sources']:
            with store.connect(self.server.app.path) as db:
                db.execute('DELETE FROM sources WHERE id=?', (route[2],))
            self.reply(200, {'ok': True})
        else:
            self.reply(404, {'error': 'Not found.'})

def main():
    parser = argparse.ArgumentParser(description='Run your local job search dashboard.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', default=str(ROOT/'data'/'job_search.db'))
    parser.add_argument('--legacy-db', help='Read-only import of an original linkedin_jobs.db')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    app = App(args.db)
    if args.legacy_db:
        print('Imported', store.import_legacy(app.path, args.legacy_db), 'legacy jobs.')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.app = app
    url = 'http://127.0.0.1:'+str(server.server_port)
    print('Job search dashboard: '+url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    monitor_stop = threading.Event()
    def monitor():
        while not monitor_stop.wait(1):
            app.reap()
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=5)
        app.stop_all('interrupted', 'Dashboard stopped. Saved jobs and ratings are retained.')
        server.server_close()

if __name__ == '__main__':
    main()
