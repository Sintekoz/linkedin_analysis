"""Employer feeds verified publicly; exact employer links can extend coverage."""
import json
from . import store, availability

CATALOG = [('stripe','Stripe'),('datadog','Datadog'),('mongodb','MongoDB'),
           ('elastic','Elastic'),('canonical','Canonical'),('toast','Toast')]
TERMS = ('data | analytics | business intelligence | financial systems | finance systems | '
         'business systems | business analyst | operations analyst | revenue operations | '
         'solutions engineer | implementation | integration')

def add_board(db,provider,board,company,region='global',wid=None):
    for row in db.execute('SELECT id,config FROM sources'):
        config=json.loads(row['config'])
        if config.get('provider')==provider and config.get('board')==board and config.get('region','global')==region:
            return row['id'],False
    config={'provider':provider,'board':board,'company':company,'region':region,'keywords':TERMS,
            'location':'Ireland / eligible remote','scope':'ireland_remote','country':'Ireland',
            'job_type':'','remote':False,'description':True,'hours_old':None,'limit':None,
            'rationale':'Public employer feed for data, analytics, finance systems and related roles; remote hiring in Ireland may need confirmation.'}
    name=company+' · Ireland & remote'
    sid=db.execute('INSERT INTO sources(name,config,created_at) VALUES(?,?,?)',(name,json.dumps(config),store.now())).lastrowid
    if wid:
        db.execute("INSERT INTO runs(source_id,name,config,status,phase,started_at,workflow_id) VALUES(?,?,?,'queued','queued',?,?)",
                   (sid,name,json.dumps(config | {'_pipeline':2}),store.now(),wid))
    return sid,True

def add_catalog(db):
    return sum(add_board(db,'greenhouse',board,company)[1] for board,company in CATALOG)

def discover(db,job,wid=None):
    if not job.get('company'):return
    for url in (job.get('url'),job.get('direct_url')):
        if not url:continue
        identity=availability.ats_identity(url)
        if identity:
            provider,board,_,region=identity
            add_board(db,provider,board,job['company'],region,wid)
