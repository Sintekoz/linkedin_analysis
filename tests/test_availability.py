import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dashboard import availability as a,store,rating
from dashboard.server import App
from dashboard.worker import run
from test_sources import FakeProcess

GH='https://job-boards.greenhouse.io/example/jobs/123'
LI='https://www.linkedin.com/jobs/view/123456/'
LEVER='https://jobs.lever.co/example/12345678-1234-1234-1234-123456789abc'

def response(url,rows):return 200,url,json.dumps({'jobs':rows,'meta':{'total':len(rows)}})

class EvidenceTests(unittest.TestCase):
 def test_complete_employer_feed_confirms_open_and_missing_post_closed_once_per_board(self):
  def read(url):return response(url,[{'id':123}])
  cache={}
  with patch.object(a,'fetch',side_effect=read) as fetch:
   self.assertEqual(a.check_link(GH,'Data',cache,set())[0],'open')
   self.assertEqual(a.check_link(GH.replace('123','456'),'Data',cache,set())[0],'closed')
   self.assertEqual(fetch.call_count,1)
 def test_partial_failed_or_unexpected_feed_never_establishes_closure(self):
  for code,payload in [(503,''),(200,{'jobs':[],'meta':{'total':100}}),(200,{}),(200,{'jobs':[{}],'meta':{'total':1}})]:
   with self.subTest(payload=payload),patch.object(a,'fetch',side_effect=lambda url:(code,url,json.dumps(payload))):
    self.assertEqual(a.check_link(GH,'Data',{},set())[0],'unverified')
 def test_lever_checks_all_pages_before_marking_absent_post_closed(self):
  calls=[]
  def read(url):
   calls.append(url);rows=[{'id':str(i)} for i in range(100)] if len(calls)==1 else []
   return 200,url,json.dumps(rows)
  with patch.object(a,'fetch',side_effect=read):self.assertEqual(a.check_link(LEVER,'Data',{},set())[0],'closed')
  self.assertEqual(len(calls),2);self.assertIn('skip=100',calls[1])
 def test_incomplete_lever_pagination_is_unverified(self):
  calls=[]
  def read(url):
   calls.append(url)
   if len(calls)==2:raise TimeoutError('Connection timed out')
   return 200,url,json.dumps([{'id':str(i)} for i in range(100)])
  with patch.object(a,'fetch',side_effect=read):self.assertEqual(a.check_link(LEVER,'Data',{},set())[0],'unverified')
 def test_linkedin_explicit_closed_panel_is_distinct_from_description_and_recommendations(self):
  self.assertEqual(a.page_status(LI,LI,'<div class="closed-job__flavor">No longer accepting applications</div>','Data')[0],'closed')
  self.assertEqual(a.page_status(LI,LI,'<p>Other role no longer accepting applications</p>','Data')[0],'unverified')
 def test_job_metadata_must_match_exact_posting_and_not_be_expired(self):
  def body(url,**extra):return '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','url':url,'title':'Data',**extra})+'</script>'
  self.assertEqual(a.page_status(LI,LI,body(LI),'Data')[0],'open')
  self.assertEqual(a.page_status(LI,LI,body(LI.replace('123456','654321')),'Data')[0],'unverified')
  self.assertEqual(a.page_status(LI,LI,body(LI,validThrough='2000-01-01'),'Data')[0],'unverified')
 def test_signin_redirect_and_deleted_search_page_do_not_imply_closed(self):
  self.assertEqual(a.page_status(LI,'https://www.linkedin.com/authwall','No longer accepting applications','Data')[0],'unverified')
  with patch.object(a,'fetch',side_effect=lambda url:(404,url,'')):
   self.assertEqual(a.check_link(LI,'Data',{},set())[0],'closed')
   self.assertEqual(a.check_link('https://www.linkedin.com/jobs/search/','Data',{},set())[0],'unverified')
 def test_platform_denial_skips_further_requests_for_same_platform(self):
  blocked=set()
  with patch.object(a,'fetch',side_effect=a.AccessBlocked('Blocked')) as fetch:
   self.assertEqual(a.check_link(LI,'Data',{},blocked)[0],'unverified')
   self.assertEqual(a.check_link(LI.replace('123456','654321'),'Data',{},blocked)[0],'unverified')
   self.assertEqual(fetch.call_count,1)
 def test_requests_and_redirects_reject_private_or_unsupported_destinations(self):
  for url in ['http://www.linkedin.com/jobs/view/123/','https://127.0.0.1/private','https://www.linkedin.com:8876/','https://user:secret@www.linkedin.com/','https://www.linkedin.com.evil.example/jobs/123','https://employer.example/jobs/123']:
   with self.subTest(url=url),self.assertRaises(ValueError):a.allowed_url(url)

class PersistenceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'jobs.db';self.app=App(self.path)
  with store.connect(self.path) as db:
   self.jid=store.upsert(db,{'title':'Data','description':'SQL','url':LI,'direct_url':GH,'source':'linkedin','stage':'interview','notes':'Keep notes'},tracking=True)[0]
   db.execute("UPDATE jobs SET rating_status='complete',rating_hash='saved-hash',analysis='saved-score' WHERE id=?",(self.jid,))
 def tearDown(self):self.app.processes.clear();self.temp.cleanup()
 def start(self):
  with patch('dashboard.server.subprocess.Popen',return_value=FakeProcess()):return self.app.start_availability({})['id']
 def job(self):
  with store.connect(self.path) as db:return dict(db.execute('SELECT * FROM jobs WHERE id=?',(self.jid,)).fetchone())
 def test_closure_keeps_tracking_and_ratings_and_checks_employer_before_aggregator(self):
  rid=self.start()
  with patch.object(a,'check_link',return_value=('closed','Employer removed exact posting')) as check,patch('dashboard.rating.review_batch') as model,patch.object(a.time,'sleep'):
   run(self.path,rid);model.assert_not_called();self.assertEqual(check.call_args.args[0],GH)
  j=self.job();self.assertEqual(j['posting_status'],'closed')
  for key,value in [('stage','interview'),('notes','Keep notes'),('rating_hash','saved-hash'),('analysis','saved-score')]:self.assertEqual(j[key],value)
  snapshot=self.app.snapshot();self.assertEqual(snapshot['posting_counts']['closed'],1)
  self.assertEqual(snapshot['workflows'][0]['availability_counts']['closed'],1)
  self.assertEqual(snapshot['workflows'][0]['status'],'completed')
 def test_blocked_recheck_retains_last_confirmed_closure_and_employer_reappearance_reopens(self):
  with store.connect(self.path) as db:
   a.record(db,self.jid,'closed','Removed from feed');a.record(db,self.jid,'unverified','Blocked')
  self.assertEqual(self.job()['posting_last_confirmed'],'closed')
  with store.connect(self.path) as db:a.seen(db,self.jid,{'source':'greenhouse','availability':'ongoing'})
  self.assertEqual(self.job()['posting_status'],'open');self.assertEqual(self.job()['stage'],'interview')
 def test_search_reappearance_alone_cannot_override_closure(self):
  with store.connect(self.path) as db:
   a.record(db,self.jid,'closed','Closed');store.upsert(db,{'title':'Data','url':LI,'availability':'unknown'})
   a.seen(db,self.jid,{'source':'linkedin','availability':'unknown'})
  self.assertEqual(self.job()['posting_status'],'closed');self.assertTrue(self.job()['posting_last_seen_at'])
 def test_checks_resume_without_rechecking_completed_jobs(self):
  rid=self.start()
  with store.connect(self.path) as db:db.execute('UPDATE runs SET checkpoint=? WHERE id=?',(json.dumps({'checked_ids':[self.jid]}),rid))
  with patch.object(a,'check_link') as check:run(self.path,rid);check.assert_not_called()
 def test_full_workflow_queues_availability_after_collection_and_expands_ids_at_launch(self):
  self.app.mutate('/api/sources',{'name':'Data','config':{'provider':'linkedin','keywords':'Data','location':'Ireland'}})
  with patch('dashboard.server.subprocess.Popen',side_effect=lambda *args,**kw:FakeProcess()):
   result=self.app.start_workflow()
   with store.connect(self.path) as db:
    rows=list(db.execute('SELECT kind,config FROM runs WHERE workflow_id=? ORDER BY id',(result['workflow_id'],)))
   self.assertEqual(rows[-1]['kind'],'availability');self.assertNotIn('availability_ids',json.loads(rows[-1]['config']))
 def test_manual_closed_override_is_separate_from_application_stage(self):
  self.app.mutate('/api/jobs',{'id':self.jid,'title':'Data','url':LI,'availability':'closed'})
  self.assertEqual(self.job()['posting_status'],'closed');self.assertEqual(self.job()['stage'],'interview')
