import json
import tempfile
import unittest
import logging
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from dashboard import store
from dashboard.ats import collect_ats, greenhouse_job, lever_job
from dashboard.collectors import collect, normalize, validate_config
from dashboard.locations import classify_location
from dashboard.server import App
from dashboard.worker import collection_wait


class SourceTests(unittest.TestCase):
    def config(self, **kw):
        return validate_config({'provider': 'greenhouse', 'keywords': 'data engineer | data analyst',
                                'location': 'Ireland', 'scope': 'ireland_remote',
                                'board': 'example', 'company': 'Example', 'limit': 2, **kw})

    def test_existing_search_defaults_to_linkedin_and_country_is_preserved(self):
        self.assertEqual(validate_config({'keywords': 'Analyst', 'location': 'Dublin'})['provider'], 'linkedin')
        self.assertEqual(self.config(provider='indeed', country='Ireland')['country'], 'Ireland')
        self.assertEqual(normalize({'title': 'Analyst', 'site': 'indeed'}, 'linkedin')['source'], 'indeed')

    def test_careers_url_parsing_rejects_arbitrary_hosts_and_paths(self):
        self.assertEqual(self.config(board='https://job-boards.greenhouse.io/intercom')['board'], 'intercom')
        lever = self.config(provider='lever', board='https://jobs.eu.lever.co/example')
        self.assertEqual(lever['region'], 'eu')
        for value in ('https://127.0.0.1/private', '../../secret', 'https://evil.example/board', 'https://user:pass@jobs.lever.co/example'):
            with self.assertRaises(ValueError):
                self.config(board=value)

    def test_greenhouse_description_is_complete_and_update_is_not_a_posting_date(self):
        row = {'id': 1, 'title': 'Data Engineer', 'absolute_url': 'https://job-boards.greenhouse.io/example/jobs/1',
               'location': {'name': 'Dublin'}, 'updated_at': '2026-10-13',
               'content': '&lt;p&gt;Python &amp; SQL&lt;/p&gt;&lt;ul&gt;&lt;li&gt;APIs&lt;/li&gt;&lt;/ul&gt;<script>hidden code</script>',
               'departments': [{'name': 'Data'}]}
        job = greenhouse_job(row, self.config())
        self.assertIn('Python & SQL', job['description'])
        self.assertIn('- APIs', job['description'])
        self.assertNotIn('hidden code', job['description'])
        self.assertEqual(job['posted_date'], '')
        self.assertEqual(job['job_function'], 'Data')

    def test_lever_keeps_all_description_sections_locations_and_salary_interval(self):
        row = {'id': 'posting', 'text': 'Data Analyst', 'categories': {'location': 'London', 'allLocations': ['London', 'Dublin'], 'commitment': 'Full Time'},
               'descriptionPlain': 'Opening', 'lists': [{'text': 'Requirements', 'content': '<li>SQL</li>'}],
               'additionalPlain': 'Closing', 'workplaceType': 'hybrid', 'salaryRange': {'min': 30, 'max': 40, 'currency': 'EUR', 'interval': 'hourly'},
               'hostedUrl': 'https://jobs.lever.co/example/posting', 'applyUrl': 'https://jobs.lever.co/example/posting/apply'}
        job = lever_job(row, self.config(provider='lever'))
        for term in ('Opening', 'Requirements', 'SQL', 'Closing'):
            self.assertIn(term, job['description'])
        self.assertEqual(classify_location(job)['location_status'], 'ireland')
        self.assertEqual(job['salary_min'], 30)
        self.assertEqual(job['salary_interval'], 'hourly')
        self.assertEqual(job['work_model'], 'Hybrid')

    def test_employer_feed_filters_title_and_location_before_rating(self):
        def row(jid, title, location):
            return {'id': jid, 'title': title, 'location': {'name': location}, 'content': '<p>SQL</p>', 'absolute_url': 'https://example.com/'+str(jid)}
        rows = [row(1, 'Data Engineer', 'New York'), row(2, 'Account Executive', 'Dublin'),
                row(3, 'Senior Data Analyst', 'Dublin'), row(4, 'Data Engineer', 'Remote - Europe')]
        with patch('dashboard.ats.read_json', return_value={'jobs': rows}):
            jobs = list(collect(self.config()))
        self.assertEqual([j['id'] for j in jobs], ['greenhouse:example:3', 'greenhouse:example:4'])

    def test_lever_paginates_until_matching_jobs_are_found(self):
        def row(jid, location):
            return {'id': str(jid), 'text': 'Data Analyst', 'categories': {'location': location}, 'hostedUrl': 'https://example.com/'+str(jid)}
        with patch('dashboard.ats.read_json', side_effect=[[row(n, 'New York') for n in range(100)], [row(101, 'Dublin')]]) as read:
            jobs = list(collect_ats(self.config(provider='lever')))
        self.assertEqual(len(jobs), 1)
        self.assertIn('skip=100', read.call_args_list[1].args[0])

    def test_remote_or_europe_does_not_imply_ireland_eligibility(self):
        cases = [('Dublin, Ireland', 'Hybrid', 'ireland'), ('Northern Ireland', 'On-site', 'outside_target'),
                 ('Remote - United States', 'Remote', 'outside_target'), ('Berlin', 'Remote', 'outside_target'),
                 ('Europe', 'On-site', 'outside_target'), ('EMEA', 'Remote', 'remote_check'), ('Remote', 'Remote', 'unclear')]
        for location, model, expected in cases:
            self.assertEqual(classify_location({'location': location, 'work_model': model})['location_status'], expected)

    def test_linkedin_remote_filter_does_not_return_onsite_results(self):
        rows = [{'title': 'Onsite Data Engineer', 'job_type': 'contract'},
                {'title': 'Remote Data Engineer', 'job_type': 'contract'},
                {'title': 'Remote Data Engineer', 'job_type': 'fulltime'}]
        def scrape(**kwargs):
            self.assertIn('remote', kwargs['search_term'])
            return SimpleNamespace(to_dict=lambda **kw: rows)
        with patch.dict(sys.modules, {'jobspy': SimpleNamespace(scrape_jobs=scrape)}):
            jobs = list(collect(self.config(provider='linkedin', remote=True, job_type='contract', limit=3)))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['work_model'], 'Remote')

    def test_provider_blocking_is_reported_after_partial_results_are_saved(self):
        def scrape(**kwargs):
            logger=logging.getLogger('JobSpy:LinkedIn');logger.setLevel(logging.WARNING)
            logger.warning('LinkedIn response status code 429 for job 123')
            return SimpleNamespace(to_dict=lambda **kw: [{'title': 'Analyst'}])
        with patch.dict(sys.modules, {'jobspy': SimpleNamespace(scrape_jobs=scrape)}):
            iterator = collect(self.config(provider='linkedin'))
            self.assertEqual(next(iterator)['title'], 'Analyst')
            with self.assertRaisesRegex(RuntimeError, 'rate-limited'):
                next(iterator)


class FakeProcess:
    def __init__(self):
        self.code = None
    def poll(self):
        return self.code


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'jobs.db'
        self.app = App(self.path)
        for name in ('One', 'Two', 'Three'):
            self.app.mutate('/api/sources', {'name': name, 'config': {'keywords': 'Analyst', 'location': 'Dublin', 'limit': 5}})

    def tearDown(self):
        self.app.processes.clear()
        self.temp.cleanup()

    def finish(self, rid, rating_error=''):
        with store.connect(self.path) as db:
            db.execute("UPDATE runs SET status='completed',rating_error=? WHERE id=?", (rating_error, rid))
        self.app.processes[rid].code = 0

    def test_run_all_advances_without_chat_or_browser_and_snapshots_limits(self):
        with patch('dashboard.server.subprocess.Popen', side_effect=lambda *a, **k: FakeProcess()) as launch:
            ids = self.app.start_all({'limit': 2})['ids']
            self.assertEqual(len(self.app.processes), 1)
            with store.connect(self.path) as db:
                runs = list(db.execute('SELECT * FROM runs ORDER BY id'))
            self.assertEqual([r['status'] for r in runs], ['running', 'queued', 'queued'])
            self.assertTrue(all(json.loads(r['config'])['limit'] == 2 for r in runs))
            self.finish(ids[0]); self.app.reap()
            self.assertEqual(list(self.app.processes), [ids[1]])
            self.finish(ids[1]); self.app.reap()
            self.finish(ids[2]); self.app.reap()
            self.assertEqual(launch.call_count, 3)
            self.assertFalse(self.app.processes)
        with store.connect(self.path) as db:
            self.assertTrue(all(json.loads(r['config'])['limit'] == 5 for r in db.execute('SELECT config FROM sources')))

    def test_stop_workflow_cancels_waiting_runs_before_terminating_active_worker(self):
        with patch('dashboard.server.subprocess.Popen', return_value=FakeProcess()):
            ids = self.app.start_all({})['ids']
        # Stand in for the already-tested OS process-tree termination.
        def stop(rid, status, message):
            self.app.processes.pop(rid, None)
            with store.connect(self.path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM runs WHERE status='queued'").fetchone()[0], 0)
                db.execute('UPDATE runs SET status=? WHERE id=?', (status, rid))
        with patch.object(self.app, 'stop', side_effect=stop):
            self.app.stop_all()
        with store.connect(self.path) as db:
            self.assertTrue(all(r['status'] == 'cancelled' for r in db.execute('SELECT status FROM runs')))

    def test_model_failure_stops_later_queue_requests(self):
        with patch('dashboard.server.subprocess.Popen', return_value=FakeProcess()) as launch:
            ids = self.app.start_all({})['ids']
            self.finish(ids[0], 'Subscription limit reached')
            self.app.reap()
            self.assertEqual(launch.call_count, 1)
        with store.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs WHERE status='cancelled'").fetchone()[0], 2)

    def test_restart_does_not_silently_resume_a_queue(self):
        with store.connect(self.path) as db:
            db.execute("INSERT INTO runs(name,config,status,started_at) VALUES('Queued','{}','queued',?)", (store.now(),))
        App(self.path)
        with store.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM runs').fetchone()[0], 'interrupted')

    def test_queue_selection_and_limits_are_validated_atomically(self):
        for data in ({'source_ids': [999]}, {'source_ids': ['1']}, {'limit': 0}, {'limit': True}):
            with self.assertRaises((ValueError, LookupError)):
                self.app.start_all(data)
        with store.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_linkedin_spacing_and_existing_location_backfill(self):
        with store.connect(self.path) as db:
            db.execute("INSERT INTO runs(name,config,status,started_at,finished_at) VALUES('Previous','{}','completed',?,?)", (store.now(),store.now()))
            rid=db.execute("INSERT INTO runs(name,config,status,started_at) VALUES('Next','{}','running',?)", (store.now(),)).lastrowid
            jid,_=store.upsert(db, {'title':'Analyst','location':'Dublin','notes':'Keep me','stage':'applied'},tracking=True)
        self.assertGreater(collection_wait(self.path, {'provider':'linkedin'},rid), 25)
        self.assertEqual(collection_wait(self.path, {'provider':'indeed'},rid), 0)
        store.init(self.path)
        with store.connect(self.path) as db:
            job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(job['location_status'],'ireland')
        self.assertEqual(job['notes'],'Keep me')
        self.assertEqual(job['stage'],'applied')
