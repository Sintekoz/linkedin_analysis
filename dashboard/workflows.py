"""Durable workflow summaries and bounded, provider-specific recovery."""
import json
import re
from datetime import datetime, timedelta, timezone
from . import store

ACTIVE = ('running', 'queued', 'retry_wait','idle')
RECOVERABLE = ('failed', 'paused', 'interrupted', 'timed_out', 'needs_attention')
MAX_ATTEMPTS = 3


def error_kind(message, blocked=False):
    text = message.lower()
    if any(s in text for s in ('subscription limit', 'allowance resets', 'chatgpt login',
                                'sign in', 'authentication', 'luna is unavailable', 'install codex', 'add your cv', 'usage limit', 'quota')):
        return 'rating_access'
    if blocked or re.search(r'\b(?:403|429)\b',text) or any(s in text for s in ('rate-limited','denied access')):
        return 'provider_access'
    if any(s in text for s in ('timed out', 'timeout', 'connection', 'unexpectedly', 'without progress',
                                'temporarily', '502', '503', '504', 'rating failed')):
        return 'temporary'
    return 'configuration'


def later(seconds):
    return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='seconds')


def remember(db, workflow_id, jid, created=False, collected=False):
    if not workflow_id:
        return
    row = db.execute('SELECT rating_hash,rating_status FROM jobs WHERE id=?', (jid,)).fetchone()
    db.execute('''INSERT INTO workflow_jobs(workflow_id,job_id,created,initial_hash,collected) VALUES(?,?,?,?,?)
        ON CONFLICT(workflow_id,job_id) DO UPDATE SET collected=MAX(workflow_jobs.collected,excluded.collected)''',
               (workflow_id, jid, int(created), row['rating_hash'] if row['rating_status']=='complete' else '',int(collected)))


def summaries(db):
    result = []
    for workflow in db.execute("SELECT * FROM workflows ORDER BY CASE WHEN EXISTS(SELECT 1 FROM runs WHERE workflow_id=workflows.id AND status IN ('running','queued','retry_wait')) THEN 0 ELSE 1 END,id DESC LIMIT 20").fetchall():
        rows = [dict(r) for r in db.execute('SELECT * FROM runs WHERE workflow_id=? ORDER BY id', (workflow['id'],))]
        counts = {'total': len(rows), 'finished': 0, 'running': 0, 'queued': 0, 'retrying': 0, 'attention': 0, 'stopped':0}
        for row in rows:
            status = row['status']
            key = 'running' if status=='running' else 'queued' if status in ('queued','idle') else 'retrying' if status=='retry_wait' else 'attention' if status in RECOVERABLE else 'stopped' if status=='cancelled' else 'finished'
            counts[key] += 1
        jobs = db.execute('''SELECT j.id,j.description,j.analysis,j.rating_status,j.rating_hash,j.location_status,j.interest,j.posting_status,w.created,w.initial_hash,w.collected
            FROM workflow_jobs w JOIN jobs j ON j.id=w.job_id WHERE w.workflow_id=? AND j.duplicate_of='' ''', (workflow['id'],)).fetchall()
        complete = [j for j in jobs if j['rating_status']=='complete']
        active = counts['running']+counts['queued']+counts['retrying']
        status = 'stopped' if workflow['status'] in ('cancelled','stopped') else 'running' if active else 'needs_attention' if counts['attention'] or any(j['rating_status'] not in ('complete','missing_description') for j in jobs) else 'completed'
        # Missing descriptions are a data limitation, surfaced separately from failures.
        finished_at = None if active else (workflow['finished_at'] or store.now())
        db.execute('UPDATE workflows SET status=?,finished_at=? WHERE id=?', (status, finished_at, workflow['id']))
        providers = {}
        provider_issues = {}
        for row in rows:
            provider = json.loads(row['config']).get('provider', row['kind'] if row['kind'] in ('rating','availability','enrichment') else 'linkedin')
            item = providers.setdefault(provider, {'total':0,'finished':0,'attention':0,'retrying':0})
            item['total'] += 1
            item['finished'] += int(row['status'] not in ACTIVE and row['status'] not in RECOVERABLE and row['status']!='cancelled')
            item['attention'] += int(row['status'] in RECOVERABLE)
            item['retrying'] += int(row['status']=='retry_wait')
            if row['kind']=='collection' and row['error_kind']=='provider_access' and row['status'] in RECOVERABLE:
                issue=provider_issues.setdefault(provider,{'affected':0,'attempted':0,'not_started':0})
                issue['affected']+=1
                attempted=row['dispatches']>0 or row['attempts']>0 or row['pages']>0
                issue['attempted']+=int(attempted)
                issue['not_started']+=int(not attempted)
        checked=[r[0] for r in db.execute("SELECT j.posting_status FROM jobs j WHERE j.id IN (SELECT job_id FROM run_jobs WHERE run_id IN (SELECT id FROM runs WHERE workflow_id=? AND kind='availability'))",(workflow['id'],))]
        enriched=sum(bool(j['description'].strip()) for j in jobs)
        searches=[r for r in rows if r['kind']=='collection']
        tasks=db.execute('''SELECT e.status,COUNT(*) n FROM enrichment_tasks e JOIN workflow_jobs w ON w.job_id=e.job_id
            JOIN jobs j ON j.id=e.job_id WHERE w.workflow_id=? AND j.duplicate_of='' AND j.interest='interested'
            AND TRIM(j.description)='' GROUP BY e.status''',(workflow['id'],)).fetchall()
        requests={r['outcome']:r['n'] for r in db.execute('''SELECT outcome,COUNT(*) n FROM request_events WHERE run_id IN
            (SELECT id FROM runs WHERE workflow_id=?) GROUP BY outcome''',(workflow['id'],))}
        attempts=sum(requests.get(k,0) for k in ('fetched','revalidated','failed','blocked','search_request'))
        successes=sum(requests.get(k,0) for k in ('fetched','revalidated','search_success'))
        elapsed=max(1,(datetime.fromisoformat(finished_at or store.now())-datetime.fromisoformat(workflow['started_at'])).total_seconds())
        def strong(j):
            try:return json.loads(j['analysis']).get('score',0)>=7
            except (ValueError,TypeError,AttributeError):return False
        usable=sum(j['collected'] and j['rating_status']=='complete' and strong(j) and j['location_status']=='ireland' and j['interest']=='interested' and j['posting_status']!='closed' for j in jobs)
        result.append(dict(workflow) | {'status':status,'finished_at':finished_at, **counts,
            'availability_only':bool(rows) and all(r['kind']=='availability' for r in rows),
            'availability_counts':{'checked':len(checked),'open':checked.count('open'),'closed':checked.count('closed'),'unverified':sum(s not in ('open','closed') for s in checked)},
            'searches': sum(r['kind']=='collection' for r in rows),
            'unique_jobs':len(jobs), 'added':sum(j['created'] for j in jobs),
            'refreshed':sum(not j['created'] and j['collected'] for j in jobs), 'ready':len(complete),
            'reused':sum(bool(j['initial_hash']) and j['initial_hash']==j['rating_hash'] for j in complete),
            'waiting':sum(j['rating_status'] not in ('complete','missing_description') for j in jobs),
            'missing_description':sum(j['rating_status']=='missing_description' for j in jobs),
            'percent':round(100*(counts['finished']+counts['attention'])/len(rows)) if rows else 0,
            'current':next((r for r in rows if r['status']=='running'), None),
            'retry_at':min((r['retry_at'] for r in rows if r['status']=='retry_wait'), default=''),
            'providers':providers,'provider_issues':provider_issues})
        result[-1]['pipeline']={'found':sum(j['collected'] for j in jobs),'descriptions':enriched,'rated':len(complete),
            'searches_started':sum(r['dispatches']>0 or r['attempts']>0 or r['pages']>0 for r in searches),'searches_total':len(searches),
            'description_percent':round(100*enriched/len(jobs)) if jobs else 0,'pending_descriptions':dict((r['status'],r['n']) for r in tasks),
            'requests':requests,'request_success_percent':round(100*successes/attempts) if attempts else None,
            'usable':usable,'usable_per_hour':round(3600*usable/elapsed,1)}
    return result
