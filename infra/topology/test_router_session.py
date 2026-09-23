"""Combined-session gates and budget rules; no cloud commands."""
import importlib.util,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
HERE=Path(__file__).resolve().parent

def module(n):
 s=importlib.util.spec_from_file_location(n,HERE/(n+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
r=module('run-router-session');b=module('router-session-budget');p=module('router-session-plan');g=module('cloud-deadline-guard');fixtures=module('test_pilot_session')
ROOT=HERE.parent.parent/'workloads/router-session'

class CombinedTests(unittest.TestCase):
 def test_collection_error_is_bounded_and_does_not_embed_archive_bytes(self):
  import subprocess
  with tempfile.TemporaryDirectory() as temp:
   obj=object.__new__(r.Controller);obj.run=Path(temp);obj.out=Path(temp);obj.ips={};obj.k=['kubectl']
   obj.call=lambda *a,**k:subprocess.CompletedProcess([],1,b'large binary archive'*10000,b'connection failed')
   with self.assertRaises(RuntimeError) as error:obj.collect()
   self.assertLess(len(str(error.exception)),2000)
   self.assertNotIn('large binary archive',str(error.exception))
   self.assertFalse(json.loads((obj.run/'last-collection.json').read_text())['complete'])
 def test_verified_snapshot_still_requires_completion_marker(self):
  import subprocess
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);source=root/'source';source.mkdir();(source/'other.json').write_text('{}')
   obj=object.__new__(r.Controller);obj.run=root;obj.out=root;obj.ips={};obj.k=['kubectl']
   with (root/'client-evidence.tar.gz').open('wb') as output:r.snapshots.snapshot(source,output)
   obj.call=lambda *a,**k:subprocess.CompletedProcess([],0,b'',b'')
   with self.assertRaisesRegex(RuntimeError,'Fresh required artifact missing'):obj.collect(required='qualification/local/complete.json')
 def test_trial_snapshot_preserves_previous_verified_results(self):
  import subprocess
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);source=root/'source';source.mkdir();(source/'complete.json').write_text('{"requests":2}')
   obj=object.__new__(r.Controller);obj.run=root;obj.out=root;obj.ips={};obj.k=['kubectl']
   old=root/'client/trials/earlier';old.mkdir(parents=True);(old/'complete.json').write_text('{"requests":1}')
   with (root/'client-evidence.tar.gz').open('wb') as output:r.snapshots.snapshot(source,output)
   calls=[]
   def call(cmd,*a,**k):calls.append(cmd);return subprocess.CompletedProcess([],0,b'',b'')
   obj.call=call;obj.collect(required='trials/current/complete.json')
   self.assertEqual(calls[0][-1],'/results/trials/current')
   self.assertEqual(json.loads((root/'client/trials/current/complete.json').read_text()),{'requests':2})
   self.assertEqual(json.loads((old/'complete.json').read_text()),{'requests':1})
 def test_remaining_pool_limits_both_host_deadline(self):
  from decimal import Decimal
  cap=b.spending_cap('74.7896614372222222222')
  d=b.admit_remote(0,11*60,32*60,180*60,cap=cap)
  self.assertLessEqual(b.cost(0,11*60,32*60,d['deletion_target_unix'])+b.MARGIN,cap)
  self.assertEqual(d['deletion_target_unix']-d['cleanup_start_deadline_unix'],1200)
  self.assertEqual(b.spending_cap('100'),Decimal('75'))
  for value in ['73.99','NaN','Infinity','-1']:
   with self.assertRaises(ValueError):b.spending_cap(value)
  with self.assertRaises(ValueError):b.admit_remote(0,11*60,36*60,180*60,cap=cap)
 def test_capacity_age_and_recheck_before_first_gpu_mutation(self):
  from datetime import datetime,timezone
  now=10000
  def advice(age):return {'selected':{'status':{'preemptible':{'effective_at':datetime.fromtimestamp(now-age,timezone.utc).isoformat()}}}}
  for age,valid in [(0,True),(1800,True),(1801,False),(-1,False)]:
   with self.subTest(age=age),patch.object(r.layout,'verify_capacity',return_value=advice(age)) as check,patch.object(r.time,'time',return_value=now):
    if valid:self.assertEqual(r.fresh_capacity(2),advice(age))
    else:
     with self.assertRaises(ValueError):r.fresh_capacity(2)
    check.assert_called_once_with(minimum=2)
  with tempfile.TemporaryDirectory() as temp:
   obj=object.__new__(r.Controller);obj.run=Path(temp);obj.local_requested=None;obj.remote_requested=None
   with patch.object(r,'fresh_capacity',side_effect=ValueError('capacity disappeared')),patch.object(r.s.Controller,'allocate') as allocate:
    with self.assertRaises(ValueError):obj.allocate('local')
    allocate.assert_not_called();self.assertIsNone(obj.local_requested)
    self.assertFalse((obj.run/'resource-request-times.json').exists())
   with patch.object(r,'fresh_capacity',return_value=advice(0)) as check,patch.object(r.s.Controller,'allocate') as allocate:
    obj.allocate('local');check.assert_called_once_with(minimum=2);allocate.assert_called_once_with('local')
    self.assertTrue((obj.run/'capacity-before-local.json').exists())
 def test_budget_early_late_and_reserve(self):
  for local,now in [(0,0),(0,20*60),(8*60,30*60)]:
   d=b.admit_remote(0,local,now,180*60)
   self.assertLessEqual(b.cost(0,local,now,d['deletion_target_unix'])+b.MARGIN,b.CAP)
   self.assertEqual(d['deletion_target_unix']-d['cleanup_start_deadline_unix'],1200)
  with self.assertRaises(ValueError):b.admit_remote(0,0,45*60,180*60)
  with self.assertRaises(ValueError):b.admit_remote(0,0,20*60,50*60)
 def test_cloud_deadline_never_extends(self):
  self.assertEqual(g.shortened_deadline(100,{'cluster':'c','deadline':80},'c'),80)
  for value in [101,float('inf'),float('nan'),-1]:
   with self.assertRaises(ValueError):g.shortened_deadline(100,{'cluster':'c','deadline':value},'c')
 def test_two_host_profile_has_only_existing_guard(self):
  plan=fixtures.PilotSessionTest().plan_json(profile=r.pilot.ROUTER_SESSION_PROFILE)
  plan['variables'].update(cloud_guard={'value':True},single_gpu_host={'value':False},existing_guard_service_account_id={'value':r.pilot.LAYOUT_GUARD_ID})
  cpu=next(x['change']['after'] for x in plan['resource_changes'] if x['address']=='nebius_mk8s_v1_node_group.cpu[0]')
  cpu['template']['service_account_id']=r.pilot.LAYOUT_GUARD_ID
  plan['configuration']={'root_module':{'resources':[{'address':'nebius_mk8s_v1_node_group.remote','reference':'nebius_compute_v1_gpu_cluster.local.id'}]}}
  r.pilot.validate_plan_structure(plan,r.pilot.ROUTER_SESSION_PROFILE)
  plan['variables']['single_gpu_host']['value']=True
  with self.assertRaises(r.pilot.SessionError):r.pilot.validate_plan_structure(plan,r.pilot.ROUTER_SESSION_PROFILE)
 def test_direct_full_plan_apply_is_blocked(self):
  with patch.object(r.pilot,'prepare_session') as prepare:
   self.assertEqual(r.pilot.main(['--execute','--profile',r.pilot.ROUTER_SESSION_PROFILE,'--approval-record','x','--approved-max-usd-pretax','70','--purchase-type','preemptible','--plan','x']),2)
   prepare.assert_not_called()
 def test_single_attempt_approval_cannot_be_reused(self):
  case=fixtures.PilotSessionTest();case.setUp()
  try:
   module=fixtures.pilot;profile=module.ROUTER_SESSION_PROFILE;policy=module.PROFILE_POLICIES[profile]
   approval=json.loads(case.approval.read_text());approval.update(allowed_profiles=[profile],allow_multiple_attempts=False,max_total_usd_pretax='75',project_id=policy['project_id'],subnet_id=policy['subnet_id'])
   case.approval.write_text(json.dumps(approval));case.args.profile=profile;case.args.approved_max_usd_pretax='75'
   module.prepare_session(case.args,state_root=case.root/'state',now=10000)
   with self.assertRaisesRegex(module.SessionError,'already used'):
    module.prepare_session(case.args,state_root=case.root/'state',now=10001)
  finally:case.doCleanups()
 def test_picker_restart_and_missing_pod_are_detected(self):
  import subprocess,copy
  obj=object.__new__(r.Controller);obj.k=['kubectl'];obj.ips={'local':'a','remote':'b'};obj.policy='soft'
  pods=[]
  for name in ['local','remote','client','prefill-http','epp-123']:
   pods.append({'metadata':{'name':name,'uid':name,'labels':{'llm-d-router-standalone':'topology-epp'} if name.startswith('epp') else {}},'status':{'phase':'Running','containerStatuses':[{'name':'main','containerID':name,'state':{'running':{}},'restartCount':0}]}})
  obj.call=lambda *a,**k:subprocess.CompletedProcess([],0,json.dumps({'items':pods}),'')
  identity=obj.live_identity();self.assertIn('picker',identity)
  pods[-1]['status']['containerStatuses'][0]['restartCount']=1
  with self.assertRaises(RuntimeError):obj.live_identity()
  pods.pop()
  with self.assertRaises(RuntimeError):obj.live_identity()
 def test_plan_counts_and_no_evaluation_pins(self):
  import gzip
  plan=p.frozen_plan();cases=json.loads(gzip.decompress((ROOT/'suite.json.gz').read_bytes()))['cases']
  self.assertEqual(len(plan['trials']),24)
  self.assertEqual(sum(4 for t in plan['trials'] if t['mode']=='heldout'),80)
  for t in plan['trials']:
   rows=p.requests_for(plan,t,cases)
   self.assertTrue(all(x['pin'] is None for x in rows));self.assertEqual(sum(x['kind']=='foreground' for x in rows),4)
   self.assertTrue(all(x['case_id'].endswith('-1') for x in rows))
 def test_combined_orchestration_preserves_local_and_cleans_every_failure(self):
  # Real execute()/execute_admitted(); effects at external phase boundaries are substituted.
  names=['bootstrap','local-allocate','local-deploy','client-apply','client-ready','image-import','local-qualify','remote-allocate','remote-deploy','remote-qualify','router-setup','trial','policy','collect']
  for fault in [None,*names]:
   with self.subTest(fault=fault),tempfile.TemporaryDirectory() as temp:
    run=Path(temp);session={'cleanup_start_deadline_unix':time.time()+12000};events=[]
    obj=object.__new__(r.Controller);obj.run=run;obj.session=session;obj.plan=p.frozen_plan();obj.plan_path=ROOT/'plan.json';obj.k=['kubectl'];obj.cpu_node='cpu';obj.policy=None;obj.gpu_checks=False;obj.config_path=ROOT/'config.json'
    def event(name,value=None):
     events.append(name)
     if name==fault:raise RuntimeError('injected '+name)
     return value
    obj.allocate=lambda role:event(role+'-allocate');obj.deploy=lambda role:event(role+'-deploy')
    obj.apply=lambda *a,**k:event('client-apply');obj.call=lambda *a,**k:event('client-ready')
    obj.qualify=lambda role:event(role+'-qualify');obj.setup_router=lambda:event('router-setup');obj.import_picker=lambda:event('image-import')
    obj.trial=lambda t:event('trial',(dict(trial=t),[]));obj.picker=lambda *a:event('policy');obj.collect=lambda *a,**k:event('collect')
    tune={'tradeoff_observed':True,'reference':'soft','parameters':{x:{} for x in p.POLICIES}}
    with patch.object(r.s.paired.Controller,'bootstrap',side_effect=lambda *a,**k:event('bootstrap')),patch.object(r,'Controller',return_value=obj),patch.object(r.pilot,'spawn_guard',return_value=Mock(pid=1)),patch.object(r.pilot,'wait_guard_ready'),patch.object(r.pilot,'cleanup_until_target',side_effect=lambda *_:events.append('cleanup')),patch.object(r.result,'tune',return_value=tune),patch.object(r.result,'summarize',return_value={'complete':True}):
     if fault:
      with self.assertRaises(RuntimeError):r.execute_admitted(run,session,ROOT/'config.json',ROOT/'suite.json.gz',ROOT/'qualification.json.gz',Path('chart'),Path('image'),ROOT/'plan.json')
     else:r.execute_admitted(run,session,ROOT/'config.json',ROOT/'suite.json.gz',ROOT/'qualification.json.gz',Path('chart'),Path('image'),ROOT/'plan.json')
    self.assertEqual(events[-1],'cleanup')
    if fault is None:
     self.assertEqual(events.count('local-deploy'),1);self.assertEqual(events.count('trial'),40)
     self.assertLess(events.index('local-qualify'),events.index('remote-allocate'))
     self.assertLess(events.index('client-ready'),events.index('local-allocate'));self.assertLess(events.index('image-import'),events.index('local-allocate'))
    if fault=='local-qualify':self.assertNotIn('remote-allocate',events)
 def test_marker_hashes_and_sizes(self):
  doc=r.code_manifest(ROOT/'config.json',{'local':'node-a','remote':'node-b'})
  self.assertEqual(len([x for x in doc['items'] if x['kind']=='Pod']),2)
  self.assertLess(len(json.dumps(doc['items'][1])),1048576)
  self.assertLess(len(json.dumps(r.client_manifest('cpu',ROOT/'suite.json.gz',ROOT/'qualification.json.gz',ROOT/'plan.json')['items'][0])),1048576)

if __name__=='__main__':unittest.main()
