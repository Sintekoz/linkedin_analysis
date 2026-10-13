import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dashboard import rating, store
from dashboard.server import App
from dashboard.worker import run


class AutomaticRatingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'jobs.db'
        self.app = App(self.path)
        self.app.mutate('/api/settings', {'cv_text': 'Four years of Python and SQL.', 'target_role': 'Data Analyst'})
        self.ready = patch('dashboard.rating.cli_status', return_value={'ready': True, 'model': rating.MODEL})
        self.ready.start()
        self.spacing = patch('dashboard.worker.collection_wait', return_value=0)
        self.spacing.start()  # Timing is covered separately; these tests exercise rating behavior.

    def tearDown(self):
        self.ready.stop()
        self.spacing.stop()
        self.temp.cleanup()

    def new_run(self):
        with store.connect(self.path) as db:
            return db.execute("INSERT INTO runs(name,config,status,started_at) VALUES('Test',?,'running',?)",
                              (json.dumps({'keywords': 'Analyst', 'location': 'Dublin'}), store.now())).lastrowid

    def reviews(self, jobs, _):
        return {j['id']: {'score': 8, 'summary': 'Strong Python match.', 'strengths': ['Python'], 'gaps': ['Seniority not provided']} for j in jobs}

    def jobs(self):
        return self.app.snapshot()['jobs']

    def test_collection_rates_without_per_job_actions_and_reuses_unchanged_scores(self):
        jobs = [{'title': 'Analyst', 'description': 'Python SQL', 'url': 'https://www.linkedin.com/jobs/view/123/'},
                {'title': 'Engineer', 'description': 'SQL pipelines', 'url': 'https://www.linkedin.com/jobs/view/456/'}]
        with patch('dashboard.worker.collect', return_value=iter(jobs)), patch('dashboard.rating.review_batch', side_effect=self.reviews) as model:
            run(self.path, self.new_run())
            self.assertEqual(model.call_count, 1)
        self.assertTrue(all(j['rating_status'] == 'complete' for j in self.jobs()))
        jid = self.jobs()[0]['id']
        store.update_tracking(self.path, jid, {'stage': 'applied', 'notes': 'Keep this note'})
        jobs[0]['raw_json'] = '{"different_collection_time":true}'
        with patch('dashboard.worker.collect', return_value=iter(jobs)), patch('dashboard.rating.review_batch') as model:
            run(self.path, self.new_run())
            model.assert_not_called()
        snapshot = self.app.snapshot()
        self.assertEqual(snapshot['runs'][0]['cached'], 2)
        self.assertEqual(next(j for j in snapshot['jobs'] if j['id'] == jid)['notes'], 'Keep this note')

    def test_changed_job_and_profile_require_new_rating(self):
        with store.connect(self.path) as db:
            jid, _ = store.upsert(db, {'title': 'Analyst', 'description': 'Python', 'id': 'test'})
        with patch('dashboard.rating.review_batch', side_effect=self.reviews) as model:
            rating.rate_jobs(self.path, [jid])
            self.app.mutate('/api/jobs', {'id': jid, 'title': 'Analyst', 'description': 'Python SQL'})
            self.assertEqual(self.jobs()[0]['rating_status'], 'pending')
            rating.rate_jobs(self.path, [jid])
            self.app.mutate('/api/settings', {'analysis_prompt': 'Prefer remote roles'})
            self.assertEqual(self.jobs()[0]['rating_status'], 'pending')
            rating.rate_jobs(self.path, [jid])
            self.assertEqual(model.call_count, 3)

    def test_failure_and_missing_description_keep_saved_jobs_and_visible_errors(self):
        jobs = [{'title': 'Analyst', 'description': 'Python', 'id': 'one'}, {'title': 'Missing description', 'id': 'two'}]
        with patch('dashboard.worker.collect', return_value=iter(jobs)), patch('dashboard.rating.review_batch', side_effect=ValueError('Subscription limit reached')):
            run(self.path, self.new_run())
        snapshot = self.app.snapshot()
        self.assertEqual(len(snapshot['jobs']), 2)
        self.assertEqual(snapshot['runs'][0]['status'], 'completed_with_warnings')
        self.assertEqual(snapshot['runs'][0]['unrated'], 2)
        self.assertEqual({j['rating_status'] for j in snapshot['jobs']}, {'failed', 'missing_description'})
        self.assertTrue(all(j['rating_error'] for j in snapshot['jobs']))
        self.assertTrue(all(not j['analysis'] for j in snapshot['jobs']))

    def test_one_failed_batch_does_not_repeat_requests_for_remaining_jobs(self):
        with store.connect(self.path) as db:
            ids = [store.upsert(db, {'id': str(n), 'title': 'Analyst', 'description': 'Python'})[0] for n in range(12)]
        with patch('dashboard.rating.review_batch', side_effect=ValueError('Disconnected')) as model:
            counts = rating.rate_jobs(self.path, ids)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(counts['unrated'], 12)
        self.assertTrue(all(j['rating_status'] == 'failed' for j in self.jobs()))

    def test_concurrent_profile_edit_does_not_save_a_stale_rating(self):
        with store.connect(self.path) as db:
            jid, _ = store.upsert(db, {'title': 'Analyst', 'description': 'Python'})
        def edit(jobs, settings):
            self.app.mutate('/api/settings', {'cv_text': 'Different candidate'})
            return self.reviews(jobs, settings)
        with patch('dashboard.rating.review_batch', side_effect=edit):
            counts = rating.rate_jobs(self.path, [jid])
        self.assertEqual(counts['unrated'], 1)
        self.assertEqual(self.jobs()[0]['analysis'], '')
        self.assertEqual(self.jobs()[0]['rating_status'], 'pending')

    def test_cancel_during_rating_does_not_commit_returned_scores(self):
        rid = self.new_run()
        with store.connect(self.path) as db:
            jid, _ = store.upsert(db, {'title': 'Analyst', 'description': 'Python'})
        def cancel(jobs, settings):
            self.app.stop(rid)
            return self.reviews(jobs, settings)
        with patch('dashboard.rating.review_batch', side_effect=cancel):
            rating.rate_jobs(self.path, [jid], rid)
        self.assertEqual(self.jobs()[0]['analysis'], '')
        self.assertEqual(self.app.snapshot()['runs'][0]['status'], 'cancelled')

    def test_mismatched_job_ids_reject_whole_batch(self):
        with store.connect(self.path) as db:
            jid, _ = store.upsert(db, {'title': 'Analyst', 'description': 'Python'})
            job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
        payload = {'reviews': [{'id': 'another-job', 'score': 8, 'summary': 'Wrong', 'strengths': [], 'gaps': []}]}
        with patch('dashboard.rating.cli_json', return_value=payload):
            with self.assertRaisesRegex(ValueError, 'mismatched job IDs'):
                rating.review_batch([job], {'cv_text': 'SQL'})

    def test_api_credentials_are_removed_and_api_login_cannot_rate(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only', 'CODEX_API_KEY': 'test-only'}):
            env = rating.subscription_env()
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('CODEX_API_KEY', env)
        with patch('dashboard.rating.cli_status', return_value={'ready': False, 'message': 'ChatGPT login required'}), patch('dashboard.rating.subprocess.Popen') as process:
            with self.assertRaisesRegex(ValueError, 'ChatGPT'):
                rating.cli_json('test', {})
            process.assert_not_called()

    def test_schema_validation_rejects_boolean_score_and_bad_arrays(self):
        for review in ({'score': True}, {'score': 8, 'summary': 'Fit', 'strengths': 'SQL', 'gaps': []}):
            with self.assertRaises(ValueError):
                rating.valid_review(review)

    def test_imported_review_cannot_reuse_an_unrelated_cached_fingerprint(self):
        with store.connect(self.path) as db:
            jid, _ = store.upsert(db, {'id': 'test', 'title': 'Analyst', 'description': 'Python'})
        with patch('dashboard.rating.review_batch', side_effect=self.reviews):
            rating.rate_jobs(self.path, [jid])
        exported = self.jobs()[0]
        exported['analysis'] = json.dumps({'score': 10, 'summary': 'From another profile', 'strengths': [], 'gaps': []})
        self.app.mutate('/api/import', {'jobs': [exported]})
        self.assertEqual(self.jobs()[0]['rating_status'], 'pending')
        self.assertEqual(self.jobs()[0]['rating_hash'], '')
        with patch('dashboard.rating.review_batch', side_effect=self.reviews) as model:
            rating.rate_jobs(self.path, [jid])
            self.assertEqual(model.call_count, 1)
        self.assertEqual(json.loads(self.jobs()[0]['analysis'])['score'], 8)


if __name__ == '__main__':
    unittest.main()
