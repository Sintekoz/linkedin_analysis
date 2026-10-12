import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch, Mock
from types import SimpleNamespace
import os
import subprocess
import sys
from dashboard import store
from dashboard.collectors import normalize, validate_config
from dashboard.server import App, Handler
from dashboard.worker import run
from main import format_job_search_url

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'workspace.db'
        self.app = App(self.path)
    def tearDown(self):
        for rid in list(self.app.processes):
            self.app.stop(rid)
        self.temp.cleanup()
    def add(self, **kw):
        return self.app.mutate('/api/jobs', {'title':'Analyst', 'company':'Example', 'url':'https://www.linkedin.com/jobs/view/123/?tracking=a', **kw})['id']
    def test_refresh_deduplicates_and_preserves_application(self):
        jid = self.add(description='Python SQL')
        store.update_tracking(self.path,jid,{'stage':'applied','notes':'Recruiter call','follow_up':'2026-12-01'})
        with store.connect(self.path) as db:
            same, created=store.upsert(db, {'title':'Senior Analyst','url':'https://www.linkedin.com/jobs/view/role-123/', 'description':''})
        j=self.app.snapshot()['jobs'][0]
        self.assertFalse(created)
        self.assertEqual(same,jid)
        self.assertEqual(j['stage'],'applied')
        self.assertEqual(j['notes'],'Recruiter call')
        self.assertEqual(j['description'],'Python SQL')
        self.assertTrue(j['applied_date'])
    def test_import_is_atomic_and_validates_links_and_dates(self):
        with self.assertRaises(ValueError):
            self.app.mutate('/api/import',{'jobs':[{'title':'Good'}, {'title':'Bad','url':'javascript:alert(1)'}]})
        self.assertEqual(len(self.app.snapshot()['jobs']),0)
        jid=self.add()
        with self.assertRaises(ValueError):
            store.update_tracking(self.path,jid,{'follow_up':'tomorrow'})
        with self.assertRaises(ValueError):
            store.update_tracking(self.path,jid,{'stage':'invalid'})
    def test_invalid_salary_cannot_corrupt_workspace_json(self):
        for value in (float('nan'), float('inf'), 'not a number', -1):
            with self.assertRaises(ValueError):
                self.add(salary_min=value)
        self.assertEqual(self.app.snapshot()['jobs'],[])

    def test_backup_roundtrip_preserves_tracking_and_analysis(self):
        jid=self.add(stage='interview',notes='Next week')
        with store.connect(self.path) as db:
            db.execute('UPDATE jobs SET analysis=?,cover_letter=? WHERE id=?', ('{"score":8}','Dear hiring team',jid))
        jobs=self.app.snapshot()['jobs']
        second=App(Path(self.temp.name)/'second.db')
        second.mutate('/api/import',{'jobs':jobs})
        actual=second.snapshot()['jobs'][0]
        for field in ('id','stage','notes','analysis','cover_letter'):
            self.assertEqual(actual[field],jobs[0][field])
    def test_legacy_database_readonly_repeatable(self):
        old=Path(self.temp.name)/'old.db'
        db=sqlite3.connect(old)
        db.executescript("CREATE TABLE job_ids(job_id TEXT PRIMARY KEY,status TEXT,job_url TEXT); CREATE TABLE job_description(job_id TEXT PRIMARY KEY,title_name TEXT,company_name TEXT,job_description TEXT,location TEXT); CREATE TABLE chatgpt_analysis(job_id INTEGER PRIMARY KEY,chatgpt_message TEXT); INSERT INTO job_ids VALUES('123','ongoing','https://www.linkedin.com/jobs/view/123/'); INSERT INTO job_description VALUES('123','Analyst','Example','SQL','Dublin'); INSERT INTO chatgpt_analysis VALUES(123,'8');")
        db.commit();db.close();before=old.read_bytes()
        store.import_legacy(self.path,old)
        jid=self.app.snapshot()['jobs'][0]['id']
        store.update_tracking(self.path,jid,{'notes':'Preserve me'})
        store.import_legacy(self.path,old)
        self.assertEqual(old.read_bytes(),before)
        self.assertEqual(len(self.app.snapshot()['jobs']),1)
        self.assertEqual(self.app.snapshot()['jobs'][0]['notes'],'Preserve me')
        self.assertIn('8',self.app.snapshot()['jobs'][0]['analysis'])
    def test_collection_failure_preserves_committed_progress(self):
        sid=self.app.mutate('/api/sources',{'name':'Test','config':{'keywords':'Analyst','location':'Dublin'}})['id']
        with store.connect(self.path) as db:
            source=db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
            rid=db.execute("INSERT INTO runs(name,config,status,started_at) VALUES(?,?,'running',?)",('Test',source['config'],store.now())).lastrowid
        def broken(_):
            yield {'title':'Analyst','url':'https://www.linkedin.com/jobs/view/123/'}
            raise RuntimeError('Provider blocked')
        with patch('dashboard.worker.collect',broken):
            run(self.path,rid)
        snapshot=self.app.snapshot()
        self.assertEqual(len(snapshot['jobs']),1)
        self.assertEqual(snapshot['runs'][0]['status'],'failed')
        self.assertEqual(snapshot['runs'][0]['added'],1)
        self.assertEqual(snapshot['runs'][0]['message'],'Provider blocked')
    def test_run_lifecycle_and_restart(self):
        with store.connect(self.path) as db:
            db.execute("INSERT INTO runs(name,config,status,started_at) VALUES('Test','{}','running',?)",(store.now(),))
        restarted=App(self.path)
        self.assertEqual(restarted.snapshot()['runs'][0]['status'],'interrupted')
    def test_search_filter_validation_and_legacy_filters(self):
        with self.assertRaises(ValueError):
            validate_config({'keywords':'Analyst','location':'Dublin','limit':10000})
        config=validate_config({'keywords':'Analyst','location':'Dublin','remote':True})
        self.assertTrue(config['remote'])
        url=format_job_search_url('https://www.linkedin.com/jobs/search/?keywords=data&f_E=3%2C4&f_JT=F&start=25&currentJobId=123')
        self.assertIn('f_E=3%2C4',url);self.assertIn('f_JT=F',url);self.assertNotIn('start=',url)
    def test_normalization_keeps_real_salary_and_missing_work_model(self):
        row=normalize({'title':'Analyst','company':'Example','job_url':'https://www.linkedin.com/jobs/view/123/','min_amount':50000,'max_amount':float('nan'),'is_remote':False})
        self.assertEqual(row['salary_min'],50000)
        self.assertIsNone(row['salary_max'])
        self.assertEqual(row['work_model'],'')

    def test_manual_edit_can_clear_description_without_clearing_notes(self):
        jid=self.add(description='Old description',notes='Keep notes')
        self.app.mutate('/api/jobs',{'id':jid,'title':'Analyst','description':''})
        j=self.app.snapshot()['jobs'][0]
        self.assertEqual(j['description'],'')
        self.assertEqual(j['notes'],'Keep notes')
    def test_active_run_cancellation_terminates_process(self):
        sid=self.app.mutate('/api/sources',{'name':'Test','config':{'keywords':'Analyst','location':'Dublin'}})['id']
        popen=subprocess.Popen
        def sleeper(*args,**kwargs):
            return popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        with patch('dashboard.server.subprocess.Popen',sleeper):
            rid=self.app.start(sid)['id']
            process=self.app.processes[rid]
            with self.assertRaises(ValueError):
                self.app.start(sid)
            self.app.stop(rid)
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.app.snapshot()['runs'][0]['status'],'cancelled')
    def test_missing_ai_key_does_not_affect_browsing(self):
        jid=self.add(description='SQL')
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}):
            with self.assertRaisesRegex(ValueError,'OPENAI_API_KEY'):
                self.app.ai(jid,'analyze')
        self.assertEqual(len(self.app.snapshot()['jobs']),1)
    def test_mocked_ai_persists_analysis_and_letter(self):
        jid=self.add(description='SQL')
        self.app.mutate('/api/settings',{'cv_text':'Test candidate with SQL experience.'})
        client=Mock()
        client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"score":8,"summary":"Strong SQL fit","strengths":["SQL"],"gaps":[]}'))])
        fake=SimpleNamespace(OpenAI=Mock(return_value=client))
        with patch.dict(sys.modules,{'openai':fake}),patch.dict(os.environ,{'OPENAI_API_KEY':'test-only'}):
            self.app.ai(jid,'analyze')
            self.assertEqual(json.loads(self.app.snapshot()['jobs'][0]['analysis'])['score'],8)
            client.chat.completions.create.return_value.choices[0].message.content='Dear hiring team'
            self.app.ai(jid,'letter')
            self.assertEqual(self.app.snapshot()['jobs'][0]['cover_letter'],'Dear hiring team')
    def test_invalid_ai_score_is_not_persisted(self):
        jid=self.add(description='SQL')
        self.app.mutate('/api/settings',{'cv_text':'Test CV'})
        client=Mock()
        client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"score":999}'))])
        with patch.dict(sys.modules,{'openai':SimpleNamespace(OpenAI=Mock(return_value=client))}),patch.dict(os.environ,{'OPENAI_API_KEY':'test-only'}):
            with self.assertRaisesRegex(ValueError,'invalid score'):
                self.app.ai(jid,'analyze')
        self.assertEqual(self.app.snapshot()['jobs'][0]['analysis'],'')

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=App(Path(self.temp.name)/'test.db')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def request(self,path,method='GET',data=None,headers=None):
        conn=HTTPConnection('127.0.0.1',self.server.server_port)
        conn.request(method,path,body=json.dumps(data) if data is not None else None,headers=headers or {})
        response=conn.getresponse();body=response.read();status=response.status;conn.close();return status,body
    def test_csrf_host_origin_and_real_crud(self):
        status,body=self.request('/api/state');self.assertEqual(status,200)
        token=json.loads(body)['token']
        status,_=self.request('/api/jobs','POST',{'title':'Analyst'},{'Content-Type':'application/json'});self.assertEqual(status,403)
        status,_=self.request('/api/state',headers={'Host':'attacker.example'});self.assertEqual(status,403)
        headers={'Content-Type':'application/json','X-Workspace-Token':token}
        status,_=self.request('/api/jobs','POST',{'title':'Analyst'},headers|{'Origin':'https://attacker.example'});self.assertEqual(status,403)
        status,body=self.request('/api/jobs','POST',{'title':'Analyst'},headers);self.assertEqual(status,200)
        jid=json.loads(body)['id'];status,_=self.request('/api/jobs/'+jid,'POST',{'stage':'interview','notes':'Call Tuesday'},headers);self.assertEqual(status,200)
        self.assertEqual(self.app.snapshot()['jobs'][0]['notes'],'Call Tuesday')
        status,body=self.request('/');self.assertEqual(status,200);self.assertIn(b'next role',body)
        status,body=self.request('/api/export');self.assertEqual(status,200);self.assertEqual(len(json.loads(body)['jobs']),1)
    def test_csv_formula_escape(self):
        self.app.mutate('/api/jobs',{'title':'=1+1','notes':'@unsafe'})
        status,body=self.request('/api/export.csv');self.assertEqual(status,200);self.assertIn(b"'=1+1",body);self.assertIn(b"'@unsafe",body)

if __name__=='__main__':
    unittest.main()
