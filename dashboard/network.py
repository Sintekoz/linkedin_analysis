"""Shared, bounded public GET cache and durable endpoint health. No credentials."""
import contextvars
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from . import store

DOMAINS = ('linkedin.com','indeed.com','glassdoor.com','glassdoor.ie')
EXACT = {'boards.greenhouse.io','job-boards.greenhouse.io','boards-api.greenhouse.io',
         'jobs.lever.co','jobs.eu.lever.co','api.lever.co','api.eu.lever.co','grnh.se'}
_context = contextvars.ContextVar('public_fetch_context', default=(None,None))

class AccessBlocked(ValueError):
    def __init__(self, message, retry_at='', attempted=True):
        super().__init__(message)
        self.retry_at=retry_at
        self.attempted=attempted

def allowed_url(url):
    p=urlsplit(url);host=(p.hostname or '').lower()
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not (
            host in EXACT or any(host==d or host.endswith('.'+d) for d in DOMAINS)):
        raise ValueError('This employer platform is not supported for automatic retrieval yet.')
    return url

def endpoint(url):
    host=urlsplit(url).hostname or ''
    return next((d.split('.')[0]+':detail' for d in DOMAINS if host==d or host.endswith('.'+d)),host+':feed')

@contextmanager
def session(path, run_id=None):
    token=_context.set((str(path),run_id))
    try: yield
    finally: _context.reset(token)

def event(kind, key, path=None, run_id=None):
    active_path,active_run=_context.get();path=path or active_path
    if not path:return
    with store.connect(path) as db:
        db.execute('INSERT INTO request_events(run_id,endpoint,outcome,created_at) VALUES(?,?,?,?)',
                   (run_id if run_id is not None else active_run,key,kind,store.now()))

def health(path,key):
    if not path:return None
    with store.connect(path) as db:
        row=db.execute('SELECT * FROM endpoint_health WHERE endpoint=?',(key,)).fetchone()
    return dict(row) if row else None

def defer(path,key,seconds,reason='',failure=False):
    if not path:return ''
    with store.connect(path) as db:
        previous=db.execute('SELECT * FROM endpoint_health WHERE endpoint=?',(key,)).fetchone()
        failures=(previous['failures'] if previous else 0)+int(failure)
        if failure:seconds=max(seconds,min(1800,120*4**min(2,max(0,failures-1))))
        retry_at=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='seconds')
        if previous and previous['retry_at']>retry_at:retry_at=previous['retry_at']
        db.execute('''INSERT INTO endpoint_health VALUES(?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET
            retry_at=excluded.retry_at,failures=excluded.failures,reason=excluded.reason,updated_at=excluded.updated_at''',
                   (key,retry_at,failures,reason,store.now()))
    return retry_at

def success(path,key):
    if path:
        with store.connect(path) as db:db.execute('UPDATE endpoint_health SET failures=0 WHERE endpoint=?',(key,))

def retry_seconds(value):
    try:return max(0,float(value))
    except (ValueError,TypeError):
        try:return max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError,TypeError,OverflowError):return 0

class RedirectGuard(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        allowed_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url,ttl=900,max_bytes=10_000_000):
    allowed_url(url)
    path,_=_context.get();key=endpoint(url);cached=None
    if path:
        with store.connect(path) as db:
            cached=db.execute('SELECT * FROM response_cache WHERE url=?',(url,)).fetchone()
        if cached and (datetime.now(timezone.utc)-datetime.fromisoformat(cached['fetched_at'])).total_seconds()<ttl:
            if len(cached['body'].encode('utf-8'))>max_bytes:raise ValueError('Cached response exceeded the safe response size.')
            event('cache_hit',key)
            return cached['status'],cached['final_url'],cached['body']
    state=health(path,key)
    if state and state['retry_at']>store.now():
        pause=(datetime.fromisoformat(state['retry_at'])-datetime.now(timezone.utc)).total_seconds()
        if not state['reason'] and pause<=3:time.sleep(max(0,pause))
        else:
            event('cooldown_skip',key)
            raise AccessBlocked(state['reason'] or 'Platform is cooling down; public retrieval will retry later.',state['retry_at'],False)
    headers={'User-Agent':'NextRole/1.0 (personal job-search tool)','Accept':'application/json,text/html'}
    if cached:
        if cached['etag']:headers['If-None-Match']=cached['etag']
        if cached['modified']:headers['If-Modified-Since']=cached['modified']
    context=ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except ImportError:pass
    opener=build_opener(HTTPSHandler(context=context),RedirectGuard())
    # Spacing is durable across isolated workers, including closure checks.
    defer(path,key,3 if key=='linkedin:detail' else 1)
    revalidated=False
    try:
        with opener.open(Request(url,headers=headers),timeout=20) as response:
            raw=response.read(max_bytes+1)
            if len(raw)>max_bytes:raise ValueError('Posting response exceeded the safe response size.')
            code,final,body=response.status,response.geturl(),raw.decode('utf-8',errors='replace')
            etag,modified=response.headers.get('ETag',''),response.headers.get('Last-Modified','')
    except HTTPError as error:
        if error.code==304 and cached:
            revalidated=True
            code,final,body=cached['status'],cached['final_url'],cached['body']
            etag,modified=cached['etag'],cached['modified'];event('revalidated',key)
        elif error.code in (401,403,429,999):
            event('blocked',key)
            reason='Platform denied or rate-limited public access (HTTP '+str(error.code)+').'
            retry_at=defer(path,key,retry_seconds(error.headers.get('Retry-After')),reason,True)
            raise AccessBlocked(reason,retry_at) from error
        else:
            event('failed',key)
            return error.code,error.geturl(),''
    except Exception:
        event('failed',key);raise
    if any(term in final.lower() for term in ('/authwall','/login','/signin','/checkpoint','/captcha')):
        reason='Sign-in or verification page returned; public retrieval will retry later.'
        event('blocked',key);raise AccessBlocked(reason,defer(path,key,120,reason,True))
    success(path,key)
    if not revalidated:event('fetched',key)
    if path and code==200:
        with store.connect(path) as db:
            db.execute('''INSERT INTO response_cache VALUES(?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
                status=excluded.status,final_url=excluded.final_url,body=excluded.body,fetched_at=excluded.fetched_at,
                etag=excluded.etag,modified=excluded.modified''',(url,code,final,body,store.now(),etag,modified))
    return code,final,body
