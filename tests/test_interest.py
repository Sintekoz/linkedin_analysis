"""Reversible library disposition must survive collection without altering applications."""
import json
import tempfile
import unittest
from pathlib import Path
from dashboard import applications,store
from dashboard.server import App

class InterestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'jobs.db';self.app=App(self.path)
        self.ids=[self.app.mutate('/api/jobs',{'title':'Analyst '+str(i),'company':'Example','url':'https://www.linkedin.com/jobs/view/'+str(123000+i)+'/'})['id'] for i in range(3)]
    def tearDown(self):self.temp.cleanup()
    def job(self,jid):
        with store.connect(self.path) as db:return dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
    def manage(self,ids,value='not_interested'):return self.app.mutate('/api/jobs/interest',{'job_ids':ids,'interest':value})
    def test_existing_database_migrates_to_interested_without_changing_records(self):
        # Use a real pre-feature schema by removing the new columns from a current fixture.
        with store.connect(self.path) as db:
            db.execute('ALTER TABLE jobs DROP COLUMN interest');db.execute('ALTER TABLE jobs DROP COLUMN interest_updated_at')
        App(self.path)
        for jid in self.ids:self.assertEqual(self.job(jid)['interest'],'interested')
    def test_bulk_change_is_atomic_and_idempotent(self):
        result=self.manage([self.ids[0],self.ids[1],self.ids[0]])
        self.assertEqual(len(result['changed_ids']),2);self.assertEqual(self.job(self.ids[2])['interest'],'interested')
        self.assertEqual(self.manage(self.ids[:2])['changed_ids'],[])
    def test_unknown_id_rolls_back_all_changes(self):
        with self.assertRaises(LookupError):self.manage([self.ids[0],'missing'])
        self.assertEqual(self.job(self.ids[0])['interest'],'interested')
    def test_invalid_input_is_rejected(self):
        for ids,value in [(None,'not_interested'),([],'not_interested'),('123','not_interested'),([1],'not_interested'),([self.ids[0]],'archive')]:
            with self.assertRaises(ValueError):self.manage(ids,value)
    def test_hiding_keeps_tracking_and_cached_rating(self):
        jid=self.ids[0];store.update_tracking(self.path,jid,{'stage':'interview','notes':'Follow up','applied_date':'2026-10-01','follow_up':'2026-10-20','contact':'Recruiter'})
        with store.connect(self.path) as db:db.execute("UPDATE jobs SET analysis=?,rating_status='complete',rating_hash='cache' WHERE id=?",(json.dumps({'score':8}),jid))
        before=self.job(jid);self.manage([jid]);after=self.job(jid)
        for key in (*store.TRACK_FIELDS,'analysis','rating_status','rating_hash','updated_at'):self.assertEqual(before[key],after[key])
        self.assertTrue(after['interest_updated_at'])
    def test_scraping_refresh_does_not_restore_hidden_position(self):
        jid=self.ids[0];self.manage([jid]);stamp=self.job(jid)['interest_updated_at']
        with store.connect(self.path) as db:store.upsert(db,{'id':jid,'title':'Updated title','url':self.job(jid)['url'],'interest':'interested'})
        self.assertEqual(self.job(jid)['interest'],'not_interested');self.assertEqual(self.job(jid)['interest_updated_at'],stamp)
    def test_restore_and_events(self):
        self.manage(self.ids[:2]);self.manage([self.ids[0]],'interested')
        self.assertEqual(self.job(self.ids[0])['interest'],'interested');self.assertEqual(self.job(self.ids[1])['interest'],'not_interested')
        with store.connect(self.path) as db:
            events=[r[0] for r in db.execute('SELECT message FROM events WHERE job_id=?',(self.ids[0],))]
        self.assertTrue(any('Marked not interested' in e for e in events));self.assertTrue(any('Restored' in e for e in events))
    def test_export_import_retains_hidden_choice(self):
        self.manage([self.ids[0]])
        with store.connect(self.path) as db:jobs=store.library(db)
        other=App(Path(self.temp.name)/'restored.db');other.mutate('/api/import',{'jobs':jobs})
        with store.connect(other.path) as db:self.assertEqual(db.execute('SELECT interest FROM jobs WHERE id=?',(self.ids[0],)).fetchone()[0],'not_interested')
    def test_duplicate_alias_applies_to_visible_owner(self):
        with store.connect(self.path) as db:db.execute('UPDATE jobs SET duplicate_of=? WHERE id=?',(self.ids[0],self.ids[1]))
        self.manage([self.ids[1]])
        self.assertEqual(self.job(self.ids[0])['interest'],'not_interested');self.assertEqual(self.job(self.ids[1])['interest'],'not_interested')
    def test_reconciliation_preserves_most_recent_disposition(self):
        self.manage([self.ids[1]])
        with store.connect(self.path) as db:
            db.execute('UPDATE jobs SET direct_url=? WHERE id IN (?,?)',('https://jobs.example.org/jobs/role-123456',*self.ids[:2]))
            store.reconcile_duplicates(db)
            visible=[dict(r) for r in db.execute("SELECT * FROM jobs WHERE duplicate_of='' AND id IN (?,?)",self.ids[:2])]
        self.assertEqual(len(visible),1);self.assertEqual(visible[0]['interest'],'not_interested')
    def test_hidden_position_blocks_agent_start(self):
        self.manage([self.ids[0]])
        with self.assertRaisesRegex(ValueError,'Restore this position'):applications.enqueue(self.path,self.ids[0])
    def test_hiding_cancels_queued_preparation_without_restart_on_restore(self):
        with store.connect(self.path) as db:
            tid=db.execute('INSERT INTO application_tasks(job_id,created_at,updated_at) VALUES(?,?,?)',(self.ids[0],store.now(),store.now())).lastrowid
        self.manage([self.ids[0]])
        self.manage([self.ids[0]],'interested')
        with store.connect(self.path) as db:self.assertEqual(db.execute('SELECT status FROM application_tasks WHERE id=?',(tid,)).fetchone()[0],'cancelled')
    def test_all_saved_positions_remain_retrievable(self):
        self.manage(self.ids)
        with store.connect(self.path) as db:jobs=store.library(db)
        self.assertEqual(len(jobs),3);self.assertTrue(all(j['interest']=='not_interested' for j in jobs))

if __name__=='__main__':unittest.main()
