"""Recovery must preserve progress, distinguish access failures and count unique jobs."""
import json
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from dashboard import store, workflows, rating
from dashboard.server import App
from dashboard.worker import run
from dashboard.collectors import collect
from test_sources import FakeProcess


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'jobs.db'
        self.app=App(self.path)
        self.app.mutate('/api/settings',{'cv_text':'SQL and Python'})
        for provider in ('linkedin','linkedin','indeed'):
            self.app.mutate('/api/sources',{'name':provider,'config':{'provider':provider,'keywords':'Data','location':'Ireland'}})
        self.popen=patch('dashboard.server.subprocess.Popen',side_effect=lambda *a,**k:FakeProcess())
        self.popen.start()
        self.ready=patch('dashboard.rating.cli_status',return_value={'ready':True})
        self.ready.start()
    def tearDown(self):
        self.app.processes.clear();self.popen.stop();self.ready.stop();self.temp.cleanup()
    def review(self,jobs,settings):
        return {j['id']:{'score':8,'summary':'SQL match','strengths':['SQL'],'gaps':[]} for j in jobs}
    def end(self,rid,**changes):
        with store.connect(self.path) as db:
            for key,value in changes.items():db.execute('UPDATE runs SET '+key+'=? WHERE id=?',(value,rid))
        self.app.processes[rid].code=0
        self.app.reap()
    def rows(self):
        with store.connect(self.path) as db:return [dict(r) for r in db.execute('SELECT * FROM runs ORDER BY id')]
    def test_denied_provider_cools_down_while_other_platform_runs_then_retries(self):
        ids=self.app.start_all({})['ids']
        self.end(ids[0],status='failed',provider_blocked=1,error_kind='provider_access')
        self.assertEqual([r['status'] for r in self.rows()],['retry_wait','retry_wait','running'])
        self.assertTrue(self.app.start_workflow()['active'])
        self.end(ids[2],status='completed')
        self.assertFalse(self.app.processes)
        with store.connect(self.path) as db:db.execute("UPDATE runs SET retry_at='2000-01-01' WHERE status='retry_wait'")
        self.app.reap()
        self.assertEqual(self.rows()[0]['attempts'],2)
        self.assertEqual(list(self.app.processes),[ids[0]])
    def test_three_denials_end_provider_retries_without_abandoning_other_sources(self):
        ids=self.app.start_all({})['ids']
        with store.connect(self.path) as db:db.execute('UPDATE runs SET attempts=3 WHERE id=?',(ids[0],))
        self.end(ids[0],status='failed',provider_blocked=1,error_kind='provider_access')
        self.assertEqual([r['status'] for r in self.rows()],['needs_attention','needs_attention','running'])
    def test_linkedin_denial_never_pauses_availability_or_rating_steps(self):
        ids=self.app.start_all({})['ids']
        with store.connect(self.path) as db:
            wid=db.execute('SELECT workflow_id FROM runs WHERE id=?',(ids[0],)).fetchone()[0]
            check=db.execute("INSERT INTO runs(name,config,status,kind,started_at,workflow_id) VALUES('Checks','{}','queued','availability',?,?)",(store.now(),wid)).lastrowid
            rating_step=db.execute("INSERT INTO runs(name,config,status,kind,started_at,workflow_id) VALUES('Ratings','{}','queued','rating',?,?)",(store.now(),wid)).lastrowid
        self.end(ids[0],status='failed',provider_blocked=1,error_kind='provider_access',attempts=3)
        rows={r['id']:r for r in self.rows()}
        self.assertEqual(rows[check]['status'],'queued');self.assertEqual(rows[rating_step]['status'],'queued')
        self.assertEqual(rows[ids[1]]['status'],'needs_attention')
        issue=self.app.snapshot()['workflows'][0]['provider_issues']['linkedin']
        self.assertEqual(issue,{'affected':2,'attempted':1,'not_started':1})
    def test_provider_cooldown_only_affects_collection_steps(self):
        ids=self.app.start_all({})['ids']
        with store.connect(self.path) as db:
            wid=db.execute('SELECT workflow_id FROM runs WHERE id=?',(ids[0],)).fetchone()[0]
            check=db.execute("INSERT INTO runs(name,config,status,kind,started_at,workflow_id) VALUES('Checks','{}','queued','availability',?,?)",(store.now(),wid)).lastrowid
        self.end(ids[0],status='failed',provider_blocked=1,error_kind='provider_access')
        self.assertEqual(next(r for r in self.rows() if r['id']==check)['status'],'queued')
    def test_resume_one_check_does_not_retry_denied_searches(self):
        ids=self.app.start_all({})['ids']
        self.app.processes.clear()
        with store.connect(self.path) as db:
            wid=db.execute('SELECT workflow_id FROM runs WHERE id=?',(ids[0],)).fetchone()[0]
            db.execute("UPDATE runs SET status='needs_attention',error_kind='provider_access'")
            check=db.execute("INSERT INTO runs(name,config,status,kind,started_at,workflow_id) VALUES('Checks','{}','needs_attention','availability',?,?)",(store.now(),wid)).lastrowid
        result=self.app.mutate('/api/workflow/resume',{'workflow_id':wid,'run_ids':[check]})
        self.assertEqual(result['ids'],[check]);self.assertEqual(list(self.app.processes),[check])
        self.assertTrue(all(r['status']=='needs_attention' for r in self.rows() if r['id'] in ids))
    def test_recovered_searches_check_new_jobs_without_repeating_saved_status_checks(self):
        ids=self.app.start_all({'source_ids':[1]})['ids'];self.app.processes.clear()
        with store.connect(self.path) as db:
            wid=db.execute('SELECT workflow_id FROM runs WHERE id=?',(ids[0],)).fetchone()[0]
            db.execute("UPDATE runs SET status='needs_attention' WHERE id=?",(ids[0],))
            check=db.execute("INSERT INTO runs(name,config,status,kind,started_at,workflow_id,checkpoint) VALUES('Checks',?,'completed','availability',?,?,?)",(json.dumps({'availability_ids':['old']}),store.now(),wid,json.dumps({'checked_ids':['old']}))).lastrowid
        result=self.app.resume_workflow(wid)
        self.assertEqual(result['ids'],[ids[0],check])
        final=next(r for r in self.rows() if r['id']==check)
        self.assertEqual(final['status'],'queued');self.assertEqual(json.loads(final['config']),{})
        self.assertEqual(json.loads(final['checkpoint'])['checked_ids'],['old'])

    def test_latest_collection_error_survives_cooldown_messages(self):
        rid=self.app.start_all({'source_ids':[1]})['ids'][0]
        from dashboard.collectors import ProviderBlocked
        with patch('dashboard.worker.collect',side_effect=ProviderBlocked('LinkedIn search response status code 429')),patch('dashboard.worker.collection_wait',return_value=0):
            run(self.path,rid)
        self.app.processes[rid].code=0;self.app.reap()
        row=self.rows()[0]
        self.assertEqual(row['status'],'retry_wait')
        self.assertIn('429',json.loads(row['checkpoint'])['last_collection_error'])

    def test_selected_resume_rejects_finished_or_unknown_steps(self):
        result=self.app.start_all({'source_ids':[3]});rid=result['ids'][0]
        self.end(rid,status='completed')
        for selected in ([rid],[999999],[],['bad']):
            with self.assertRaises(ValueError):self.app.resume_workflow(result['workflow_id'],selected)
        self.assertEqual(self.rows()[0]['status'],'completed')

    def test_restart_resumes_durable_queue_and_checkpoint(self):
        ids=self.app.start_all({})['ids']
        with store.connect(self.path) as db:db.execute('UPDATE runs SET checkpoint=? WHERE id=?',(json.dumps({'offset':100}),ids[0]))
        self.app.processes.clear()
        restarted=App(self.path)
        restarted.reap()
        self.assertEqual(list(restarted.processes),[ids[0]])
        self.assertEqual(self.rows()[0]['attempts'],1)
        self.assertEqual(json.loads(self.rows()[0]['checkpoint'])['offset'],100)
        self.assertEqual([r['status'] for r in self.rows()],['running','queued','queued'])
        restarted.processes.clear()
    def test_explicit_stop_remains_stopped_after_restart_and_resume_skips_finished(self):
        result=self.app.start_all({});ids=result['ids']
        self.end(ids[0],status='completed')
        def stop(rid,status,message):
            self.app.processes.pop(rid)
            with store.connect(self.path) as db:db.execute('UPDATE runs SET status=? WHERE id=?',(status,rid))
        with patch.object(self.app,'stop',side_effect=stop):self.app.stop_all()
        restarted=App(self.path)
        self.assertEqual(restarted.snapshot()['workflows'][0]['status'],'stopped')
        self.assertEqual(restarted.snapshot()['workflows'][0]['status'],'stopped')
        result=restarted.resume_workflow()
        self.assertEqual(result['ids'],ids[1:]);self.assertNotIn(ids[0],restarted.processes)
        restarted.processes.clear()
    def test_rating_timeout_does_not_defer_ratings_on_next_search(self):
        ids=self.app.start_all({})['ids']
        self.end(ids[0],status='completed_with_warnings',rating_error='Luna rating timed out after 3 minutes.',error_kind='temporary')
        self.assertEqual(self.rows()[0]['status'],'retry_wait')
        self.assertNotIn('rating_paused',json.loads(self.rows()[1]['config']))
    def test_access_failure_defers_model_calls_but_preserves_collection(self):
        ids=self.app.start_all({})['ids']
        self.end(ids[0],status='completed_with_warnings',rating_error='Codex subscription limit reached.',error_kind='rating_access')
        rows=self.rows();self.assertEqual(rows[0]['status'],'needs_attention')
        self.assertEqual(rows[1]['status'],'running')
        self.assertIn('rating_paused',json.loads(rows[1]['config']))
    def test_resume_rating_failure_does_not_collect_completed_source_again(self):
        ids=self.app.start_all({'source_ids':[3]})['ids'];rid=ids[0]
        with store.connect(self.path) as db:
            store.upsert(db,{'id':'one','title':'Data','description':'SQL'})
            db.execute('INSERT INTO run_jobs VALUES(?,?)',(rid,'one'))
            workflows.remember(db,self.rows()[0]['workflow_id'],'one',True)
            db.execute("UPDATE runs SET collection_done=1,status='needs_attention' WHERE id=?",(rid,))
        self.app.processes.clear();self.app.resume_workflow()
        with patch('dashboard.worker.collect') as scrape,patch('dashboard.rating.review_batch',side_effect=self.review):run(self.path,rid)
        scrape.assert_not_called()
        self.assertEqual(self.rows()[0]['status'],'completed')
    def test_global_unique_totals_do_not_count_repeated_search_matches_or_retry_twice(self):
        result=self.app.start_all({'source_ids':[3]});rid=result['ids'][0]
        job={'id':'one','title':'Data','description':'SQL','url':'https://example.com/jobs/12345'}
        with patch('dashboard.worker.collect',return_value=iter([job,job])),patch('dashboard.rating.review_batch',side_effect=self.review):run(self.path,rid)
        self.end(rid,status='completed')
        snapshot=self.app.snapshot()['workflows'][0]
        self.assertEqual((snapshot['unique_jobs'],snapshot['added'],snapshot['ready']),(1,1,1))
        with store.connect(self.path) as db:db.execute("UPDATE runs SET status='running' WHERE id=?",(rid,))
        with patch('dashboard.worker.collect') as scrape,patch('dashboard.rating.review_batch') as model:run(self.path,rid)
        scrape.assert_not_called();model.assert_not_called()
        self.assertEqual(self.app.snapshot()['workflows'][0]['unique_jobs'],1)
        self.assertEqual(self.rows()[0]['found'],1)
    def test_rating_temporary_failure_retries_only_once_and_reuses_success(self):
        with store.connect(self.path) as db:store.upsert(db,{'id':'one','title':'Data','description':'SQL'})
        def respond(jobs,settings):return self.review(jobs,settings)
        with patch('dashboard.rating.review_batch',side_effect=[ValueError('Connection timed out'),{'one':self.review([{'id':'one'}],{})['one']}]) as model,patch('dashboard.rating.time.sleep'):
            self.assertEqual(rating.rate_jobs(self.path,['one'])['rated'],1)
            self.assertEqual(model.call_count,2)
        with patch('dashboard.rating.review_batch') as model:
            self.assertEqual(rating.rate_jobs(self.path,['one'])['cached'],1);model.assert_not_called()
    def test_resume_legacy_failed_sources_does_not_repeat_successful_sources(self):
        with store.connect(self.path) as db:
            for sid,status in [(1,'failed'),(2,'paused'),(3,'completed')]:
                source=db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
                db.execute('INSERT INTO runs(source_id,name,config,status,started_at) VALUES(?,?,?,?,?)',(sid,source['name'],source['config'],status,store.now()))
        result=self.app.resume_workflow()
        self.assertEqual([r['source_id'] for r in self.rows() if r['workflow_id']==result['workflow_id'] and r['source_id']],[1,2])

    def test_launch_failure_retries_three_times_then_needs_attention(self):
        with patch('dashboard.server.subprocess.Popen',side_effect=OSError('Cannot start')):
            rid=self.app.start(1)['id']
            self.assertEqual(self.rows()[0]['status'],'retry_wait')
            for _ in range(2):
                with store.connect(self.path) as db:db.execute("UPDATE runs SET retry_at='2000-01-01' WHERE id=?",(rid,))
                self.app.reap()
        self.assertEqual(self.rows()[0]['attempts'],3)
        self.assertEqual(self.rows()[0]['status'],'needs_attention')
    def test_shutdown_preserves_running_and_waiting_work_for_restart(self):
        ids=self.app.start_all({})['ids']
        def stop(rid,status,message):
            self.app.processes.pop(rid)
            with store.connect(self.path) as db:db.execute('UPDATE runs SET status=? WHERE id=?',(status,rid))
        with patch.object(self.app,'stop',side_effect=stop):self.app.suspend()
        self.assertEqual([r['status'] for r in self.rows()],['interrupted','queued','queued'])
        restarted=App(self.path);restarted.reap()
        self.assertEqual(list(restarted.processes),[ids[0]])
        restarted.processes.clear()
    def test_stop_cancels_delayed_retries_and_never_launches_them_later(self):
        ids=self.app.start_all({})['ids']
        self.end(ids[0],status='failed',provider_blocked=1,error_kind='provider_access')
        self.end(ids[2],status='completed')
        self.app.stop_all()
        with store.connect(self.path) as db:db.execute("UPDATE runs SET retry_at='2000-01-01'")
        self.app.reap()
        self.assertFalse(self.app.processes)
        self.assertEqual(self.app.snapshot()['workflows'][0]['status'],'stopped')
        self.assertEqual(self.app.snapshot()['workflows'][0]['finished'],1)

    def test_resume_specific_history_workflow_shows_its_progress_first(self):
        first=self.app.start_all({'source_ids':[3]})
        self.end(first['ids'][0],status='failed',error_kind='configuration')
        second=self.app.start_all({'source_ids':[1]})
        self.end(second['ids'][0],status='completed')
        result=self.app.resume_workflow(first['workflow_id'])
        self.assertEqual(result['ids'],first['ids'])
        self.assertEqual(self.app.snapshot()['workflows'][0]['id'],first['workflow_id'])

    def test_existing_jobs_queued_for_rating_are_not_counted_as_refetched(self):
        with store.connect(self.path) as db:store.upsert(db,{'id':'existing','title':'Data','description':'SQL'})
        result=self.app.start_workflow()
        summary=self.app.snapshot()['workflows'][0]
        self.assertEqual((summary['unique_jobs'],summary['refreshed']),(1,0))
        with store.connect(self.path) as db:workflows.remember(db,result['workflow_id'],'existing',collected=True)
        self.assertEqual(self.app.snapshot()['workflows'][0]['refreshed'],1)


class CheckpointTests(unittest.TestCase):
    def test_search_denial_keeps_raw_status_and_saved_page_checkpoint(self):
        saved=[]
        def scrape(**kw):
            logger=logging.getLogger('JobSpy:LinkedIn');logger.setLevel(logging.ERROR)
            logger.error('LinkedIn response status code 429')
            return SimpleNamespace(to_dict=lambda **_: [{'title':'Data','job_url':'https://example.org/jobs/123456'}])
        with patch.dict('sys.modules',{'jobspy':SimpleNamespace(scrape_jobs=scrape)}):
            iterator=collect({'provider':'linkedin','keywords':'Data','location':'Ireland'},save_checkpoint=saved.append)
            self.assertEqual(next(iterator)['title'],'Data')
            with self.assertRaisesRegex(RuntimeError,'search denied.*429'):next(iterator)
        self.assertEqual(saved[-1]['offset'],0);self.assertEqual(saved[-1]['found'],1)
        self.assertEqual(len(saved[-1]['seen']),1)
    def test_description_denial_switches_later_pages_to_metadata_without_stopping_search(self):
        calls=[]
        def scrape(**kw):
            calls.append(kw)
            if kw['offset']==0:
                logger=logging.getLogger('JobSpy:LinkedIn');logger.setLevel(logging.WARNING)
                logger.warning('LinkedIn response status code 429 for job 123')
            count=10 if kw['offset']==0 else 1
            return SimpleNamespace(to_dict=lambda **_: [{'title':'Data','job_url':f'https://example.org/jobs/{kw["offset"]+i}'} for i in range(count)])
        with patch.dict('sys.modules',{'jobspy':SimpleNamespace(scrape_jobs=scrape)}),patch('dashboard.collectors.time.sleep'):
            jobs=list(collect({'provider':'linkedin','keywords':'Data','location':'Ireland'}))
        self.assertEqual(len(jobs),11)
        self.assertTrue(calls[0]['linkedin_fetch_description']);self.assertFalse(calls[1]['linkedin_fetch_description'])

    def test_resumed_description_denial_does_not_repeat_detail_requests(self):
        calls=[]
        def scrape(**kw):
            calls.append(kw)
            return SimpleNamespace(to_dict=lambda **_: [])
        checkpoint={'offset':10,'found':10,'pages':1,'scanned':10,'seen':[],
                    'description_failures':2,'description_deferred':True}
        with patch.dict('sys.modules',{'jobspy':SimpleNamespace(scrape_jobs=scrape)}):
            list(collect({'provider':'linkedin','keywords':'Data','location':'Ireland'},checkpoint=checkpoint))
        self.assertFalse(calls[0]['linkedin_fetch_description'])
        self.assertEqual(calls[0]['offset'],10)

    def test_mid_page_timeout_resumes_last_fully_saved_batch(self):
        calls=[];saved=[]
        def scrape(**kwargs):
            calls.append(kwargs['offset'])
            if len(calls)==2:raise TimeoutError('Connection timed out')
            n=100 if kwargs['offset']==0 else 2
            rows=[{'title':'Data','job_url':f'https://example.com/jobs/{kwargs["offset"]+i}'} for i in range(n)]
            return SimpleNamespace(to_dict=lambda **_:rows)
        config={'provider':'indeed','keywords':'Data','location':'Ireland'}
        with patch.dict('sys.modules',{'jobspy':SimpleNamespace(scrape_jobs=scrape)}),patch('dashboard.collectors.time.sleep'):
            with self.assertRaises(TimeoutError):list(collect(config,save_checkpoint=saved.append))
            resumed=list(collect(config,checkpoint=saved[-1]))
        self.assertEqual(calls,[0,100,100]);self.assertEqual(len(resumed),2)
        self.assertEqual(saved[-1]['found'],100)

class DetailGuardTests(unittest.TestCase):
    def test_first_denial_stops_other_detail_requests_and_restores_adapter(self):
        from dashboard.collectors import description_guard,ProviderMessages
        calls=[];messages=ProviderMessages()
        class Adapter:
            def _fetch_details(self,jid):
                calls.append(jid)
                messages.messages.append((logging.WARNING,'LinkedIn response status code 429 for job '+jid))
                return {}
        original=Adapter._fetch_details
        with patch.dict('sys.modules',{'jobspy.linkedin':SimpleNamespace(LinkedIn=Adapter)}):
            with description_guard(messages,True):
                a=Adapter();a._fetch_details('1');a._fetch_details('2');a._fetch_details('3')
        self.assertEqual(calls,['1']);self.assertIs(Adapter._fetch_details,original)
    def test_successful_detail_requests_are_paced_and_exception_restores_adapter(self):
        from dashboard.collectors import description_guard,ProviderMessages
        class Adapter:
            def _fetch_details(self,jid):
                if jid=='2':raise ValueError('Parser error')
                return {'description':'SQL'}
        original=Adapter._fetch_details
        with patch.dict('sys.modules',{'jobspy.linkedin':SimpleNamespace(LinkedIn=Adapter)}),patch('dashboard.collectors.time.monotonic',return_value=0),patch('dashboard.collectors.time.sleep') as sleep:
            with self.assertRaises(ValueError):
                with description_guard(ProviderMessages(),True):
                    a=Adapter();a._fetch_details('1');a._fetch_details('2')
            sleep.assert_called_once_with(2)
        self.assertIs(Adapter._fetch_details,original)
