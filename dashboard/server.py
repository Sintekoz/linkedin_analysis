"""Loopback-only HTTP server for the local HTML dashboard."""
import argparse
import csv
import importlib.util
import io
import json
import mimetypes
import os
import re
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
from . import workflows
from . import availability
from . import applications
from .locations import classify_location

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'dashboard' / 'static'

class App:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.token = secrets.token_urlsafe(32)
        self.processes = {}
        self.application_processes = {}
        self.lock = threading.RLock()
        self.rating_info = None
        self.rating_checked = 0
        store.init(self.path)
        with store.connect(self.path) as db:
            # A crash can leave an isolated worker behind. Stop only a verified
            # worker for this database/run before allowing another writer to resume it.
            for row in db.execute("SELECT id,worker_pid FROM runs WHERE status='running' AND workflow_id IS NOT NULL").fetchall():
                if row['worker_pid'] and os.name=='posix':
                    try:
                        command = subprocess.run(['ps','-p',str(row['worker_pid']),'-o','command='], capture_output=True,text=True,timeout=3).stdout
                        import shlex
                        args = shlex.split(command)
                        if '-m' in args and args[args.index('-m')+1]=='dashboard.worker' and '--db' in args and args[args.index('--db')+1]==self.path and '--run' in args and args[args.index('--run')+1]==str(row['id']):
                            os.killpg(row['worker_pid'], signal.SIGKILL)
                    except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):
                        pass
            db.execute("UPDATE runs SET status='queued',phase='queued',attempts=MAX(0,attempts-1),worker_pid=NULL,finished_at=NULL,message='Resuming saved progress after restart.' WHERE status IN ('running','interrupted') AND workflow_id IN (SELECT id FROM workflows WHERE status='running')")
            db.execute("UPDATE runs SET status='interrupted',message='Dashboard restarted before collection or rating finished. Saved jobs are retained.',finished_at=? WHERE status IN ('running','queued') AND workflow_id IS NULL", (store.now(),))
            for workflow in db.execute("SELECT id FROM workflows w WHERE status='running' AND EXISTS(SELECT 1 FROM runs WHERE workflow_id=w.id AND kind='rating') AND EXISTS(SELECT 1 FROM runs WHERE workflow_id=w.id AND kind='collection') AND NOT EXISTS(SELECT 1 FROM runs WHERE workflow_id=w.id AND kind='availability')").fetchall():
                db.execute("INSERT INTO runs(name,config,status,kind,phase,message,started_at,workflow_id) VALUES(?,'{}','queued','availability','queued',?,?,?)",('Posting availability','Checking saved vacancies after collection finishes.',store.now(),workflow['id']))
            for row in db.execute("SELECT id,worker_pid FROM application_tasks WHERE status='running'").fetchall():
                if row['worker_pid'] and os.name=='posix':
                    try:
                        command=subprocess.run(['ps','-p',str(row['worker_pid']),'-o','command='],capture_output=True,text=True,timeout=3).stdout
                        import shlex
                        args=shlex.split(command)
                        if '-m' in args and args[args.index('-m')+1]=='dashboard.applications' and '--db' in args and args[args.index('--db')+1]==self.path and '--task' in args and args[args.index('--task')+1]==str(row['id']):
                            os.killpg(row['worker_pid'],signal.SIGKILL)
                    except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):
                        pass
            db.execute("UPDATE application_tasks SET status='interrupted',message='Dashboard restarted during preparation. Inspect the browser form before retrying; nothing was marked Applied.',updated_at=? WHERE status='running'",(store.now(),))

    def snapshot(self):
        self.reap()
        if self.rating_info is None or time.monotonic() - self.rating_checked > 60:
            self.rating_info = rating.cli_status()
            self.rating_checked = time.monotonic()
        with store.connect(self.path) as db:
            return dict(jobs=store.library(db),
                workflows=workflows.summaries(db),
                application_tasks=[dict(r) | {'has_review_image':(applications.folder(self.path,r['id'])/'review.png').is_file()} for r in db.execute("SELECT * FROM application_tasks WHERE status IN ('running','queued','ready_review','needs_answers','needs_login','needs_browser','interrupted','needs_attention') OR id IN (SELECT id FROM application_tasks ORDER BY id DESC LIMIT 100) ORDER BY id DESC")],
                application_cv=applications.cv_path(self.path).is_file(),
                posting_counts={'open':db.execute("SELECT COUNT(*) FROM jobs WHERE duplicate_of='' AND posting_status='open'").fetchone()[0],
                    'closed':db.execute("SELECT COUNT(*) FROM jobs WHERE duplicate_of='' AND posting_status='closed'").fetchone()[0],
                    'unverified':db.execute("SELECT COUNT(*) FROM jobs WHERE duplicate_of='' AND posting_status NOT IN ('open','closed')").fetchone()[0]},
                duplicates=db.execute("SELECT count(*) FROM jobs WHERE duplicate_of!=''").fetchone()[0],
                sources=[dict(r) | {'config': json.loads(r['config'])} for r in db.execute('SELECT * FROM sources ORDER BY id DESC')],
                runs=[dict(r) for r in db.execute("SELECT * FROM runs WHERE status IN ('running','queued','retry_wait') OR workflow_id IN (SELECT id FROM workflows WHERE status='running') OR workflow_id IN (SELECT id FROM workflows ORDER BY id DESC LIMIT 20) OR id IN (SELECT id FROM runs ORDER BY id DESC LIMIT 100) ORDER BY id DESC")],
                settings={r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')},
                stages=store.STAGES, providers=PROVIDERS, jobspy=bool(importlib.util.find_spec('jobspy')), rating=self.rating_info, token=self.token)

    def reap(self):
        with self.lock:
            self.reap_applications()
            for rid, process in list(self.processes.items()):
                code = process.poll()
                if code is not None:
                    with store.connect(self.path) as db:
                        db.execute("UPDATE runs SET status='failed',error_kind='temporary',message='Background process exited unexpectedly. Saved batches are retained.',finished_at=? WHERE id=? AND status='running'", (store.now(), rid))
                        outcome = db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
                        if outcome and outcome['workflow_id']:
                            self.recover_run(db,outcome)
                        elif outcome and outcome['rating_error']:
                            for queued in db.execute("SELECT id,config FROM runs WHERE kind='collection' AND status='queued'").fetchall():
                                config = json.loads(queued['config'])
                                config['rating_paused'] = outcome['rating_error']
                                db.execute('UPDATE runs SET config=?,message=? WHERE id=?',
                                           (json.dumps(config), 'Collection will continue; ratings need attention and will wait for a later retry.', queued['id']))
                        if outcome and not outcome['workflow_id'] and outcome['provider_blocked']:
                            provider = json.loads(outcome['config']).get('provider', 'linkedin')
                            for queued in db.execute("SELECT id,config FROM runs WHERE kind='collection' AND status='queued'").fetchall():
                                if json.loads(queued['config']).get('provider', 'linkedin') == provider:
                                    db.execute("UPDATE runs SET status='paused',message=?,finished_at=? WHERE id=?",
                                               (PROVIDERS[provider]+' denied or rate-limited access. Remaining searches on this platform were paused; retry after a break. Other platforms continue.', store.now(), queued['id']))
                    del self.processes[rid]
                else:
                    with store.connect(self.path) as db:
                        row = db.execute('SELECT phase,heartbeat FROM runs WHERE id=?', (rid,)).fetchone()
                    activity = (row['phase'], row['heartbeat'])
                    if activity != getattr(process, 'activity', None):
                        process.activity, process.phase_started = activity, time.monotonic()
                    # A healthy large search/rating can run as long as it is making
                    # progress. Terminate only a stalled worker, not an elapsed budget.
                    if time.monotonic() - process.phase_started > 600:
                        self.stop(rid, 'timed_out', 'Stopped after ten minutes without progress. Saved jobs and ratings are retained.')
                        with store.connect(self.path) as db:
                            row = db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
                            if row['workflow_id']:
                                db.execute("UPDATE runs SET error_kind='temporary' WHERE id=?",(rid,))
                                self.recover_run(db,dict(row)|{'error_kind':'temporary'})
            self.launch_next()

    def recover_run(self, db, row):
        kind = row['error_kind'] or workflows.error_kind(row['rating_error'] or row['message'],row['provider_blocked'])
        # A rating problem never prevents collecting on a different search.
        if row['rating_error'] and workflows.error_kind(row['rating_error'])=='rating_access':
            for queued in db.execute("SELECT id,config FROM runs WHERE workflow_id=? AND status='queued'",(row['workflow_id'],)).fetchall():
                config=json.loads(queued['config']);config['rating_paused']=row['rating_error']
                db.execute('UPDATE runs SET config=? WHERE id=?',(json.dumps(config),queued['id']))
        if row['status'] not in ('failed','timed_out','completed_with_warnings'):
            return
        checkpoint=json.loads(row['checkpoint'])
        progressed=row['kind']=='collection' and 'attempt_start_offset' in checkpoint and (checkpoint.get('offset',0)>checkpoint['attempt_start_offset'] or checkpoint.get('found',0)>checkpoint.get('attempt_start_found',0))
        attempts=1 if progressed else row['attempts']
        if progressed and kind in ('temporary','provider_access'):
            db.execute('UPDATE runs SET attempts=1 WHERE id=?',(row['id'],))
        if kind in ('temporary','provider_access') and attempts < workflows.MAX_ATTEMPTS:
            delay = (120 if kind=='provider_access' else 30) * (4**max(0,attempts-1))
            retry_at=workflows.later(delay)
            db.execute("UPDATE runs SET status='retry_wait',phase='waiting',retry_at=?,message=?,finished_at=NULL WHERE id=?",(retry_at,'Temporary interruption. Saved progress will retry automatically; other platforms can continue.',row['id']))
            if kind=='provider_access':
                provider=json.loads(row['config']).get('provider','linkedin')
                for queued in db.execute("SELECT id,config FROM runs WHERE workflow_id=? AND kind='collection' AND status IN ('queued','retry_wait')",(row['workflow_id'],)).fetchall():
                    if json.loads(queued['config']).get('provider','linkedin')==provider:
                        db.execute("UPDATE runs SET status='retry_wait',phase='waiting',retry_at=?,message=? WHERE id=?",(retry_at,PROVIDERS[provider]+' is cooling down. Other platforms continue; this search will retry automatically.',queued['id']))
        elif row['status']=='failed' or row['rating_error']:
            db.execute("UPDATE runs SET status='needs_attention',error_kind=?,message=? WHERE id=?",(kind,'Automatic recovery stopped. '+row['message'],row['id']))
            if kind=='provider_access':
                provider=json.loads(row['config']).get('provider','linkedin')
                for queued in db.execute("SELECT id,config FROM runs WHERE workflow_id=? AND kind='collection' AND status IN ('queued','retry_wait')",(row['workflow_id'],)).fetchall():
                    if json.loads(queued['config']).get('provider','linkedin')==provider:
                        db.execute("UPDATE runs SET status='needs_attention',error_kind='provider_access',retry_at='',finished_at=?,message=? WHERE id=?",(store.now(),PROVIDERS[provider]+' still denies access after three attempts. Resume unfinished work later; other platforms continue.',queued['id']))

    def launch_next(self):
        if self.processes:
            return
        with store.connect(self.path) as db:
            db.execute("UPDATE runs SET status='queued',phase='queued' WHERE status='retry_wait' AND retry_at<=?",(store.now(),))
            row = db.execute("SELECT id FROM runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return
            rid = row['id']
            step=db.execute('SELECT kind,config,checkpoint FROM runs WHERE id=?',(rid,)).fetchone()
            if step['kind']=='collection':
                checkpoint=json.loads(step['checkpoint'])
                checkpoint.update(attempt_start_offset=checkpoint.get('offset',0),attempt_start_found=checkpoint.get('found',0))
                db.execute('UPDATE runs SET checkpoint=? WHERE id=?',(json.dumps(checkpoint),rid))
            if step['kind']=='availability' and 'availability_ids' not in json.loads(step['config']):
                ids=[r[0] for r in db.execute("SELECT id FROM jobs WHERE duplicate_of='' ORDER BY CASE WHEN stage IN ('saved','applied','interview','offer') THEN 0 ELSE 1 END,created_at DESC")]
                db.execute('UPDATE runs SET config=? WHERE id=?',(json.dumps({'availability_ids':ids}),rid))
            db.execute("UPDATE runs SET status='running',phase='collecting',attempts=attempts+1,provider_blocked=0,error_kind='',rating_error='',retry_at='',finished_at=NULL,heartbeat=?,message='Working from saved progress; ratings run automatically.' WHERE id=?", (store.now(), rid))
        try:
            self.launch(rid)
        except Exception:
            with store.connect(self.path) as db:
                row=db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
                if row['workflow_id']:
                    self.recover_run(db,row)

    def launch(self, rid, phase='collecting'):
        try:
            process = subprocess.Popen([sys.executable, '-m', 'dashboard.worker', '--db', self.path, '--run', str(rid)], cwd=ROOT,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            process.started = process.phase_started = time.monotonic()
            process.phase = phase
            self.processes[rid] = process
            with store.connect(self.path) as db:
                db.execute('UPDATE runs SET worker_pid=? WHERE id=?',(getattr(process,'pid',None),rid))
        except Exception:
            with store.connect(self.path) as db:
                db.execute("UPDATE runs SET status='failed',error_kind='temporary',message='Could not launch background worker.',finished_at=? WHERE id=?", (store.now(), rid))
            raise
        return {'id': rid}

    def start(self, source_id):
        with self.lock:
            self.reap()
            if self.processes:
                raise ValueError('A collection or rating is already running. Wait or stop it before starting another.')
            with store.connect(self.path) as db:
                if db.execute("SELECT 1 FROM runs WHERE status IN ('running','queued','retry_wait')").fetchone():
                    raise ValueError('A workflow is already active, including automatic retries. Stop it first.')
                source = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
                if not source:
                    raise LookupError('Search not found.')
                rid = db.execute("INSERT INTO runs(source_id,name,config,status,message,started_at) VALUES(?,?,?,'queued',?,?)",
                    (source_id, source['name'], source['config'], 'Fetching jobs. Full descriptions can take a few minutes.', store.now())).lastrowid
                wid=db.execute('INSERT INTO workflows(started_at) VALUES(?)',(store.now(),)).lastrowid
                db.execute('UPDATE runs SET workflow_id=? WHERE id=?',(wid,rid))
            self.launch_next()
            return {'id':rid,'workflow_id':wid}

    def start_all(self, data, include_waiting=False):
        with self.lock:
            self.reap()
            if self.processes:
                raise ValueError('A workflow is already running. Wait or stop it first.')
            override = data.get('limit')
            if override is not None and (type(override) is not int or override < 1):
                raise ValueError('The optional per-search limit must be a positive integer.')
            requested = data.get('source_ids')
            if requested is not None and (not isinstance(requested, list) or not requested or
                    any(type(i) is not int for i in requested)):
                raise ValueError('Choose valid source IDs.')
            with store.connect(self.path) as db:
                if db.execute("SELECT 1 FROM runs WHERE status IN ('running','queued','retry_wait')").fetchone():
                    raise ValueError('A workflow is already active, including automatic retries. Stop it before starting another.')
                sources = db.execute('SELECT * FROM sources ORDER BY id').fetchall()
                if requested is not None:
                    if not set(requested) <= {s['id'] for s in sources}:
                        raise LookupError('A selected search was not found.')
                    sources = [s for s in sources if s['id'] in requested]
                waiting = [r['id'] for r in db.execute("SELECT id FROM jobs WHERE rating_status!='complete' AND duplicate_of='' ORDER BY created_at DESC")] if include_waiting else []
                if not sources and not waiting:
                    raise ValueError('Add at least one search.')
                ids = []
                wid=db.execute('INSERT INTO workflows(started_at) VALUES(?)',(store.now(),)).lastrowid
                if waiting:
                    ids.append(db.execute("INSERT INTO runs(name,config,status,kind,phase,message,started_at) VALUES(?,?,'queued','rating','queued',?,?)",
                        ('Existing job ratings', json.dumps({'job_ids': waiting}), 'Existing jobs will be checked automatically before collecting more.', store.now())).lastrowid)
                for source in sources:
                    config = validate_config(json.loads(source['config']))
                    if 'limit' in data:
                        config['limit'] = override
                    ids.append(db.execute("INSERT INTO runs(source_id,name,config,status,phase,message,started_at) VALUES(?,?,?,'queued','queued',?,?)",
                        (source['id'], source['name'], json.dumps(config), 'Waiting for the previous search and ratings to finish.', store.now())).lastrowid)
                for rid in ids:
                    db.execute('UPDATE runs SET workflow_id=? WHERE id=?',(wid,rid))
                for jid in waiting:
                    workflows.remember(db,wid,jid)
                if include_waiting:
                    ids.append(db.execute("INSERT INTO runs(name,config,status,kind,phase,message,started_at,workflow_id) VALUES(?,'{}','queued','availability','queued',?,?,?)",('Posting availability','Checking saved vacancies after collection finishes.',store.now(),wid)).lastrowid)
            self.launch_next()
            return {'ids': ids, 'queued': len(ids), 'workflow_id':wid}

    def start_workflow(self):
        with self.lock:
            self.reap()
            if self.processes:
                # Repeated clicks must not enqueue another scrape or rating.
                return {'active': True, 'ids': list(self.processes)}
            with store.connect(self.path) as db:
                active=db.execute("SELECT workflow_id FROM runs WHERE status IN ('running','queued','retry_wait') LIMIT 1").fetchone()
                if active:
                    return {'active':True,'workflow_id':active[0]}
                duplicates = store.reconcile_duplicates(db)
            result = self.start_all({}, include_waiting=True)
            return result | {'duplicates': duplicates}

    def stop_all(self, status='cancelled', message='Workflow stopped. Saved jobs and ratings are retained.'):
        with self.lock:
            with store.connect(self.path) as db:
                db.execute("UPDATE runs SET status=?,message=?,finished_at=? WHERE status IN ('queued','retry_wait')", (status, message, store.now()))
                if status=='cancelled':
                    db.execute("UPDATE workflows SET status='cancelled',finished_at=? WHERE status='running'",(store.now(),))
            for rid in list(self.processes):
                self.stop(rid, status, message)
            return {'ok': True}

    def resume_workflow(self, workflow_id=None, run_ids=None):
        if workflow_id is not None and (type(workflow_id) is not int or workflow_id<1):
            raise ValueError('Choose a valid workflow.')
        if run_ids is not None and (not isinstance(run_ids,list) or not run_ids or any(type(r) is not int for r in run_ids)):
            raise ValueError('Select valid unfinished workflow steps.')
        with self.lock:
            self.reap()
            with store.connect(self.path) as db:
                if db.execute("SELECT 1 FROM runs WHERE status IN ('running','queued','retry_wait')").fetchone():
                    return {'active':True}
                latest=db.execute('SELECT id FROM workflows WHERE id=?',(workflow_id,)).fetchone() if workflow_id else db.execute('SELECT id FROM workflows ORDER BY id DESC LIMIT 1').fetchone()
                if workflow_id and not latest:
                    raise LookupError('Workflow not found.')
                if latest:
                    wid=latest['id']
                    rows=db.execute("SELECT * FROM runs WHERE workflow_id=? AND (status IN ('failed','paused','interrupted','timed_out','needs_attention','cancelled') OR (status='completed_with_warnings' AND rating_error!=''))",(wid,)).fetchall()
                    if run_ids is not None:
                        if not set(run_ids)<= {r['id'] for r in rows}:
                            raise ValueError('Only unfinished steps in this workflow can be resumed.')
                        rows=[r for r in rows if r['id'] in run_ids]
                    if not rows:
                        raise ValueError('No unfinished steps remain in this workflow.')
                    for row in rows:
                        config=json.loads(row['config']);config.pop('rating_paused',None)
                        db.execute("UPDATE runs SET status='queued',phase='queued',attempts=0,retry_at='',error_kind='',rating_error='',config=?,finished_at=NULL,message='Resuming unfinished work from saved progress.' WHERE id=?",(json.dumps(config),row['id']))
                    recovered_ids=[r['id'] for r in rows]
                    if any(r['kind']=='collection' for r in rows):
                        final=db.execute("SELECT * FROM runs WHERE workflow_id=? AND kind='availability' AND status='completed' ORDER BY id DESC LIMIT 1",(wid,)).fetchone()
                        if final:
                            # The saved checkpoint retains earlier checks; generate a
                            # fresh library snapshot at launch so new jobs are included.
                            db.execute("UPDATE runs SET status='queued',phase='queued',attempts=0,config='{}',finished_at=NULL,message='Checking new positions after recovered searches finish.' WHERE id=?",(final['id'],))
                            recovered_ids.append(final['id'])
                    db.execute("UPDATE workflows SET status='running',finished_at=NULL WHERE id=?",(wid,))
                    result={'workflow_id':wid,'ids':recovered_ids}
                else:
                    # Older versions had no workflow IDs/checkpoints. Recover only
                    # the most recent unsuccessful result for each saved search.
                    sources=[r['source_id'] for r in db.execute("SELECT r.source_id FROM runs r JOIN sources s ON s.id=r.source_id WHERE r.id=(SELECT MAX(id) FROM runs WHERE source_id=r.source_id) AND r.status IN ('failed','paused','interrupted','timed_out')")]
                    result=None
            if result:
                self.launch_next()
                return result
            return self.start_all({'source_ids':sources} if sources else {},include_waiting=True)

    def suspend(self):
        """Closing the server preserves the queue for the next launch."""
        with self.lock:
            for tid in list(self.application_processes):
                self.stop_application(tid,'interrupted','Dashboard closed during preparation. Inspect the existing form before retrying.')
            for rid in list(self.processes):
                self.stop(rid,'interrupted','Dashboard closed. Saved progress will resume when it opens again.')

    def reap_applications(self):
        for tid,process in list(self.application_processes.items()):
            if process.poll() is not None:
                with store.connect(self.path) as db:
                    db.execute("UPDATE application_tasks SET status='needs_attention',message='Application agent exited before finishing. Inspect the browser form before retrying.',updated_at=? WHERE id=? AND status='running'",(store.now(),tid))
                del self.application_processes[tid]
            elif time.monotonic()-process.started>1200:
                self.stop_application(tid,'needs_attention','Preparation reached its 20-minute limit. Inspect the existing form before retrying.')
        if self.application_processes:return
        with store.connect(self.path) as db:
            row=db.execute("SELECT id FROM application_tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:return
            tid=row['id']
            db.execute("UPDATE application_tasks SET status='running',heartbeat=?,updated_at=?,message='Starting review-only Luna application agent…' WHERE id=?",(store.now(),store.now(),tid))
        try:
            process=subprocess.Popen([sys.executable,'-m','dashboard.applications','--db',self.path,'--task',str(tid)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            process.started=time.monotonic();self.application_processes[tid]=process
            with store.connect(self.path) as db:db.execute('UPDATE application_tasks SET worker_pid=? WHERE id=?',(getattr(process,'pid',None),tid))
        except OSError:
            with store.connect(self.path) as db:db.execute("UPDATE application_tasks SET status='needs_attention',message='Could not start the application agent.',updated_at=? WHERE id=?",(store.now(),tid))

    def stop_application(self,tid,status='cancelled',message='Preparation stopped. Inspect the existing browser form before retrying.'):
        with self.lock:
            process=self.application_processes.pop(tid,None)
            if process:
                try:
                    if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
                    elif os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
                    else:process.terminate()
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
                    else:process.kill()
                    process.wait()
                except ProcessLookupError:pass
            with store.connect(self.path) as db:
                db.execute("UPDATE application_tasks SET status=?,message=?,updated_at=? WHERE id=? AND status IN ('running','queued')",(status,message,store.now(),tid))
            return {'ok':True}

    def start_ratings(self):
        with self.lock:
            self.reap()
            if self.processes:
                raise ValueError('A collection or rating is already running. Wait or stop it first.')
            with store.connect(self.path) as db:
                if db.execute("SELECT 1 FROM runs WHERE status IN ('running','queued','retry_wait')").fetchone():
                    raise ValueError('A workflow is already active. Stop it before retrying ratings.')
                ids = [r['id'] for r in db.execute("SELECT id FROM jobs WHERE rating_status!='complete' AND duplicate_of='' ORDER BY created_at DESC")]
                if not ids:
                    raise ValueError('No jobs are waiting for a rating.')
                rid = db.execute("INSERT INTO runs(name,config,status,kind,phase,message,started_at) VALUES(?,?,'queued','rating','rating',?,?)",
                                 ('Waiting job ratings', json.dumps({'job_ids': ids}), 'Checking waiting jobs with Luna…', store.now())).lastrowid
                wid=db.execute('INSERT INTO workflows(started_at) VALUES(?)',(store.now(),)).lastrowid
                db.execute('UPDATE runs SET workflow_id=? WHERE id=?',(wid,rid))
                for jid in ids:
                    workflows.remember(db,wid,jid)
            self.launch_next()
            return {'id':rid,'workflow_id':wid}

    def start_availability(self,data):
        with self.lock:
            self.reap()
            with store.connect(self.path) as db:
                if db.execute("SELECT 1 FROM runs WHERE status IN ('running','queued','retry_wait')").fetchone():
                    raise ValueError('A workflow is already active. Its queued availability step will run after collection.')
                selected=data.get('job_ids')
                if selected is not None and (not isinstance(selected,list) or not selected or any(not isinstance(j,str) for j in selected)):
                    raise ValueError('Choose valid jobs to check.')
                ids=[r[0] for r in db.execute("SELECT id FROM jobs WHERE duplicate_of='' ORDER BY CASE WHEN stage IN ('saved','applied','interview','offer') THEN 0 ELSE 1 END,created_at DESC")]
                if selected is not None:
                    if not set(selected)<=set(ids):raise LookupError('A selected job was not found.')
                    ids=[j for j in ids if j in selected]
                if not ids:raise ValueError('No saved jobs to check.')
                wid=db.execute('INSERT INTO workflows(started_at) VALUES(?)',(store.now(),)).lastrowid
                rid=db.execute("INSERT INTO runs(name,config,status,kind,phase,message,started_at,workflow_id) VALUES(?,?,'queued','availability','queued',?,?,?)",('Posting availability',json.dumps({'availability_ids':ids}),'Checking public posting status. No model tokens are used.',store.now(),wid)).lastrowid
            self.launch_next()
            return {'id':rid,'workflow_id':wid}

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
                db.execute("UPDATE runs SET status=?,message=?,finished_at=? WHERE id=? AND status IN ('running','queued','retry_wait')", (status, message, store.now(), rid))
            return {'ok': True}

    def mutate(self, route, data):
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object.')
        bits = route.strip('/').split('/')
        if route == '/api/workflow/start':
            return self.start_workflow()
        if route == '/api/workflow/resume':
            return self.resume_workflow(data.get('workflow_id'),data.get('run_ids'))
        if route == '/api/availability/run':
            return self.start_availability(data)
        if route == '/api/applications/start':
            if data.get('review_only') is not True:raise ValueError('The application agent only prepares forms for your review.')
            with self.lock:
                result=applications.enqueue(self.path,str(data.get('job_id','')))
                self.reap_applications()
                return result
        if route == '/api/application-cv':
            with self.lock:
                return applications.save_cv(self.path,data.get('pdf'))
        if route == '/api/jobs/interest':
            with self.lock:
                result=store.set_interest(self.path,data.get('job_ids'),data.get('interest'))
                if result['interest']=='not_interested':
                    with store.connect(self.path) as db:
                        tasks=[r['id'] for r in db.execute("SELECT id,job_id FROM application_tasks WHERE status IN ('queued','running')") if r['job_id'] in result['job_ids']]
                    for tid in tasks:
                        self.stop_application(tid,message='Preparation stopped because you marked the position not interested.')
                return result
        if len(bits)==4 and bits[:2]==['api','applications']:
            tid=int(bits[2])
            if bits[3]=='stop':return self.stop_application(tid)
            if bits[3]=='retry':
                with self.lock:
                    result=applications.retry(self.path,tid,data.get('answers',{}));self.reap_applications();return result
            if bits[3]=='confirm-submitted':
                if data.get('submitted_by_me') is not True:raise ValueError('Confirm that you submitted the application yourself.')
                with self.lock:
                    return applications.mark_submitted(self.path,tid)
        if route == '/api/jobs':
            with store.connect(self.path) as db:
                jid, created = store.upsert(db, data, tracking=True)
                job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
                if 'availability' in data:
                    chosen={'ongoing':'open','cancelled':'closed','deleted':'closed','unknown':'unverified'}.get(data['availability'],data['availability'])
                    if chosen not in ('open','closed','unverified'):
                        raise ValueError('Choose open, closed or unverified availability.')
                    if chosen!=(job['posting_status'] or 'unverified'):
                        availability.record(db,jid,chosen,'Manually marked by you.')
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
            allowed = set(rating.PROFILE_FIELDS+applications.PROFILE_FIELDS)
            with store.connect(self.path) as db:
                before = rating.settings_from(db)
                for key in allowed & data.keys():
                    db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, json.dumps(str(data[key])[:100000])))
                if any(before.get(k, '') != str(data[k])[:100000] for k in set(rating.PROFILE_FIELDS) & data.keys()):
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
                imported_ids = {}
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError('Every imported job must be an object.')
                    jid, created = store.upsert(db, row, tracking=True)
                    if row.get('id'):
                        imported_ids[str(row['id'])] = jid
                    added += int(created)
                    for field in ('analysis', 'cover_letter'):
                        if row.get(field) and not row.get('duplicate_of'):
                            db.execute('UPDATE jobs SET '+field+'=? WHERE id=?', (str(row[field]), jid))
                            if field == 'analysis':
                                db.execute("UPDATE jobs SET rating_status='pending',rating_hash='',rating_error='Imported review needs verification against your current profile.' WHERE id=?", (jid,))
                links = data.get('links', [])
                if not isinstance(links, list) or len(links)>20000:
                    raise ValueError('Backup source links must be an array of up to 20,000 links.')
                for link in links:
                    if not isinstance(link, dict) or not isinstance(link.get('source','manual'), str):
                        raise ValueError('Invalid backup source link.')
                    jid = imported_ids.get(str(link.get('job_id') or ''))
                    url = store.canonical_url(link.get('url'))
                    if not jid or not url:
                        raise ValueError('Backup links must identify an imported job and a valid URL.')
                    db.execute('INSERT OR IGNORE INTO job_links(url,job_id,source) VALUES(?,?,?)', (url,jid,str(link.get('source') or 'manual')[:100]))
                store.reconcile_duplicates(db)
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
        elif re.fullmatch(r'/api/applications/\d+/review-image',route):
            try:
                body,mime=applications.review_image(app.path,int(route.split('/')[3]))
                self.reply(200,body,mime)
            except LookupError as error:
                self.reply(404,{'error':str(error)})
        elif route == '/api/export':
            with store.connect(app.path) as db:
                jobs = [dict(r) for r in db.execute('SELECT * FROM jobs')]
                links = [dict(r) for r in db.execute('SELECT * FROM job_links')]
            self.reply(200, {'version': 2, 'exported_at': store.now(), 'jobs': jobs, 'links': links}, disposition='attachment; filename="job-search-backup.json"')
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
    # Prevent a second dashboard from recovering/interrupting the first one's workers.
    lock_file=None
    if os.name=='posix':
        import fcntl
        Path(args.db).resolve().parent.mkdir(parents=True,exist_ok=True)
        lock_file=open(str(Path(args.db).resolve())+'.lock','a')
        try:
            fcntl.flock(lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1,'This workspace is already running. Open its existing dashboard.\n')
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
        app.suspend()
        server.server_close()
        if lock_file:
            lock_file.close()

if __name__ == '__main__':
    main()
