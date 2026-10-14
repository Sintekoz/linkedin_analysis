"""Public posting availability evidence, independent of application stages and fit ratings."""
import json
import re
import ssl
import time
from datetime import datetime,timezone,timedelta
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlsplit,urljoin,parse_qs,quote
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler
from . import store
from .duplicates import posting_link

ALLOWED = ('linkedin.com','indeed.com','glassdoor.com','glassdoor.ie')
EXACT = {'boards.greenhouse.io','job-boards.greenhouse.io','boards-api.greenhouse.io',
         'jobs.lever.co','jobs.eu.lever.co','api.lever.co','api.eu.lever.co','grnh.se'}

class AccessBlocked(ValueError):pass


def allowed_url(url):
    p=urlsplit(url)
    host=(p.hostname or '').lower()
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not (host in EXACT or any(host==d or host.endswith('.'+d) for d in ALLOWED)):
        raise ValueError('This employer platform is not supported for automatic status checks yet.')
    return url


class RedirectGuard(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        allowed_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch(url):
    allowed_url(url)
    context=ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except ImportError:pass
    opener=build_opener(HTTPSHandler(context=context),RedirectGuard())
    try:
        with opener.open(Request(url,headers={'User-Agent':'NextRole/1.0 (personal job-search tool)','Accept':'application/json,text/html'}),timeout=20) as response:
            body=response.read(3_000_001)
            if len(body)>3_000_000:raise ValueError('Posting response exceeded the safe response size.')
            return response.status,response.geturl(),body.decode('utf-8',errors='replace')
    except HTTPError as error:
        if error.code in (401,403,429,999):raise AccessBlocked('Platform denied or rate-limited public access. Closure could not be verified.')
        return error.code,error.geturl(),''


def ats_identity(url):
    p=urlsplit(url);host=p.hostname;path=p.path.strip('/').split('/')
    if host in ('boards.greenhouse.io','job-boards.greenhouse.io') and len(path)==3 and path[1]=='jobs' and path[2].isdigit():
        return 'greenhouse',path[0],path[2],'global'
    if host in ('jobs.lever.co','jobs.eu.lever.co') and len(path)>=2 and re.fullmatch(r'[0-9a-f-]{36}',path[1],re.I):
        return 'lever',path[0],path[1],'eu' if host=='jobs.eu.lever.co' else 'global'
    return None


def published_ids(identity,cache):
    provider,board,_,region=identity;key=(provider,board,region)
    if key in cache:
        value=cache[key]
        if isinstance(value,Exception):raise value
        return value
    try:
        if provider=='greenhouse':
            url='https://boards-api.greenhouse.io/v1/boards/'+quote(board,safe='')+'/jobs'
            code,final,body=fetch(url)
            if code!=200 or final!=url:raise ValueError('Employer board could not be verified (HTTP '+str(code)+').')
            payload=json.loads(body)
            rows=payload.get('jobs') if isinstance(payload,dict) else None
            if not isinstance(rows,list) or not isinstance(payload.get('meta'),dict) or type(payload['meta'].get('total')) is not int or payload['meta']['total']!=len(rows) or any(not isinstance(r,dict) or not re.fullmatch(r'[0-9]+',str(r.get('id',''))) for r in rows):
                raise ValueError('Employer returned an incomplete or unexpected feed; closure was not inferred.')
            ids={str(r['id']) for r in rows}
            if len(ids)!=len(rows):raise ValueError('Employer feed duplicated posting IDs; closure was not inferred.')
        else:
            host='api.eu.lever.co' if region=='eu' else 'api.lever.co'
            ids=set();skip=0
            while True:
                url='https://'+host+'/v0/postings/'+quote(board,safe='')+'?mode=json&limit=100&skip='+str(skip)
                code,final,body=fetch(url)
                if code!=200 or final!=url:raise ValueError('Employer board could not be verified (HTTP '+str(code)+').')
                rows=json.loads(body)
                if not isinstance(rows,list) or any(not isinstance(r,dict) or not r.get('id') for r in rows):
                    raise ValueError('Employer returned an unexpected feed; closure was not inferred.')
                page={str(r['id']) for r in rows}
                if len(page)!=len(rows):raise ValueError('Employer feed duplicated posting IDs; closure was not inferred.')
                if rows and page<=ids:raise ValueError('Employer feed repeated a page; closure was not inferred.')
                ids.update(page)
                if len(rows)<100:break
                skip+=100
                time.sleep(0.2)
        cache[key]=ids
        return ids
    except Exception as error:
        cache[key]=error
        raise


class PostingHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.stack=[];self.closed_text=[];self.json_scripts=[];self.current_script=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='script' and attrs.get('type','').lower()=='application/ld+json':self.current_script=[]
        if tag not in ('meta','link','input','br','hr','img','source','area','wbr'):
            self.stack.append((tag,attrs.get('class','')+' '+attrs.get('id','')))
    def handle_endtag(self,tag):
        if tag=='script' and self.current_script is not None:
            self.json_scripts.append(''.join(self.current_script));self.current_script=None
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:self.stack=self.stack[:i];break
    def handle_data(self,data):
        if self.current_script is not None:self.current_script.append(data)
        # LinkedIn's explicit posting-closure panel, never recommendations/footer.
        if any('closed-job' in cls for _,cls in self.stack):self.closed_text.append(data)


def nodes(value):
    if isinstance(value,list):
        for item in value:yield from nodes(item)
    elif isinstance(value,dict):
        yield value
        if '@graph' in value:yield from nodes(value['@graph'])


def page_status(url,final,body,title):
    if any(term in final.lower() for term in ('/authwall','/login','/signin','/checkpoint','/captcha')):
        return 'unverified','Sign-in or verification page returned; closure could not be verified.'
    if store.canonical_url(url)!=store.canonical_url(final):
        return 'unverified','Redirected away from this exact posting; closure could not be verified.'
    parser=PostingHTML();parser.feed(body)
    if re.search(r'no longer accepting applications|no longer available|job has expired',' '.join(parser.closed_text),re.I):
        return 'closed','The posting explicitly says applications are no longer accepted.'
    for script in parser.json_scripts:
        try:payload=json.loads(script)
        except ValueError:continue
        for node in nodes(payload):
            kind=node.get('@type');kind=kind if isinstance(kind,list) else [kind]
            if 'JobPosting' not in kind:continue
            target=node.get('url') or node.get('mainEntityOfPage')
            if isinstance(target,dict):target=target.get('@id')
            same_url=False
            try:same_url=bool(target) and store.canonical_url(target)==store.canonical_url(url)
            except (ValueError,TypeError):pass
            same_title=str(node.get('title','')).strip().casefold()==str(title).strip().casefold()
            if not (same_url or not target and same_title):continue
            expiry=node.get('validThrough')
            if expiry:
                try:
                    stamp=datetime.fromisoformat(str(expiry).replace('Z','+00:00'))
                    if not stamp.tzinfo:stamp=stamp.replace(tzinfo=timezone.utc)
                    if stamp < datetime.now(timezone.utc):return 'unverified','Posting metadata has expired; the employer has not confirmed closure.'
                except ValueError:return 'unverified','Posting expiry metadata could not be interpreted.'
            return 'open','The exact posting is currently published with JobPosting metadata.'
    return 'unverified','Page was reachable but did not provide reliable evidence that this exact vacancy is open or closed.'


def check_link(url,title,cache,blocked):
    try:
        allowed_url(url)
        identity=ats_identity(url)
        if identity:
            ids=published_ids(identity,cache)
            return ('open','The exact posting is in the complete published employer feed.') if identity[2] in ids else ('closed','The exact posting is no longer in the complete published employer feed.')
        host=urlsplit(url).hostname
        key=next((d for d in ALLOWED if host==d or host.endswith('.'+d)),host)
        if key in blocked:return 'unverified','This platform blocked an earlier check. Further requests were skipped for this run.'
        last=cache.get(('last_request',key),0)
        if time.monotonic()-last<1:time.sleep(1-(time.monotonic()-last))
        cache[('last_request',key)]=time.monotonic()
        code,final,body=fetch(url)
        redirected=ats_identity(final)
        if redirected:
            ids=published_ids(redirected,cache)
            return ('open','The employer publishes this exact posting.') if redirected[2] in ids else ('closed','The employer no longer publishes this exact posting.')
        if code in (404,410) and posting_link(url) and store.canonical_url(url)==store.canonical_url(final):
            return 'closed','This exact posting returned HTTP '+str(code)+'.'
        if code!=200:return 'unverified','Platform returned HTTP '+str(code)+'; closure was not inferred.'
        return page_status(url,final,body,title)
    except AccessBlocked as error:
        host=urlsplit(url).hostname or ''
        blocked.add(next((d for d in ALLOWED if host==d or host.endswith('.'+d)),host))
        return 'unverified',str(error)
    except Exception as error:
        return 'unverified',str(error)[:400]


def record(db,jid,status,reason):
    old=db.execute('SELECT posting_status,posting_last_confirmed FROM jobs WHERE id=?',(jid,)).fetchone()
    stamp=store.now()
    db.execute('UPDATE jobs SET posting_status=?,posting_checked_at=?,posting_reason=? WHERE id=?',(status,stamp,reason,jid))
    if status in ('open','closed'):
        db.execute('UPDATE jobs SET posting_confirmed_at=?,posting_last_confirmed=?,availability=? WHERE id=?',(stamp,status,status,jid))
    if status in ('open','closed') and status!=(old['posting_last_confirmed'] or old['posting_status']):
        db.execute('INSERT INTO events(job_id,message,created_at) VALUES(?,?,?)',(jid,'Posting '+status+': '+reason,stamp))


def seen(db,jid,job):
    db.execute('UPDATE jobs SET posting_last_seen_at=? WHERE id=?',(store.now(),jid))
    # Employer adapters read the public published feed. Search visibility alone
    # on aggregators records last-seen evidence without overriding a verified closure.
    if job.get('source') in ('greenhouse','lever') and job.get('availability')=='ongoing':
        record(db,jid,'open','Seen in the employer’s published feed during collection.')


def run_checks(path,run_id):
    with store.connect(path) as db:
        row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        config=json.loads(row['config']);checkpoint=json.loads(row['checkpoint'])
        ids=config['availability_ids'];done=set(checkpoint.get('checked_ids',[]))
    cache={};blocked=set(checkpoint.get('blocked_hosts',[]))
    for jid in ids:
        if jid in done:continue
        with store.connect(path) as db:
            if db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()[0]!='running':return
            job=db.execute("SELECT * FROM jobs WHERE id=? AND duplicate_of=''",(jid,)).fetchone()
            links=[r[0] for r in db.execute('SELECT url FROM job_links WHERE job_id=?',(jid,))]
        if not job:
            done.add(jid);continue
        links=list(dict.fromkeys([job['direct_url'],job['url']]+links));links=[u for u in links if u]
        # Prefer authoritative employer identities. A stale aggregator cannot
        # override a confirmed employer closure (or an employer's active feed).
        employer=[u for u in links if ats_identity(u) or urlsplit(u).hostname=='grnh.se']
        outcomes=[]
        for url in employer or links:
            outcome=check_link(url,job['title'],cache,blocked);outcomes.append(outcome)
            if outcome[0]=='open':break
        if any(s=='open' for s,_ in outcomes):status='open'
        elif outcomes and all(s=='closed' for s,_ in outcomes):status='closed'
        else:status='unverified'
        reason=next((reason for s,reason in outcomes if s==status),'No supported posting link is available.')
        with store.connect(path) as db:
            if db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()[0]!='running':return
            record(db,jid,status,reason);done.add(jid)
            db.execute('INSERT OR IGNORE INTO run_jobs(run_id,job_id) VALUES(?,?)',(run_id,jid))
            db.execute("UPDATE runs SET phase='checking',scanned=?,checkpoint=?,heartbeat=?,message=? WHERE id=?",(len(done),json.dumps({'checked_ids':list(done),'blocked_hosts':list(blocked)}),store.now(),f'Checking posting availability: {len(done)} of {len(ids)} jobs.',run_id))
    with store.connect(path) as db:
        db.execute("UPDATE runs SET status='completed',phase='finished',finished_at=?,message=? WHERE id=? AND status='running'",(store.now(),f'Availability checks finished for {len(done)} jobs. Blocked or ambiguous pages remain unverified.',run_id))
