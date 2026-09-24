"""Native allocation failure handling; real owned-process shutdown, no cloud writes."""
import importlib.util,json,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('pilot',HERE/'pilot-session.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class AllocationFailureTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.plan=self.root/'remote.tfplan';self.plan.write_text('test plan only')
  self.session={'profile':p.ROUTER_SESSION_PROFILE,'terraform_plan_path':str(self.plan),'terraform_plan_sha256':p.file_sha256(self.plan),'placement_timeout_seconds':60}
  p.write_json(self.root/'cloud-guard-ready.json',{'cluster':'cluster-this-run'})
 def group(self,role='remote',failed=True):
  return {'metadata':{'id':'group-'+role,'name':'router-'+role,'parent_id':'cluster-this-run'},'status':{'state':'PROVISIONING','events':[{'last_occurrence':{'level':'ERROR','code':'ComputeInstanceOperationFailed','error':{'details':[{'code':'NotEnoughResources'}]}}}] if failed else []}}
 def check(self,doc):
  return p.allocation_failure(self.root,self.session,query_fn=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps(doc),'')))
 def test_real_native_error_payload_in_observed_event_shape(self):
  fixture=HERE.parent.parent/'results/2026-09-24-remote-capacity/remote-operations-final.json'
  operation=next(x for x in json.loads(fixture.read_text())['operations'] if x['description']=='Create Instance')
  group=self.group();group['status']['events'][0]['last_occurrence']['error']=operation['status']
  failure=self.check({'items':[group]});self.assertEqual(failure['role'],'remote');self.assertIn('NotEnoughResources',json.dumps(failure))
 def test_missing_group_healthy_and_other_group_failure(self):
  for doc in [{},{'items':[]},{'items':[self.group(failed=False)]},{'items':[self.group('local')]}]:self.assertIsNone(self.check(doc))
 def test_invalid_or_ambiguous_status_is_not_success(self):
  wrong=self.group();wrong['metadata']['parent_id']='other'
  for doc in [[],{'items':None},{'items':[wrong]},{'items':[self.group(),self.group()]},{'next_page_token':'next'}]:
   with self.subTest(doc=doc),self.assertRaises((ValueError,KeyError,TypeError)):self.check(doc)
 def test_wait_detects_failure_after_one_poll(self):
  proc=Mock();proc.wait.side_effect=subprocess.TimeoutExpired('apply',10);check=Mock(return_value={'role':'remote','events':['failure']})
  with self.assertRaises(p.SessionError):p.wait_for_allocation(proc,self.root,self.session,check_fn=check)
  check.assert_called_once();self.assertTrue((self.root/'allocation-failure.json').exists())
 def test_transient_query_failure_recovers_but_three_stop(self):
  proc=Mock();proc.wait.side_effect=[subprocess.TimeoutExpired('apply',10),subprocess.TimeoutExpired('apply',10),0]
  check=Mock(side_effect=[ValueError('bad JSON'),None]);self.assertEqual(p.wait_for_allocation(proc,self.root,self.session,check_fn=check),0)
  proc.wait.side_effect=subprocess.TimeoutExpired('apply',10);check=Mock(side_effect=ValueError('bad JSON'))
  with self.assertRaisesRegex(p.SessionError,'three'):p.wait_for_allocation(proc,self.root,self.session,check_fn=check)
  self.assertEqual(check.call_count,3)
 def test_graceful_stop_and_deadline(self):
  proc=Mock();proc.wait.side_effect=subprocess.TimeoutExpired('apply',10)
  (self.root/'graceful-stop-request.json').write_text('{}')
  with self.assertRaisesRegex(p.SessionError,'Graceful'):p.wait_for_allocation(proc,self.root,self.session,check_fn=Mock())
  with self.assertRaises(subprocess.TimeoutExpired):p.wait_for_allocation(proc,self.root,self.session,now_fn=Mock(side_effect=[0,61]))
 def test_apply_failure_interrupts_real_child_then_cleans(self):
  child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  self.addCleanup(lambda:child.poll() is None and child.kill())
  original=p.wait_for_allocation
  check=Mock(return_value={'role':'remote','events':['native failure']});cleanup=Mock(return_value=0)
  with patch.object(p,'session_infrastructure_settings',return_value=(self.root,'project','subnet')),patch.object(p,'wait_for_allocation',side_effect=lambda process,run,session:original(process,run,session,check_fn=check)):
   started=time.monotonic();code=p.apply_plan(self.root,self.session,popen_factory=Mock(return_value=child),cleanup_fn=cleanup)
  self.assertEqual(code,125);self.assertLess(time.monotonic()-started,15);self.assertIsNotNone(child.poll());cleanup.assert_called_once_with(self.root);self.assertTrue((self.root/'allocation-abort.json').exists())

if __name__=='__main__':unittest.main()
