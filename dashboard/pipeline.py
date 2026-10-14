"""Independent, fair discovery / enrichment / rating steps in one durable workflow."""
import json
from . import store, rating, enrichment, workflows, network

def rating_ids(db,wid,rid=None):
    settings=rating.settings_from(db)
    ids=[]
    for row in db.execute("SELECT j.* FROM jobs j JOIN workflow_jobs w ON w.job_id=j.id WHERE w.workflow_id=? AND j.duplicate_of='' AND j.interest='interested' AND j.posting_status!='closed' AND j.posting_last_confirmed!='closed' AND TRIM(j.description)!=''",(wid,)):
        if not row['description'].strip():continue
        if row['rating_status']=='complete' and row['rating_hash']==rating.fingerprint(dict(row),settings):
            try:rating.valid_review(json.loads(row['analysis']));continue
            except (ValueError,TypeError):pass
        ids.append(row['id'])
    return ids

def rate_batch(path,rid):
    with store.connect(path) as db:
        row=dict(db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())
        ids=rating_ids(db,row['workflow_id'],rid)[:rating.BATCH_SIZE*2]
    counts=rating.rate_jobs(path,ids,rid,defer_reason=json.loads(row['config']).get('rating_paused',''),initial_counts={k:row[k] for k in ('rated','cached','unrated')})
    with store.connect(path) as db:
        if db.execute('SELECT status FROM runs WHERE id=?',(rid,)).fetchone()[0]!='running':return
        error=''
        for jid in ids:
            job=db.execute('SELECT rating_status,rating_error FROM jobs WHERE id=?',(jid,)).fetchone()
            if job['rating_status']=='complete':db.execute('INSERT OR IGNORE INTO run_jobs VALUES(?,?)',(rid,jid))
            elif job['rating_status'] in ('failed','blocked'):error=error or job['rating_error']
        # rate_jobs reports per-pass counters; retain overall totals across passes.
        status='completed_with_warnings' if error else 'yielded'
        db.execute("UPDATE runs SET status=?,rated=?,cached=?,unrated=?,rating_error=?,error_kind=?,phase='queued',message=?,finished_at=? WHERE id=?",
            (status,row['rated']+counts['rated'],row['cached']+counts['cached'],row['unrated']+counts['unrated'],error,
             workflows.error_kind(error) if error else '',f"{counts['rated']} fresh ratings saved; unchanged reviews are reused.",store.now(),rid))

def refresh(db,wid):
    """Wake independent queues only when there is work; don't spin empty workers."""
    steps=db.execute('SELECT * FROM runs WHERE workflow_id=?',(wid,)).fetchall()
    collection_active=any(r['kind']=='collection' and r['status'] in workflows.ACTIVE for r in steps)
    for row in steps:
        if not json.loads(row['config']).get('_pipeline'):continue
        if row['kind']=='enrichment':
            pending=db.execute("""SELECT e.* FROM enrichment_tasks e JOIN workflow_jobs w ON w.job_id=e.job_id
                JOIN jobs j ON j.id=e.job_id WHERE w.workflow_id=? AND j.duplicate_of='' AND j.interest='interested'
                AND TRIM(j.description)='' AND e.status IN ('pending','retry_wait')""",(wid,)).fetchall()
            due=any(r['next_attempt_at']<=store.now() for r in pending)
            attention=db.execute("""SELECT COUNT(*) FROM enrichment_tasks e JOIN workflow_jobs w ON w.job_id=e.job_id
                JOIN jobs j ON j.id=e.job_id WHERE w.workflow_id=? AND j.duplicate_of='' AND j.interest='interested'
                AND TRIM(j.description)='' AND e.status IN ('needs_attention','unsupported')""",(wid,)).fetchone()[0]
            state='queued' if due else 'retry_wait' if pending else 'idle' if collection_active else 'needs_attention' if attention else 'completed'
            retry_at=min((r['next_attempt_at'] for r in pending),default='')
        elif row['kind']=='rating':
            pending=rating_ids(db,wid,row['id'])
            enrichment_active=any(r['kind']=='enrichment' and r['status'] in (*workflows.ACTIVE,'idle') for r in steps)
            state='queued' if pending else 'idle' if collection_active or enrichment_active else 'completed'
            retry_at=''
        else:continue
        if row['status'] in ('running',*workflows.RECOVERABLE,'cancelled'):continue
        if row['status']=='completed_with_warnings' and row['rating_error']:continue
        db.execute('UPDATE runs SET status=?,retry_at=?,finished_at=?,phase=? WHERE id=?',
                   (state,retry_at,store.now() if state in ('completed','needs_attention') else None,'finished' if state in ('completed','needs_attention') else 'queued',row['id']))
        if state=='needs_attention' and row['kind']=='enrichment':
            db.execute("UPDATE runs SET error_kind='enrichment_access',message=? WHERE id=?",(f'{attention} descriptions could not be recovered after retries or have unsupported links. Saved positions remain available.',row['id']))

def add_steps(db,wid):
    ids=[]
    for kind,name in [('enrichment','Description recovery'),('rating','Automatic fit ratings')]:
        ids.append(db.execute("INSERT INTO runs(name,config,status,kind,phase,message,started_at,workflow_id) VALUES(?,?,'idle',?,'queued',?,?,?)",
                   (name,json.dumps({'_pipeline':2}),kind,'Independent queue; saved progress is reused.',store.now(),wid)).lastrowid)
    return ids

def search_key(config):
    provider=config.get('provider','linkedin')
    return provider+':search' if provider not in ('greenhouse','lever') else (
        'boards-api.greenhouse.io:feed' if provider=='greenhouse' else ('api.eu.lever.co:feed' if config.get('region')=='eu' else 'api.lever.co:feed'))

def delay_search(path,config):
    # Search endpoints and detail endpoints have separate health. All queries on
    # one search endpoint still respect the same cooldown, including restarts.
    network.defer(path,search_key(config),30 if config.get('provider','linkedin')=='linkedin' else 5)
