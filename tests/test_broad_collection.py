"""Broader ingestion: pagination, provider boundaries and independent ratings."""
import json
import logging
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from dashboard import store, rating
from dashboard.collectors import collect, validate_config
from dashboard.worker import run
import test_sources as source_tests
import test_rating as rating_tests
FakeProcess = source_tests.FakeProcess


class BroadSourceTests(unittest.TestCase):
    config = source_tests.SourceTests.config

    def test_all_results_and_any_posting_date_are_defaults_and_large_limits_are_allowed(self):
        config = validate_config({'keywords':'Data', 'location':'Ireland'})
        self.assertIsNone(config['limit'])
        self.assertIsNone(config['hours_old'])
        self.assertEqual(self.config(limit=5000, hours_old=10000)['limit'], 5000)
        for invalid in (0, -1, 1.5, True, 'abc'):
            with self.assertRaises(ValueError):
                self.config(limit=invalid)

    def test_job_board_pages_past_100_and_deduplicates_overlapping_pages(self):
        calls, reports = [], []
        def scrape(**kwargs):
            calls.append(kwargs)
            offset = kwargs['offset']
            # The second page overlaps the first; a final page exposes 20 more.
            rows = [{'id':str(i),'title':'Data Analyst','job_url':'https://example.com/'+str(i)}
                    for i in (range(0,100) if offset==0 else range(90,190) if offset==100 else range(190,210))]
            return SimpleNamespace(to_dict=lambda **kw: rows)
        with patch.dict(sys.modules, {'jobspy':SimpleNamespace(scrape_jobs=scrape)}), patch('dashboard.collectors.time.sleep'):
            jobs = list(collect(self.config(provider='indeed', limit=None, hours_old=None), lambda *r: reports.append(r)))
        self.assertEqual(len(jobs),210)
        self.assertEqual([c['offset'] for c in calls], [0,100,200])
        self.assertTrue(all(c['hours_old'] is None for c in calls))
        self.assertEqual(reports[-1][:2], (3,220))
        self.assertIn('partial batch', reports[-1][2])

    def test_repeated_pages_terminate_and_explicit_errors_keep_partial_results(self):
        rows=[{'id':str(i),'title':'Analyst','job_url':'https://example.com/'+str(i)} for i in range(10)]
        reports=[]
        with patch.dict(sys.modules, {'jobspy':SimpleNamespace(scrape_jobs=lambda **kw: SimpleNamespace(to_dict=lambda **kw:rows))}), patch('dashboard.collectors.time.sleep'):
            self.assertEqual(len(list(collect(self.config(provider='linkedin',limit=None), lambda *r:reports.append(r)))),10)
        self.assertIn('repeated', reports[-1][2])
        def broken(**kw):
            logger=logging.getLogger('JobSpy:Indeed'); logger.setLevel(logging.ERROR)
            logger.error('Indeed response status code 500')
            return SimpleNamespace(to_dict=lambda **kw:rows[:1])
        with patch.dict(sys.modules, {'jobspy':SimpleNamespace(scrape_jobs=broken)}):
            iterator=collect(self.config(provider='indeed',limit=None))
            self.assertEqual(next(iterator)['title'],'Analyst')
            with self.assertRaisesRegex(RuntimeError, '500'):
                next(iterator)

    def test_optional_cap_counts_matches_after_remote_filter(self):
        def scrape(**kw):
            start=kw['offset']
            rows=[{'id':str(i),'title':('Remote ' if i%2 else 'Onsite ')+'Engineer',
                   'job_url':'https://example.com/'+str(i)} for i in range(start,start+10)]
            return SimpleNamespace(to_dict=lambda **kw:rows)
        with patch.dict(sys.modules, {'jobspy':SimpleNamespace(scrape_jobs=scrape)}), patch('dashboard.collectors.time.sleep'):
            jobs=list(collect(self.config(provider='linkedin',remote=True,limit=12)))
        self.assertEqual(len(jobs),12)
        self.assertTrue(all(j['work_model']=='Remote' for j in jobs))
        self.assertTrue(any(j['url'].endswith('/23') for j in jobs))

    def test_lever_scans_beyond_previous_2000_ceiling(self):
        def read(url):
            from urllib.parse import urlsplit,parse_qs
            skip=int(parse_qs(urlsplit(url).query)['skip'][0])
            n=100 if skip<2100 else 1
            return [{'id':str(skip+i),'text':'Data Analyst','categories':{'location':'Dublin'},
                     'hostedUrl':'https://example.com/'+str(skip+i)} for i in range(n)]
        with patch('dashboard.ats.read_json', side_effect=read):
            jobs=list(collect(self.config(provider='lever',limit=None)))
        self.assertEqual(len(jobs),2101)


class BroadWorkflowTests(unittest.TestCase):
    setUp = source_tests.WorkflowTests.setUp
    tearDown = source_tests.WorkflowTests.tearDown
    finish = source_tests.WorkflowTests.finish

    def test_large_queue_shows_all_active_work_and_overrides_can_raise_or_remove_cap(self):
        for n in range(103):
            self.app.mutate('/api/sources', {'name':str(n),'config':{'keywords':'Data','location':'Ireland'}})
        with patch('dashboard.server.subprocess.Popen',side_effect=lambda *a,**kw:FakeProcess()), patch('dashboard.server.rating.cli_status',return_value={'ready':True}):
            ids=self.app.start_all({'limit':None})['ids']
            snapshot=self.app.snapshot()
        self.assertEqual(len(ids),106)
        self.assertEqual(len(snapshot['runs']),106)
        self.assertEqual(sum(r['status']=='running' for r in snapshot['runs']),1)
        self.assertTrue(all(json.loads(r['config'])['limit'] is None for r in snapshot['runs']))
        self.app.processes.clear()
        with store.connect(self.path) as db:
            db.execute("UPDATE runs SET status='cancelled'")
        with patch('dashboard.server.subprocess.Popen',return_value=FakeProcess()):
            self.app.start_all({'limit':5000})
        with store.connect(self.path) as db:
            self.assertTrue(all(json.loads(r['config'])['limit']==5000 for r in db.execute("SELECT config FROM runs WHERE status IN ('running','queued')")))

    def test_denied_platform_cools_down_while_other_platforms_continue(self):
        self.app.mutate('/api/sources/3', {'name':'Indeed', 'config':{'provider':'indeed','keywords':'Data','location':'Ireland'}})
        with patch('dashboard.server.subprocess.Popen',side_effect=lambda *a,**kw:FakeProcess()):
            ids=self.app.start_all({})['ids']
            self.finish(ids[0])
            with store.connect(self.path) as db:
                db.execute("UPDATE runs SET status='failed',provider_blocked=1,error_kind='provider_access' WHERE id=?",(ids[0],))
            self.app.reap()
        with store.connect(self.path) as db:
            statuses=[r[0] for r in db.execute('SELECT status FROM runs ORDER BY id')]
        self.assertEqual(statuses,['retry_wait','retry_wait','running'])
        self.assertEqual(list(self.app.processes),[ids[2]])

    def test_progressing_work_has_no_elapsed_deadline_but_stalled_work_stops(self):
        with patch('dashboard.server.subprocess.Popen',return_value=FakeProcess()), patch('dashboard.server.time.monotonic',return_value=0):
            rid=self.app.start_all({'source_ids':[1]})['ids'][0]
            self.app.reap()
        with store.connect(self.path) as db:
            db.execute("UPDATE runs SET heartbeat='new progress' WHERE id=?",(rid,))
        with patch('dashboard.server.time.monotonic',return_value=4000), patch.object(self.app,'stop') as stop:
            self.app.reap(); stop.assert_not_called()
        with patch('dashboard.server.time.monotonic',return_value=4601), patch.object(self.app,'stop') as stop:
            self.app.reap(); stop.assert_called_once()
            self.assertIn('without progress',stop.call_args.args[2])

    def test_retry_ratings_includes_more_than_100_waiting_jobs(self):
        with store.connect(self.path) as db:
            for n in range(125):
                store.upsert(db, {'id':str(n),'title':'Data'})
        with patch('dashboard.server.subprocess.Popen',return_value=FakeProcess()):
            rid=self.app.start_ratings()['id']
        with store.connect(self.path) as db:
            config=json.loads(db.execute('SELECT config FROM runs WHERE id=?',(rid,)).fetchone()[0])
        self.assertEqual(len(config['job_ids']),125)


class BroadRatingTests(unittest.TestCase):
    setUp = rating_tests.AutomaticRatingTests.setUp
    tearDown = rating_tests.AutomaticRatingTests.tearDown
    new_run = rating_tests.AutomaticRatingTests.new_run
    reviews = rating_tests.AutomaticRatingTests.reviews

    def test_deferred_ratings_keep_new_jobs_without_more_model_calls_and_reuse_cache(self):
        with store.connect(self.path) as db:
            cached=store.upsert(db,{'id':'cached','title':'Analyst','description':'Python'})[0]
        with patch('dashboard.rating.review_batch',side_effect=self.reviews):
            rating.rate_jobs(self.path,[cached])
        rid=self.new_run()
        with store.connect(self.path) as db:
            config=json.loads(db.execute('SELECT config FROM runs WHERE id=?',(rid,)).fetchone()[0])
            config['rating_paused']='Subscription limit reached'
            db.execute('UPDATE runs SET config=? WHERE id=?',(json.dumps(config),rid))
        jobs=[{'id':'cached','title':'Analyst','description':'Python'}, {'id':'new','title':'Engineer','description':'SQL'}]
        with patch('dashboard.worker.collect',return_value=iter(jobs)), patch('dashboard.rating.review_batch') as model:
            run(self.path,rid); model.assert_not_called()
        with store.connect(self.path) as db:
            result=db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            statuses={r['id']:r['rating_status'] for r in db.execute('SELECT id,rating_status FROM jobs')}
        self.assertEqual(result['found'],2)
        self.assertEqual(result['cached'],1)
        self.assertEqual(result['unrated'],1)
        self.assertEqual(statuses,{'cached':'complete','new':'blocked'})
