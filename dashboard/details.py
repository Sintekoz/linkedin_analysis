"""Optional JobSpy structured descriptions, requested only after deduplication.

Glassdoor's installed adapter supports a batch of exact IDs in one detail query.
Keep this version-dependent integration isolated; public pages are a fallback.
"""
import json
import re
import time
from importlib import import_module
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs
from . import store,network

KEY='glassdoor:structured_detail'

def native_id(url):
    p=urlsplit(url or '');host=p.hostname or ''
    if not any(host==d or host.endswith('.'+d) for d in ('glassdoor.com','glassdoor.ie')):return None
    value=(parse_qs(p.query).get('jobListingId') or parse_qs(p.query).get('jl') or [''])[0]
    return 'gd-'+value if re.fullmatch(r'[0-9]{1,20}',value) else None

def blocked(reason,retry_at,attempted=True):
    error=network.AccessBlocked(reason,retry_at,attempted);error.endpoint=KEY
    return error

def glassdoor(path,jobs):
    result={};pending={};urls={}
    with store.connect(path) as db:
        # Older running dashboards can adopt the new optional adapter without
        # interrupting their active worker; normal initialization creates it too.
        db.execute('''CREATE TABLE IF NOT EXISTS detail_cache(provider TEXT NOT NULL,native_id TEXT NOT NULL,
            payload TEXT NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(provider,native_id))''')
        for job in jobs:
            links=[job['url'],job['direct_url']]+[r[0] for r in db.execute('SELECT url FROM job_links WHERE job_id=?',(job['id'],))]
            from .availability import ats_identity
            if any(ats_identity(u) for u in links if u):continue # employer first
            url=next((u for u in links if native_id(u)),None)
            if not url:continue
            native=native_id(url);urls[job['id']]=url
            cached=db.execute("SELECT * FROM detail_cache WHERE provider='glassdoor' AND native_id=?",(native,)).fetchone()
            if cached and (datetime.now(timezone.utc)-datetime.fromisoformat(cached['fetched_at'])).total_seconds()<900:
                result[job['id']]=json.loads(cached['payload'])
            else:pending[job['id']]=native
    for jid in result:network.event('cache_hit',KEY)
    if not pending:return result
    state=network.health(path,KEY)
    if state and state['retry_at']>store.now():
        pause=(datetime.fromisoformat(state['retry_at'])-datetime.now(timezone.utc)).total_seconds()
        if not state['reason'] and pause<=3:time.sleep(max(0,pause))
        else:
            for jid in pending:result[jid]=blocked(state['reason'],state['retry_at'],False);network.event('cooldown_skip',KEY)
            return result
    try:
        gd=import_module('jobspy.glassdoor')
        from jobspy.model import ScraperInput,Country,DescriptionFormat
        if not hasattr(gd.Glassdoor,'_fetch_details') or not hasattr(gd.Glassdoor,'_graph'):return result
        adapter=gd.Glassdoor()
        adapter.scraper_input=ScraperInput(country=Country.IRELAND,location='Ireland',search_term='',description_format=DescriptionFormat.MARKDOWN)
        adapter.base_url=Country.IRELAND.get_glassdoor_url()
        network.allowed_url(adapter.base_url)
        adapter.session=gd.create_session()
        adapter.session.headers.update(gd.headers)
    except (ImportError,AttributeError):return result
    original=adapter._graph;last_error=None
    def request(call,*args,reset_health=True,**kwargs):
        nonlocal last_error
        if last_error:raise last_error
        network.defer(path,KEY,3)
        try:response=call(*args,**kwargs)
        except Exception as error:
            network.event('failed',KEY)
            last_error=blocked('Structured description request failed: '+str(error)[:200],network.defer(path,KEY,120,str(error),True))
            raise
        if response.status_code in (401,403,429,999):
            network.event('blocked',KEY)
            reason='Glassdoor structured descriptions denied access (HTTP '+str(response.status_code)+').'
            last_error=blocked(reason,network.defer(path,KEY,network.retry_seconds(response.headers.get('Retry-After')),reason,True))
        elif response.status_code!=200:
            network.event('failed',KEY)
            reason='Glassdoor structured descriptions returned HTTP '+str(response.status_code)+'.'
            last_error=blocked(reason,network.defer(path,KEY,120,reason,True))
        else:
            if reset_health:network.success(path,KEY)
            network.event('fetched',KEY)
        return response
    def graph(operation,query,variables):return request(original,operation,query,variables)
    adapter._graph=graph
    try:
        # Mirror the library's normal anonymous session initialization. No login
        # cookies are loaded or persisted. A denied initialization stops here.
        if hasattr(adapter,'_search_variables') and hasattr(adapter.session,'get'):
            original_get=adapter.session.get
            adapter.session.get=lambda *args,**kwargs:request(original_get,*args,reset_health=False,**kwargs)
            try:adapter._search_variables()
            finally:adapter.session.get=original_get
        # run_batch uses at most 20 jobs; chunk explicitly to keep requests paced
        # if another caller supplies a larger batch.
        values={};natives=list(dict.fromkeys(pending.values()))
        for start in range(0,len(natives),25):
            if last_error:break
            if start:time.sleep(3)
            values.update(adapter._fetch_details(natives[start:start+25]))
        with store.connect(path) as db:
            for jid,native in pending.items():
                payload=values.get(native,{})
                if isinstance(payload.get('description'),str) and payload['description'].strip():
                    value={'description':payload['description'],'source_url':urls[jid]}
                    db.execute("INSERT INTO detail_cache VALUES('glassdoor',?,?,?) ON CONFLICT(provider,native_id) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at",(native,json.dumps(value),store.now()))
                    result[jid]=value
                else:result[jid]=last_error or ValueError('Glassdoor returned no full description for this exact posting.')
    finally:
        adapter._graph=original
        if hasattr(adapter.session,'close'):adapter.session.close()
    return result
