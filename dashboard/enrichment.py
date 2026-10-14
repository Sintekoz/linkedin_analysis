"""Durable description recovery, separate from discovery and model calls."""
import json
import re
from datetime import datetime,timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit,quote
from . import store, network, availability, ats, workflows
from .locations import classify_location

def enqueue(db,jid,retry=False):
    job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not job or job['duplicate_of'] or job['interest']=='not_interested':return
    if job['description'].strip():
        db.execute("UPDATE enrichment_tasks SET status='complete',message='Description already saved.',updated_at=? WHERE job_id=?",(store.now(),jid))
        return
    db.execute("UPDATE jobs SET rating_status='missing_description',rating_error='Description recovery is queued; rating will follow automatically.' WHERE id=?",(jid,))
    db.execute('INSERT OR IGNORE INTO enrichment_tasks(job_id,updated_at) VALUES(?,?)',(jid,store.now()))
    db.execute("UPDATE enrichment_tasks SET status='pending',attempts=0,next_attempt_at='',message='Description is missing again; recovery queued.',updated_at=? WHERE job_id=? AND status='complete'",(store.now(),jid))
    if retry:
        db.execute("UPDATE enrichment_tasks SET status='pending',attempts=0,next_attempt_at='',message='Retrying description recovery.',updated_at=? WHERE job_id=? AND status IN ('needs_attention','unsupported')",(store.now(),jid))

class DetailHTML(HTMLParser):
    """Read only the main LinkedIn description, never footer/recommendations."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth=0;self.description_depth=None;self.parts=[];self.h1_depth=None;self.title=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('br','hr','img','meta','link','input','source','wbr'):
            if tag=='br' and self.description_depth:self.parts.append('\n')
            return
        self.depth+=1;attrs=dict(attrs)
        if tag=='h1':self.h1_depth=self.depth
        if 'show-more-less-html__markup' in attrs.get('class','').split():self.description_depth=self.depth
        if self.description_depth and tag in ('p','div','li','h2','h3'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if self.description_depth==self.depth:self.description_depth=None
        if self.h1_depth==self.depth:self.h1_depth=None
        self.depth=max(0,self.depth-1)
    def handle_data(self,value):
        if self.description_depth:self.parts.append(value)
        if self.h1_depth:self.title.append(value)

def page_description(url,final,body,title):
    if store.canonical_url(url)!=store.canonical_url(final):return ''
    parser=availability.PostingHTML();parser.feed(body)
    for script in parser.json_scripts:
        try:payload=json.loads(script)
        except ValueError:continue
        for node in availability.nodes(payload):
            kinds=node.get('@type');kinds=kinds if isinstance(kinds,list) else [kinds]
            if 'JobPosting' not in kinds:continue
            target=node.get('url') or node.get('mainEntityOfPage')
            if isinstance(target,dict):target=target.get('@id')
            try:same=bool(target) and store.canonical_url(target)==store.canonical_url(url)
            except (ValueError,TypeError):same=False
            same_title=re.sub(r'\s+',' ',str(node.get('title','')).strip()).casefold()==re.sub(r'\s+',' ',title.strip()).casefold()
            if same or not target and same_title:
                return ats.text_html(node.get('description'))
    if (urlsplit(url).hostname or '').endswith('linkedin.com'):
        detail=DetailHTML();detail.feed(body)
        if re.sub(r'\s+',' ',''.join(detail.title).strip()).casefold()==re.sub(r'\s+',' ',title.strip()).casefold():return ''.join(detail.parts).strip()
    return ''

def employer_description(identity,company):
    provider,board,jid,region=identity
    config={'board':board,'company':company}
    if provider=='greenhouse':
        url='https://boards-api.greenhouse.io/v1/boards/'+quote(board,safe='')+'/jobs?content=true'
        payload=ats.read_json(url)
        if not isinstance(payload,dict) or not isinstance(payload.get('jobs'),list):raise ValueError('Unexpected employer feed format.')
        for row in payload['jobs']:
            if str(row.get('id'))==jid:return ats.greenhouse_job(row,config),url
    else:
        host='api.eu.lever.co' if region=='eu' else 'api.lever.co';skip=0;seen=set()
        while True:
            url='https://'+host+'/v0/postings/'+quote(board,safe='')+'?mode=json&limit=100&skip='+str(skip)
            rows=ats.read_json(url)
            if not isinstance(rows,list):raise ValueError('Unexpected employer feed format.')
            for row in rows:
                if str(row.get('id'))==jid:return ats.lever_job(row,config),url
            page={str(r.get('id')) for r in rows}
            if len(rows)<100:break
            if page<=seen:raise ValueError('Employer feed repeated a page.')
            seen.update(page);skip+=100
    return {},url

def retrieve(job,links):
    urls=list(dict.fromkeys([job['direct_url'],*links,job['url']]))
    urls=[u for u in urls if u]
    urls.sort(key=lambda u:0 if availability.ats_identity(u) else 1 if u==job['direct_url'] else 2)
    errors=[];retry_at='';supported=False;attempted=False
    for url in urls:
        try:
            network.allowed_url(url);supported=True
            identity=availability.ats_identity(url)
            if identity:
                payload,source=employer_description(identity,job['company'])
                attempted=True
                if payload.get('description','').strip():return payload,source
            else:
                code,final,body=network.fetch(url,max_bytes=3_000_000)
                attempted=True
                if code!=200:raise ValueError('Description page returned HTTP '+str(code)+'.')
                redirected=availability.ats_identity(final)
                if redirected:
                    payload,source=employer_description(redirected,job['company'])
                    if payload.get('description','').strip():return payload,source
                description=page_description(url,final,body,job['title'])
                if description.strip():return {'description':description},final
            errors.append('No full description for this exact posting at '+urlsplit(url).hostname+'.')
        except network.AccessBlocked as error:
            errors.append(str(error));retry_at=max(retry_at,error.retry_at)
            attempted=attempted or error.attempted
        except ValueError as error:errors.append(str(error))
        except Exception as error:errors.append(str(error)[:200])
    if not supported:raise ValueError('No supported exact posting link is available for description recovery.')
    raise network.AccessBlocked(' '.join(dict.fromkeys(errors))[:800],retry_at,attempted)

def save_description(db,job,payload,source):
    data={'id':job['id'],'title':job['title'],'url':job['url'],**payload}
    data.update(id=job['id'],url=job['url'],source=job['source'])
    jid,_=store.upsert(db,data)
    current=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    evidence=classify_location(dict(current))
    db.execute('UPDATE jobs SET location_status=?,location_note=? WHERE id=?',(*evidence.values(),jid))
    db.execute('UPDATE jobs SET description_source=?,description_fetched_at=? WHERE id=?',(source,store.now(),jid))
    db.execute("UPDATE enrichment_tasks SET status='complete',message='Full description recovered.',source_url=?,updated_at=? WHERE job_id=?",(source,store.now(),job['id']))
    return jid

def reuse_cached(db,job,links):
    """A status check may have just fetched changed details. Reuse, never refetch."""
    urls=list(dict.fromkeys([job['direct_url'],job['url'],*links]));urls=[u for u in urls if u]
    urls.sort(key=lambda u:0 if availability.ats_identity(u) else 1)
    for url in urls:
        identity=availability.ats_identity(url)
        if identity:
            provider,board,jid,region=identity
            prefix=('https://boards-api.greenhouse.io/v1/boards/'+quote(board,safe='')+'/jobs?content=true') if provider=='greenhouse' else (
                'https://'+('api.eu.lever.co' if region=='eu' else 'api.lever.co')+'/v0/postings/'+quote(board,safe='')+'?mode=json&limit=100&skip=')
            rows=db.execute('SELECT * FROM response_cache WHERE url=?',(prefix,)).fetchall() if provider=='greenhouse' else db.execute('SELECT * FROM response_cache WHERE substr(url,1,?)=?',(len(prefix),prefix)).fetchall()
        else:rows=db.execute('SELECT * FROM response_cache WHERE url=?',(url,)).fetchall()
        for cached in rows:
            if cached['status']!=200 or (datetime.now(timezone.utc)-datetime.fromisoformat(cached['fetched_at'])).total_seconds()>900:continue
            if identity and cached['final_url']!=cached['url']:continue
            try:
                if identity:
                    value=json.loads(cached['body']);postings=value.get('jobs',[]) if provider=='greenhouse' else value
                    row=next((r for r in postings if str(r.get('id'))==jid),None)
                    if not row:continue
                    config={'board':board,'company':job['company']}
                    payload=ats.greenhouse_job(row,config) if provider=='greenhouse' else ats.lever_job(row,config)
                else:payload={'description':page_description(url,cached['final_url'],cached['body'],job['title'])}
                description=payload.get('description','').strip()
                if not description:continue
                if description!=job['description'].strip():save_description(db,job,payload,cached['url'])
                else:enqueue(db,job['id'])
                return True
            except (ValueError,TypeError,KeyError):continue
    return False

def run_batch(path,run_id,batch_size=20):
    with store.connect(path) as db:
        run=dict(db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone())
        tasks=db.execute('''SELECT j.* FROM enrichment_tasks e JOIN jobs j ON j.id=e.job_id
            JOIN workflow_jobs w ON w.job_id=j.id WHERE w.workflow_id=? AND j.duplicate_of=''
            AND j.interest='interested' AND e.status IN ('pending','retry_wait') AND e.next_attempt_at<=?
            ORDER BY CASE WHEN j.stage IN ('saved','applied','interview','offer') THEN 0 ELSE 1 END,e.attempts,e.updated_at,j.created_at LIMIT ?''',(run['workflow_id'],store.now(),batch_size)).fetchall()
    from . import details
    structured=details.glassdoor(path,[dict(r) for r in tasks])
    recovered=0
    for row in tasks:
        job=dict(row)
        with store.connect(path) as db:
            if db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()[0]!='running':return
            current=db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if current['description'].strip():enqueue(db,job['id']);continue
            links=[r[0] for r in db.execute('SELECT url FROM job_links WHERE job_id=?',(job['id'],))]
            db.execute("UPDATE runs SET phase='enriching',message=?,heartbeat=? WHERE id=?",('Recovering descriptions; discovery and ratings have independent progress.',store.now(),run_id))
        try:
            native=structured.get(job['id'])
            if isinstance(native,Exception):raise native
            if native:
                payload={'description':native['description']};source=native['source_url']
            else:payload,source=retrieve(job,links)
            with store.connect(path) as db:
                if db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()[0]!='running':return
                current=db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
                if current['description'].strip():enqueue(db,job['id']);continue
                jid=save_description(db,job,payload,source)
                from . import employers
                employers.discover(db,payload,run['workflow_id'])
                db.execute('INSERT OR IGNORE INTO run_jobs VALUES(?,?)',(run_id,jid))
                recovered+=1
        except Exception as error:
            with store.connect(path) as db:
                task=db.execute('SELECT attempts FROM enrichment_tasks WHERE job_id=?',(job['id'],)).fetchone()
                # Cooldown skips are not attempts at the remote endpoint.
                skipped=isinstance(error,network.AccessBlocked) and not error.attempted
                attempts=task['attempts']+int(not skipped)
                retry_at=getattr(error,'retry_at','') or workflows.later(120*4**min(2,max(0,attempts-1)))
                unsupported='No supported exact posting link' in str(error)
                endpoints=[]
                if getattr(error,'endpoint',None):endpoints.append(network.health(path,error.endpoint))
                for url in [job['url'],job['direct_url'],*links]:
                    if not url:continue
                    try:network.allowed_url(url)
                    except ValueError:continue
                    identity=availability.ats_identity(url)
                    key=('boards-api.greenhouse.io:feed' if identity[0]=='greenhouse' else ('api.eu.lever.co:feed' if identity[3]=='eu' else 'api.lever.co:feed')) if identity else network.endpoint(url)
                    if not getattr(error,'endpoint',None):endpoints.append(network.health(path,key))
                exhausted=bool(endpoints) and all(s and s['failures']>=3 for s in endpoints)
                state='unsupported' if unsupported else 'needs_attention' if attempts>=3 or exhausted else 'retry_wait'
                db.execute('UPDATE enrichment_tasks SET status=?,attempts=?,next_attempt_at=?,message=?,updated_at=? WHERE job_id=?',(state,attempts,retry_at,str(error)[:800],store.now(),job['id']))
    with store.connect(path) as db:
        db.execute("UPDATE runs SET found=found+?,scanned=scanned+?,status='yielded',phase='queued',finished_at=?,message=? WHERE id=? AND status='running'",(recovered,len(tasks),store.now(),f'{recovered} descriptions recovered in this pass. Ratings are queued separately.',run_id))
