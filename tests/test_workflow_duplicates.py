"""One user action, conservative posting identity, and retained tracking."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dashboard import store, rating
from dashboard.server import App
from dashboard.worker import run
from dashboard.duplicates import posting_link
import test_sources as source_tests


class WorkflowDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'jobs.db'
        self.app=App(self.path)
        self.app.mutate('/api/settings', {'cv_text':'Python, SQL and data operations.'})
        self.ready=patch('dashboard.rating.cli_status', return_value={'ready':True})
        self.ready.start()

    def tearDown(self):
        self.app.processes.clear()
        self.ready.stop()
        self.temp.cleanup()

    def job(self, native, provider='indeed', direct='https://careers.example.com/jobs/123456'):
        return {'title':'Data Engineer','company':'Example','location':'Dublin, Ireland',
                'description':'Python and SQL. '+('Work on data pipelines. '*15),
                'url':'https://www.indeed.com/viewjob?jk='+native if provider=='indeed' else 'https://www.linkedin.com/jobs/view/'+native+'/',
                'direct_url':direct, 'source':provider}

    def reviews(self, jobs, settings):
        return {j['id']:{'score':8,'summary':'Python match.','strengths':['SQL'],'gaps':[]} for j in jobs}

    def test_tracking_urls_and_regional_board_links_identify_the_same_posting(self):
        self.assertEqual(store.canonical_url('https://ie.indeed.com/viewjob?from=search&jk=abc123&utm_source=test'),
                         store.canonical_url('https://www.indeed.com/viewjob?jk=abc123'))
        self.assertEqual(store.canonical_url('https://www.glassdoor.ie/job-listing/analyst.htm?jl=123&src=GD_JOB'),
                         store.canonical_url('https://www.glassdoor.com/partner/jobListing.htm?jobListingId=123'))
        self.assertEqual(store.canonical_url('https://boards.greenhouse.io/example/jobs/123?utm_source=linkedin'),
                         store.canonical_url('https://job-boards.greenhouse.io/example/jobs/123'))
        self.assertEqual(store.canonical_url('https://jobs.lever.co/example/12345678-1234-1234-1234-123456789abc/apply?utm_source=indeed'),
                         store.canonical_url('https://jobs.lever.co/example/12345678-1234-1234-1234-123456789abc'))
        self.assertNotEqual(store.canonical_url('https://careers.example.com/job?id=111&utm_source=x'),
                            store.canonical_url('https://careers.example.com/job?id=222&utm_source=x'))

    def test_same_post_on_two_platforms_keeps_one_rating_all_links_and_tracking(self):
        with store.connect(self.path) as db:
            first,_=store.upsert(db,self.job('abc123')|{'stage':'applied','notes':'Keep interview prep'},tracking=True)
        with patch('dashboard.rating.review_batch',side_effect=self.reviews) as model:
            rating.rate_jobs(self.path,[first])
            with store.connect(self.path) as db:
                second,created=store.upsert(db,self.job('123456','linkedin',direct='https://careers.example.com/jobs/123456?utm_source=linkedin'))
            self.assertFalse(created)
            self.assertEqual(first,second)
            result=rating.rate_jobs(self.path,[first,second])
            self.assertEqual(model.call_count,1)
            self.assertEqual(result,{'rated':0,'cached':1,'unrated':0})
        jobs=self.app.snapshot()['jobs']
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['stage'],'applied')
        self.assertEqual(jobs[0]['notes'],'Keep interview prep')
        self.assertEqual({l['source'] for l in jobs[0]['listings']},{'indeed','linkedin'})
        self.assertEqual(jobs[0]['source'],'indeed')

    def test_same_titles_different_requisitions_and_generic_careers_links_stay_separate(self):
        with store.connect(self.path) as db:
            store.upsert(db,self.job('one',direct='https://careers.example.com/jobs/123456'))
            store.upsert(db,self.job('two',direct='https://careers.example.com/jobs/234567'))
            store.upsert(db,self.job('three',direct='https://careers.example.com/jobs'))
            store.upsert(db,self.job('four',direct='https://careers.example.com/jobs'))
            self.assertEqual(store.reconcile_duplicates(db),0)
        jobs=self.app.snapshot()['jobs']
        self.assertEqual(len(jobs),4)
        self.assertTrue(all(len(j['similar_jobs'])==3 for j in jobs))
        for link in ('https://example.com/careers','https://example.com/jobs/search','https://example.com/careers/2026','https://example.com/jobs/category/engineering-2026'):
            self.assertFalse(posting_link(link))

    def test_reconciliation_retains_originals_and_selects_the_tracked_record(self):
        with store.connect(self.path) as db:
            old,_=store.upsert(db,self.job('old'),tracking=True)
            tracked,_=store.upsert(db,self.job('123456','linkedin')|{'stage':'interview','notes':'Important note'},tracking=True)
            self.assertEqual(store.reconcile_duplicates(db),1)
            self.assertEqual(store.reconcile_duplicates(db),0)
            self.assertEqual(db.execute('SELECT duplicate_of FROM jobs WHERE id=?',(old,)).fetchone()[0],tracked)
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],2)
        snapshot=self.app.snapshot()
        self.assertEqual(snapshot['duplicates'],1)
        self.assertEqual(len(snapshot['jobs']),1)
        self.assertEqual(snapshot['jobs'][0]['notes'],'Important note')
        self.assertEqual(snapshot['jobs'][0]['stage'],'interview')

    def test_separately_tracked_applications_are_never_hidden(self):
        with store.connect(self.path) as db:
            store.upsert(db,self.job('first')|{'stage':'applied','notes':'First application'},tracking=True)
            store.upsert(db,self.job('123456','linkedin')|{'stage':'interview','notes':'Second application'},tracking=True)
            self.assertEqual(store.reconcile_duplicates(db),0)
        self.assertEqual(len(self.app.snapshot()['jobs']),2)

    def test_one_button_completes_existing_ratings_all_sources_and_skips_cross_search_duplicates(self):
        for provider in ('indeed','linkedin'):
            self.app.mutate('/api/sources', {'name':provider,'config':{'provider':provider,'keywords':'Data','location':'Ireland'}})
        with store.connect(self.path) as db:
            original,_=store.upsert(db,self.job('existing'))
        other=self.job('new',direct='https://careers.example.com/jobs/234567')
        cross=other|{'source':'linkedin','url':'https://www.linkedin.com/jobs/view/234567/'}
        final=self.job('final',direct='https://careers.example.com/jobs/345678')
        with patch('dashboard.server.subprocess.Popen',side_effect=lambda *a,**k:source_tests.FakeProcess()) as launch, patch('dashboard.rating.review_batch',side_effect=self.reviews) as model, patch('dashboard.worker.collection_wait',return_value=0):
            result=self.app.mutate('/api/workflow/start',{})
            repeated=self.app.mutate('/api/workflow/start',{})
            self.assertTrue(repeated['active'])
            self.assertEqual(len(result['ids']),3)
            self.assertEqual(launch.call_count,1)
            for index,rid in enumerate(result['ids']):
                jobs=[] if index==0 else [self.job('existing'),other] if index==1 else [cross,final]
                with patch('dashboard.worker.collect',return_value=iter(jobs)) as collect:
                    run(self.path,rid)
                    if index==0:collect.assert_not_called()
                    else:collect.assert_called_once()
                self.app.processes[rid].code=0
                self.app.reap()
            self.assertFalse(self.app.processes)
            self.assertEqual(model.call_count,3)
            self.assertEqual(launch.call_count,3)
        snapshot=self.app.snapshot()
        self.assertEqual(len(snapshot['jobs']),3)
        self.assertTrue(all(j['rating_status']=='complete' for j in snapshot['jobs']))
        self.assertTrue(all(r['status']=='completed' for r in snapshot['runs']))
        self.assertEqual(sum(r['cached'] for r in snapshot['runs']),2)

    def test_workflow_can_rate_existing_jobs_without_any_saved_search(self):
        with store.connect(self.path) as db:
            store.upsert(db,self.job('existing'))
        with patch('dashboard.server.subprocess.Popen',return_value=source_tests.FakeProcess()):
            ids=self.app.start_workflow()['ids']
        self.assertEqual(len(ids),1)
        with store.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT kind FROM runs WHERE id=?',(ids[0],)).fetchone()[0],'rating')

    def test_backup_roundtrip_keeps_all_source_links_and_tracking(self):
        with store.connect(self.path) as db:
            store.upsert(db,self.job('first')|{'stage':'applied','notes':'Keep me'},tracking=True)
            store.upsert(db,self.job('123456','linkedin'))
            payload={'jobs':[dict(r) for r in db.execute('SELECT * FROM jobs')], 'links':[dict(r) for r in db.execute('SELECT * FROM job_links')]}
        restored=App(Path(self.temp.name)/'restored.db')
        restored.mutate('/api/import',payload)
        job=restored.snapshot()['jobs'][0]
        self.assertEqual(job['stage'],'applied')
        self.assertEqual(job['notes'],'Keep me')
        self.assertEqual({l['source'] for l in job['listings']},{'indeed','linkedin'})
        bad=App(Path(self.temp.name)/'bad.db')
        with self.assertRaises(ValueError):
            bad.mutate('/api/import',payload|{'links':[{'url':'javascript:bad','job_id':job['id']}]})
        self.assertFalse(bad.snapshot()['jobs'])
