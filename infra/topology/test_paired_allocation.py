"""Both GPU groups admitted together before either model; no cloud calls."""
import importlib.util,json,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('runner',HERE/'run-router-session.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
ROOT=HERE.parent.parent/'workloads/router-session'
class PairedAllocationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.obj=object.__new__(r.Controller);o=self.obj;o.run=self.root;o.tf=['terraform'];o.k=['kubectl'];o.out=self.root;o.cluster='cluster';o.ips={};o.nodes_by_role={};o.config_path=ROOT/'config.json';o.local_requested=None;o.remote_requested=None;o.session_cap=r.budget.CAP;o.original_target=time.time()+10800;o.session={'started_unix':time.time()-600,'cleanup_start_deadline_unix':time.time()+9000}
  targets=['nebius_mk8s_v1_node_group.local','nebius_mk8s_v1_node_group.remote[0]','nebius_compute_v1_gpu_cluster.local'];o.original={'variables':{},'configuration':{},'resource_changes':[{'address':x,'change':{'actions':['create']}} for x in targets]};self.calls=[]
  def call(cmd,name,*a,**k):
   self.calls.append((cmd,name));(self.root/'gpu-pair.tfplan').write_text('plan')
   payload=o.original if name=='gpu-pair-plan-json' else {'values':{'root_module':{'resources':[{'address':t,'values':{'id':'id-'+str(i)}} for i,t in enumerate(targets)]}}}
   return subprocess.CompletedProcess(cmd,0,json.dumps(payload),'')
  o.call=call;o.reduce_deadline=Mock();o.nodes=Mock(return_value={'local':'node-a','remote':'node-b'})
 def test_single_apply_targets_both_before_healthy_node_handoff(self):
  with patch.object(r,'fresh_capacity',return_value={'ok':True}) as capacity,patch.object(r.pilot,'apply_plan',return_value=0) as apply:
   self.obj.allocate_pair()
  apply.assert_called_once();capacity.assert_called_once_with(minimum=2)
  self.assertEqual(self.obj.local_requested,self.obj.remote_requested)
  self.assertEqual(len([v for v in self.calls[0][0] if v.startswith('-target=')]),2)
  self.obj.nodes.assert_called_once_with({'local':'id-0','remote':'id-1'})
  self.assertEqual(Path(self.obj.session['terraform_plan_path']).name,'gpu-pair.tfplan')
 def test_capacity_guard_or_allocation_failure_blocks_model_handoff(self):
  for where in ['capacity','guard','apply']:
   with self.subTest(where=where):
    self.obj.reduce_deadline=Mock(side_effect=ValueError('guard') if where=='guard' else None)
    with patch.object(r,'fresh_capacity',side_effect=ValueError('capacity') if where=='capacity' else None,return_value={}),patch.object(r.pilot,'apply_plan',return_value=1) as apply:
     with self.assertRaises((ValueError,RuntimeError)):self.obj.allocate_pair()
     if where!='apply':apply.assert_not_called()
    self.obj.nodes.assert_not_called()
 def test_unrelated_plan_change_rejected_before_apply(self):
  original=self.obj.call
  def call(cmd,name,*a,**k):
   result=original(cmd,name,*a,**k)
   if name=='gpu-pair-plan-json':
    doc=json.loads(result.stdout);doc['resource_changes'].append({'address':'unrelated','change':{'actions':['delete']}});result.stdout=json.dumps(doc)
   return result
  self.obj.call=call
  with patch.object(r.pilot,'apply_plan') as apply,patch.object(r,'fresh_capacity') as capacity:
   with self.assertRaises(ValueError):self.obj.allocate_pair()
   apply.assert_not_called();capacity.assert_not_called()
 def test_missing_healthy_host_blocks_loading(self):
  self.obj.nodes.return_value={'local':'node-a'}
  with patch.object(r,'fresh_capacity',return_value={}),patch.object(r.pilot,'apply_plan',return_value=0):
   with self.assertRaisesRegex(ValueError,'Both healthy'):self.obj.allocate_pair()
  with self.assertRaisesRegex(ValueError,'Both GPU'):self.obj.deploy_pair()
 def test_shared_budget_charges_both_from_request_and_never_extends(self):
  d=r.budget.admit_pair(0,600,10800)
  self.assertLessEqual(r.budget.cost(0,600,600,d['deletion_target_unix'])+r.budget.MARGIN,r.budget.CAP)
  self.assertEqual(d['deletion_target_unix']-d['cleanup_start_deadline_unix'],1200)
  with self.assertRaises(ValueError):r.budget.admit_pair(0,600,1200)
 def test_both_pods_submitted_before_either_readiness_check(self):
  o=self.obj;o.nodes_by_role={'local':'a','remote':'b'};events=[]
  o.apply=lambda doc,name:events.append(('apply',[x['metadata']['name'] for x in doc['items'] if x['kind']=='Pod']))
  def call(cmd,name,*a,**k):
   events.append((name,None))
   if name.endswith('-pod'):return subprocess.CompletedProcess(cmd,0,json.dumps({'metadata':{},'status':{'phase':'Running','podIP':'10.0.0.'+('1' if name.startswith('local') else '2')}}),'')
   return subprocess.CompletedProcess(cmd,1 if name.endswith('-failure') else 0,'','')
  o.call=call;o.deploy_pair();self.assertEqual(events[0],('apply',['local','remote']));self.assertEqual(set(o.ips),{'local','remote'})
 def test_remote_startup_failure_not_hidden_by_local_readiness_wait(self):
  o=self.obj;o.nodes_by_role={'local':'a','remote':'b'};o.apply=Mock();o.sleep=Mock()
  def call(cmd,name,*a,**k):
   return subprocess.CompletedProcess(cmd,0,json.dumps({'metadata':{},'status':{'phase':'Failed' if name=='remote-pod' else 'Pending'}}),'')
  o.call=call
  with self.assertRaisesRegex(RuntimeError,'remote GPU Pod failed'):o.deploy_pair()
  o.sleep.assert_not_called()
 def test_guard_ack_requires_no_preexisting_gpu_group(self):
  o=self.obj;o.call=lambda *a,**k:subprocess.CompletedProcess([],0,json.dumps({'cluster':'cluster','deadline':100,'gpu_groups_present_at_arm':['router-local']}),'')
  with self.assertRaisesRegex(ValueError,'GPU groups exist'):r.Controller.reduce_deadline(o,{'cleanup_start_deadline_unix':100,'deletion_target_unix':200})
 def test_failure_detector_catches_either_group_in_pair(self):
  o=self.obj;o.session.update(profile=r.pilot.ROUTER_SESSION_PROFILE,terraform_plan_path=str(self.root/'gpu-pair.tfplan'));r.pilot.write_json(self.root/'cloud-guard-ready.json',{'cluster':'cluster'})
  for failed in ['local','remote']:
   groups=[{'metadata':{'name':'router-'+role,'id':role,'parent_id':'cluster'},'status':{'state':'PROVISIONING','events':[{'last_occurrence':{'level':'ERROR','code':'ComputeInstanceOperationFailed'}}] if role==failed else []}} for role in ['local','remote']]
   result=r.pilot.allocation_failure(self.root,o.session,query_fn=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps({'items':groups}),'')))
   self.assertEqual(result['role'],failed)
if __name__=='__main__':unittest.main()
