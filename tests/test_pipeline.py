import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from importlib.machinery import ModuleSpec
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from dashboard import store,network,enrichment,pipeline,ats,availability,employers,details,rating
from dashboard.collectors import collect
from dashboard.server import App
from dashboard.worker import run
from test_sources import FakeProcess

LI='https://www.linkedin.com/jobs/view/123456/'
GH='https://job-boards.greenhouse.io/example/jobs/123'
FEED='https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true'

class Response:
    def __init__(self,url,body,headers=None):self.status=200;self.url=url;self.body=body.encode();self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,n):return self.body[:n]
    def geturl(self):return self.url

class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'jobs.db';store.init(self.path)
    def tearDown(self):self.temp.cleanup()
    def job(self,**fields):
        with store.connect(self.path) as db:return store.upsert(db,{'title':'Data Engineer','company':'Example','location':'Dublin, Ireland','url':LI,**fields},tracking=True)[0]

class SharedCacheTests(DatabaseCase):
    def test_discovery_description_and_closure_share_the_same_feed_across_workers(self):
        payload=json.dumps({'jobs':[{'id':123,'title':'Data Engineer','content':'<p>Python SQL</p>','absolute_url':GH}], 'meta':{'total':1}})
        opener=SimpleNamespace(open=lambda *a,**k:Response(FEED,payload))
        with patch.object(network,'build_opener',return_value=opener) as build:
            with network.session(self.path):self.assertEqual(ats.read_json(FEED)['jobs'][0]['id'],123)
            with network.session(self.path):
                result,_=enrichment.employer_description(('greenhouse','example','123','global'),'Example')
                self.assertIn('Python',result['description'])
            with network.session(self.path):self.assertEqual(availability.check_link(GH,'Data Engineer',{},set())[0],'open')
        self.assertEqual(build.call_count,1)
        with store.connect(self.path) as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM request_events WHERE outcome='cache_hit'").fetchone()[0],2)

    def test_stale_cache_uses_conditional_headers_and_keeps_body_after_304(self):
        with network.session(self.path),patch.object(network,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k:Response(LI,'original',{'ETag':'version-1'}))):network.fetch(LI)
        with store.connect(self.path) as db:
            db.execute("UPDATE response_cache SET fetched_at='2020-01-01T00:00:00+00:00'");db.execute("UPDATE endpoint_health SET retry_at=''")
        requests=[]
        def read(request,**kw):
            requests.append(request);raise HTTPError(LI,304,'Not modified',{},None)
        with network.session(self.path),patch.object(network,'build_opener',return_value=SimpleNamespace(open=read)):
            self.assertEqual(network.fetch(LI)[2],'original')
        self.assertEqual(requests[0].get_header('If-none-match'),'version-1')

    def test_retry_after_persists_and_does_not_spend_requests_during_cooldown(self):
        def read(*a,**k):raise HTTPError(LI,429,'Too many',{'Retry-After':'600'},None)
        with network.session(self.path),patch.object(network,'build_opener',return_value=SimpleNamespace(open=read)) as build:
            with self.assertRaises(network.AccessBlocked) as first:network.fetch(LI)
            self.assertTrue(first.exception.attempted)
            with self.assertRaises(network.AccessBlocked) as skipped:network.fetch(LI.replace('123456','654321'))
            self.assertFalse(skipped.exception.attempted);self.assertEqual(build.call_count,1)
        self.assertGreater(datetime.fromisoformat(first.exception.retry_at),datetime.now(timezone.utc)+timedelta(seconds=590))
        with store.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM response_cache').fetchone()[0],0)
        self.assertIsNone(network.health(self.path,'linkedin:search'))

    def test_authwall_is_never_cached_as_a_description(self):
        with network.session(self.path),patch.object(network,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k:Response('https://www.linkedin.com/authwall','Sign in'))):
            with self.assertRaises(network.AccessBlocked):network.fetch(LI)
        with store.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM response_cache').fetchone()[0],0)

    def test_bad_redirect_and_oversized_responses_are_rejected(self):
        with self.assertRaises(ValueError):network.RedirectGuard().redirect_request(None,None,302,'',{},'https://127.0.0.1/private')
        with network.session(self.path),patch.object(network,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k:Response(LI,'x'*20))):
            with self.assertRaisesRegex(ValueError,'response size'):network.fetch(LI,max_bytes=10)

class DescriptionTests(DatabaseCase):
    def body(self,target,title='Data Engineer'):
        return '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','url':target,'title':title,'description':'<p>SQL and Python pipelines</p>'})+'</script>'
    def test_exact_posting_description_and_recommendation_rejection(self):
        self.assertIn('SQL',enrichment.page_description(LI,LI,self.body(LI),'Data Engineer'))
        self.assertEqual(enrichment.page_description(LI,LI,self.body(LI.replace('123456','654321')),'Data Engineer'),'')
        self.assertEqual(enrichment.page_description(LI,'https://www.linkedin.com/authwall',self.body(LI),'Data Engineer'),'')
    def test_linkedin_html_fallback_requires_matching_main_heading(self):
        html='<h1>Data Engineer</h1><div class="show-more-less-html__markup"><p>Build SQL pipelines</p></div><p>Recommended job</p>'
        self.assertIn('SQL',enrichment.page_description(LI,LI,html,'Data Engineer'))
        self.assertNotIn('Recommended',enrichment.page_description(LI,LI,html,'Data Engineer'))
        self.assertEqual(enrichment.page_description(LI,LI,html,'Other role'),'')
    def test_ambiguous_jobposting_without_url_must_match_title(self):
        html='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Other role','description':'Unrelated'})+'</script>'
        self.assertEqual(enrichment.page_description(LI,LI,html,'Data Engineer'),'')
    def test_employer_link_is_tried_before_linkedin(self):
        jid=self.job(direct_url=GH)
        with store.connect(self.path) as db:job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        with patch.object(enrichment,'employer_description',return_value=({'description':'SQL'},FEED)) as feed,patch.object(network,'fetch') as public:
            self.assertEqual(enrichment.retrieve(job,[])[0]['description'],'SQL');feed.assert_called_once();public.assert_not_called()
    def test_queue_deduplicates_and_preserves_human_tracking_during_recovery(self):
        jid=self.job(notes='Keep notes',stage='interview')
        with store.connect(self.path) as db:
            for _ in range(3):enrichment.enqueue(db,jid)
            wid=db.execute('INSERT INTO workflows(started_at) VALUES(?)',(store.now(),)).lastrowid
            from dashboard import workflows
            workflows.remember(db,wid,jid)
            rid=db.execute("INSERT INTO runs(name,config,status,kind,started_at,workflow_id) VALUES('Recover','{}','running','enrichment',?,?)",(store.now(),wid)).lastrowid
        with patch.object(enrichment,'retrieve',return_value=({'description':'SQL and Python'},LI)):enrichment.run_batch(self.path,rid)
        with store.connect(self.path) as db:
            job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            self.assertEqual((job['notes'],job['stage']),('Keep notes','interview'));self.assertEqual(job['rating_status'],'pending')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM enrichment_tasks').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM enrichment_tasks').fetchone()[0],'complete')
            self.assertEqual(job['description_source'],LI)
    def test_existing_description_prevents_another_network_fetch(self):
        jid=self.job(description='SQL')
        with store.connect(self.path) as db:enrichment.enqueue(db,jid);self.assertEqual(db.execute('SELECT COUNT(*) FROM enrichment_tasks').fetchone()[0],0)
    def test_not_interested_jobs_do_not_enter_description_queue(self):
        jid=self.job();store.set_interest(self.path,[jid],'not_interested')
        with store.connect(self.path) as db:enrichment.enqueue(db,jid);self.assertEqual(db.execute('SELECT COUNT(*) FROM enrichment_tasks').fetchone()[0],0)
    def test_status_page_cache_updates_changed_description_without_overwriting_tracking(self):
        jid=self.job(description='Original description',notes='Keep',stage='applied')
        with store.connect(self.path) as db:
            db.execute("UPDATE jobs SET rating_status='complete',rating_hash='previous' WHERE id=?",(jid,))
            db.execute('INSERT INTO response_cache VALUES(?,?,?,?,?,?,?)',(LI,200,LI,self.body(LI),store.now(),'',''))
            job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
            with patch.object(network,'fetch') as fetch:
                self.assertTrue(enrichment.reuse_cached(db,job,[]));fetch.assert_not_called()
            after=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            self.assertEqual((after['notes'],after['stage']),('Keep','applied'));self.assertIn('Python',after['description']);self.assertEqual(after['rating_status'],'pending')
    def test_empty_cached_page_does_not_remove_a_saved_description(self):
        jid=self.job(description='Keep description')
        with store.connect(self.path) as db:
            db.execute('INSERT INTO response_cache VALUES(?,?,?,?,?,?,?)',(LI,200,LI,'<p>Sign in</p>',store.now(),'',''))
            job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
            self.assertFalse(enrichment.reuse_cached(db,job,[]));self.assertEqual(db.execute('SELECT description FROM jobs WHERE id=?',(jid,)).fetchone()[0],'Keep description')
    def test_missing_description_after_an_earlier_recovery_reenters_queue(self):
        jid=self.job()
        with store.connect(self.path) as db:
            enrichment.enqueue(db,jid);db.execute("UPDATE enrichment_tasks SET status='complete' WHERE job_id=?",(jid,))
            enrichment.enqueue(db,jid)
            self.assertEqual(db.execute('SELECT status FROM enrichment_tasks WHERE job_id=?',(jid,)).fetchone()[0],'pending')

class StructuredDetailTests(DatabaseCase):
    def setUp(self):
        super().setUp();self.calls=[];self.denied=False
        outer=self
        class Adapter:
            def __init__(self):self.session=None
            def _graph(self,operation,query,variables):
                outer.calls.append(query)
                return SimpleNamespace(status_code=429 if outer.denied else 200,headers={'Retry-After':'600'})
            def _fetch_details(self,ids):
                response=self._graph('JobDetails',list(ids),{})
                return {} if response.status_code!=200 else {jid:{'description':'Full SQL '+jid} for jid in ids}
        country=SimpleNamespace(get_glassdoor_url=lambda:'https://www.glassdoor.ie')
        self.modules=patch.dict('sys.modules',{'jobspy.glassdoor':SimpleNamespace(Glassdoor=Adapter,create_session=lambda:SimpleNamespace(headers={}),headers={}),
            'jobspy.model':SimpleNamespace(Country=SimpleNamespace(IRELAND=country),DescriptionFormat=SimpleNamespace(MARKDOWN='markdown'),ScraperInput=lambda **kw:SimpleNamespace(**kw))})
        self.modules.start()
    def tearDown(self):self.modules.stop();super().tearDown()
    def jobs(self,n=3):
        ids=[self.job(url='https://www.glassdoor.com/partner/jobListing.htm?jobListingId='+str(1000+i),source='glassdoor') for i in range(n)]
        with store.connect(self.path) as db:return [dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()) for jid in ids]
    def test_exact_ids_are_batched_and_reused_from_persistent_cache(self):
        jobs=self.jobs()
        with network.session(self.path):
            value=details.glassdoor(self.path,jobs);self.assertTrue(all('SQL' in value[j['id']]['description'] for j in jobs))
            cached=details.glassdoor(self.path,jobs)
        self.assertEqual(len(self.calls),1);self.assertEqual(set(self.calls[0]),{'gd-1000','gd-1001','gd-1002'});self.assertEqual(value,cached)
    def test_detail_denial_has_its_own_cooldown_and_skips_subsequent_requests(self):
        jobs=self.jobs();self.denied=True
        with network.session(self.path):
            failed=details.glassdoor(self.path,jobs);skipped=details.glassdoor(self.path,jobs)
        self.assertEqual(len(self.calls),1);self.assertTrue(all(e.attempted for e in failed.values()));self.assertTrue(all(not e.attempted for e in skipped.values()))
        self.assertIsNone(network.health(self.path,'glassdoor:search'))
    def test_employer_id_is_prioritized_over_structured_aggregator_details(self):
        jobs=self.jobs(1);jobs[0]['direct_url']=GH
        self.assertEqual(details.glassdoor(self.path,jobs),{});self.assertEqual(self.calls,[])
    def test_generic_glassdoor_url_is_not_a_posting_id(self):
        for url in ('https://www.glassdoor.com/jobs','https://evil.example/?jl=123','https://www.glassdoor.com/?jl=abc'):
            self.assertIsNone(details.native_id(url))
    def test_normal_anonymous_initialization_precedes_detail_query(self):
        jobs=self.jobs(1);events=[]
        module=__import__('sys').modules['jobspy.glassdoor'];old=module.Glassdoor
        class WarmAdapter(old):
            def _search_variables(self):events.append('initialize');self.session.get('https://www.glassdoor.ie/autocomplete/location?term=Ireland')
            def _fetch_details(self,ids):events.append('details');return super()._fetch_details(ids)
        module.Glassdoor=WarmAdapter
        module.create_session=lambda:SimpleNamespace(headers={},get=lambda *a,**k:SimpleNamespace(status_code=200,headers={}))
        details.glassdoor(self.path,jobs)
        self.assertEqual(events,['initialize','details']);self.assertEqual(len(self.calls),1)
    def test_successful_initialization_does_not_erase_repeated_detail_denials(self):
        jobs=self.jobs(1);self.denied=True
        module=__import__('sys').modules['jobspy.glassdoor'];old=module.Glassdoor
        class WarmAdapter(old):
            def _search_variables(self):self.session.get('https://www.glassdoor.ie/autocomplete/location?term=Ireland')
        module.Glassdoor=WarmAdapter
        module.create_session=lambda:SimpleNamespace(headers={},get=lambda *a,**k:SimpleNamespace(status_code=200,headers={}))
        for _ in range(3):
            with store.connect(self.path) as db:db.execute("UPDATE endpoint_health SET retry_at='' WHERE endpoint=?",(details.KEY,))
            details.glassdoor(self.path,jobs)
        self.assertEqual(network.health(self.path,details.KEY)['failures'],3)

class FairWorkflowTests(DatabaseCase):
    def setUp(self):
        super().setUp();self.app=App(self.path);self.app.mutate('/api/settings',{'cv_text':'SQL Python'})
        for provider in ('linkedin','indeed'):
            self.app.mutate('/api/sources',{'name':provider,'config':{'provider':provider,'keywords':'Data','location':'Ireland'}})
        self.process=patch('dashboard.server.subprocess.Popen',side_effect=lambda *a,**k:FakeProcess());self.process.start()
    def tearDown(self):self.app.processes.clear();self.process.stop();super().tearDown()
    def test_full_workflow_rotates_pages_then_enriches_and_rates_automatically(self):
        calls=[]
        def scrape(**kwargs):
            calls.append((kwargs['site_name'][0],kwargs['offset']))
            self.assertFalse(kwargs.get('fetch_description',kwargs.get('linkedin_fetch_description')))
            offset=kwargs['offset'];provider=kwargs['site_name'][0]
            count=10 if provider=='linkedin' and offset==0 else 1
            rows=[{'title':'Data Engineer','company':'Example','location':'Dublin, Ireland',
                   'description':'' if provider=='linkedin' else 'Python SQL',
                   'job_url':f'https://www.linkedin.com/jobs/view/{123456+offset+i}/' if provider=='linkedin' else 'https://www.indeed.com/viewjob?jk=new'} for i in range(count)]
            return SimpleNamespace(to_dict=lambda **kw:rows)
        def reviews(jobs,settings):return {j['id']:{'score':8,'summary':'SQL fit','strengths':['SQL'],'gaps':[]} for j in jobs}
        with patch.dict('sys.modules',{'jobspy':SimpleNamespace(scrape_jobs=scrape,__spec__=ModuleSpec('jobspy',None))}),patch.object(pipeline,'delay_search'),patch.object(enrichment,'retrieve',side_effect=lambda job,links:({'description':'Python SQL recovered'},job['url'])),patch('dashboard.rating.cli_status',return_value={'ready':True}),patch('dashboard.rating.review_batch',side_effect=reviews),patch.object(availability,'check_link',return_value=('open','Test posting')),patch.object(availability.time,'sleep'):
            result=self.app.start_workflow()
            for _ in range(30):
                self.app.reap()
                if not self.app.processes:break
                rid=next(iter(self.app.processes));run(self.path,rid);self.app.processes[rid].code=0;self.app.reap()
            self.assertFalse(self.app.processes)
            snapshot=self.app.snapshot()
        self.assertEqual(calls,[('linkedin',0),('indeed',0),('linkedin',10)])
        self.assertEqual(len(snapshot['jobs']),12);self.assertTrue(all(j['rating_status']=='complete' for j in snapshot['jobs']))
        metrics=snapshot['workflows'][0]['pipeline'];self.assertEqual((metrics['found'],metrics['descriptions'],metrics['rated']),(12,12,12))
        self.assertEqual(metrics['searches_started'],2);self.assertEqual(snapshot['workflows'][0]['status'],'completed')

    def test_provider_cooldown_rotation_prefers_unstarted_query_after_cooldown(self):
        self.app.mutate('/api/sources',{'name':'LinkedIn second','config':{'provider':'linkedin','keywords':'Analytics','location':'Ireland'}})
        result=self.app.start_workflow();first=result['ids'][0]
        with store.connect(self.path) as db:
            db.execute("UPDATE runs SET status='failed',provider_blocked=1,error_kind='provider_access' WHERE id=?",(first,))
        self.app.processes[first].code=0;self.app.reap()
        self.assertEqual(next(iter(self.app.processes)),result['ids'][1])
        self.app.processes.clear()
        with store.connect(self.path) as db:
            db.execute("UPDATE runs SET status='completed' WHERE source_id=2")
            db.execute("UPDATE runs SET retry_at='2000-01-01' WHERE kind='collection'")
            db.execute("UPDATE endpoint_health SET retry_at='2000-01-01'")
        self.app.reap()
        self.assertEqual(next(iter(self.app.processes)),result['ids'][2])

    def test_metadata_checkpoint_yields_without_imposing_a_total_limit(self):
        saved=[]
        def scrape(**kw):return SimpleNamespace(to_dict=lambda **_: [{'title':'Data','job_url':LI.replace('123456',str(123456+i))} for i in range(10)])
        with patch.dict('sys.modules',{'jobspy':SimpleNamespace(scrape_jobs=scrape)}):
            self.assertEqual(len(list(collect({'keywords':'Data','location':'Ireland'},save_checkpoint=saved.append,page_budget=1))),10)
        self.assertEqual(saved[-1]['offset'],10);self.assertFalse(saved[-1]['complete'])

    def test_employer_catalog_and_discovered_boards_are_idempotent(self):
        with store.connect(self.path) as db:
            self.assertEqual(employers.add_catalog(db),6);self.assertEqual(employers.add_catalog(db),0)
            employers.discover(db,{'company':'Example','direct_url':GH});employers.discover(db,{'company':'Example','direct_url':GH})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sources WHERE name='Example · Ireland & remote'").fetchone()[0],1)
    def test_rating_progress_keeps_prior_pass_totals(self):
        jid=self.job(description='SQL')
        with store.connect(self.path) as db:
            rid=db.execute("INSERT INTO runs(name,config,status,kind,started_at,rated) VALUES('Rates','{}','running','rating',?,10)",(store.now(),)).lastrowid
        observed=[]
        def review(jobs,settings):
            with store.connect(self.path) as db:observed.append(db.execute('SELECT rated FROM runs WHERE id=?',(rid,)).fetchone()[0])
            return {j['id']:{'score':8,'summary':'SQL fit','strengths':['SQL'],'gaps':[]} for j in jobs}
        with patch.object(rating,'cli_status',return_value={'ready':True}),patch.object(rating,'review_batch',side_effect=review):
            self.assertEqual(rating.rate_jobs(self.path,[jid],rid,initial_counts={'rated':10})['rated'],1)
        self.assertEqual(observed,[10])
    def test_later_metadata_refresh_rerates_a_job_already_processed_in_this_workflow(self):
        jid=self.job(description='SQL')
        with store.connect(self.path) as db:
            wid=db.execute('INSERT INTO workflows(started_at) VALUES(?)',(store.now(),)).lastrowid
            from dashboard import workflows
            workflows.remember(db,wid,jid)
            rid=db.execute("INSERT INTO runs(name,config,status,kind,started_at,workflow_id) VALUES('Rates','{}','running','rating',?,?)",(store.now(),wid)).lastrowid
            db.execute('INSERT INTO run_jobs VALUES(?,?)',(rid,jid))
            job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
            review={'score':8,'summary':'SQL fit','strengths':['SQL'],'gaps':[]}
            db.execute("UPDATE jobs SET rating_status='complete',rating_hash=?,analysis=? WHERE id=?",(rating.fingerprint(job,rating.settings_from(db)),json.dumps(review),jid))
            self.assertEqual(pipeline.rating_ids(db,wid,rid),[])
            store.upsert(db,{'id':jid,'title':job['title'],'url':LI,'description':'SQL plus Python'})
            self.assertEqual(pipeline.rating_ids(db,wid,rid),[jid])
    def test_corrupt_review_is_not_treated_as_valid_cache(self):
        jid=self.job(description='SQL')
        with store.connect(self.path) as db:
            wid=db.execute('INSERT INTO workflows(started_at) VALUES(?)',(store.now(),)).lastrowid
            from dashboard import workflows
            workflows.remember(db,wid,jid)
            job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
            db.execute("UPDATE jobs SET rating_status='complete',rating_hash=?,analysis='{}' WHERE id=?",(rating.fingerprint(job,rating.settings_from(db)),jid))
            self.assertEqual(pipeline.rating_ids(db,wid),[jid])

if __name__=='__main__':unittest.main()
