import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent

def load(name,file):
 s=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
client=load('paired_client','paired-client.py');controller=load('paired_controller','run-paired-hosts.py');guard=load('cloud_guard','cloud-deadline-guard.py')

class PairedTests(unittest.TestCase):
 def test_no_transport_restriction_and_shared_devices(self):
  doc=controller.engine_manifest({'local':'a','remote':'b'})
  pods=[x for x in doc['items'] if x['kind']=='Pod']
  self.assertEqual([x['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu'] for x in pods],['2','1'])
  self.assertNotIn('UCX_TLS',[x['name'] for x in pods[0]['spec']['containers'][0]['env']])
  self.assertNotIn('hostIPC',pods[0]['spec'])
 def test_guard_only_exact_cluster_children(self):
  self.assertEqual(guard.select_groups([{'id':'g','name':'router-local','parent_id':'c'}],'c'),{'router-local':'g'})
  for name,parent in [('unrelated','c'),('router-local','other')]:
   with self.assertRaises(ValueError):guard.select_groups([{'id':'g','name':name,'parent_id':parent}],'c')
 def test_balanced_frozen_pairs(self):
  cases=[{'input_tokens':n,'request_body':{'prompt':[1]*n}} for n in (512,8192)]
  pairs=client.plan_pairs(cases)
  self.assertEqual(len(pairs),48)
  for level in (0,1,3):
   for n in (512,8192):
    rows=[x for x in pairs if x['load']==level and x['tokens']==n]
    self.assertEqual(sum(x['order'][0]=='pd-local' for x in rows),len(rows)//2)
    self.assertTrue(all(x['body']['max_tokens']==32 for x in rows))
 def test_selected_transport_not_available_line(self):
  self.assertFalse(client.protocol_tables('Available cuda_ipc','cuda_ipc'))
  self.assertFalse(client.protocol_tables('remote memory read into host\nzero-copy rc_mlx5','rc_mlx5'))
  self.assertTrue(client.protocol_tables('remote memory read into cuda/GPU0\nzero-copy rc_mlx5/mlx5_0','rc_mlx5'))
 def test_wrong_route_or_error_rejected(self):
  zero={m:0 for m in (client.COUNT,client.SIZE,*client.w.FAILURES)}
  before={r:dict(zero) for r in ('prefill','local','remote')};after=json.loads(json.dumps(before))
  after['local'][client.COUNT]=1;after['local'][client.SIZE]=1024
  client.check_transfer(before,after,'pd-local')
  with self.assertRaises(ValueError):client.check_transfer(before,after,'pd-remote')
  after['prefill'][client.w.FAILURES[0]]=1
  with self.assertRaises(ValueError):client.check_transfer(before,after,'pd-local')
 def test_sse_parser_requires_usage_done_and_generated_text(self):
  config={'prefill':'p:8100','endpoints':{'pd-local':'http://example'}}
  body={'stream':True,'prompt':[1,2],'max_tokens':1}
  class Response(io.BytesIO):status=200
  events=[{'choices':[{'text':''}]},{'choices':[{'text':'Hello'}]},{'choices':[],'usage':{'prompt_tokens':2,'completion_tokens':1}}]
  raw=b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)+b'data: [DONE]\n\n'
  with patch.object(client.urllib.request,'urlopen',return_value=Response(raw)):
   result=client.request(config,'pd-local',body)
   self.assertEqual(result['text'],'Hello');self.assertGreater(result['ttft_seconds'],0)
  for broken in [raw.replace(b'data: [DONE]',b': missing'),raw.replace(b'"completion_tokens": 1',b'"completion_tokens": 2')]:
   with patch.object(client.urllib.request,'urlopen',return_value=Response(broken)):
    with self.assertRaises(ValueError):client.request(config,'pd-local',body)
 def test_remaining_plan_cannot_add_or_change_resources(self):
  addresses=['nebius_compute_v1_gpu_cluster.local','nebius_mk8s_v1_node_group.local','nebius_mk8s_v1_node_group.remote[0]']
  plan={'resource_changes':[{'address':a,'change':{'actions':['create']}} for a in addresses],'variables':{},'configuration':{}}
  controller.validate_remaining(plan,plan)
  bad=json.loads(json.dumps(plan));bad['resource_changes'][0]['change']['actions']=['update']
  with self.assertRaises(ValueError):controller.validate_remaining(plan,bad)
  bad=json.loads(json.dumps(plan));bad['variables']['project']={'value':'other'}
  with self.assertRaises(ValueError):controller.validate_remaining(plan,bad)

if __name__=='__main__':unittest.main()

class ClientFlowReplay(unittest.TestCase):
 def test_real_client_flow_reaches_timings_and_preserves_transfer_gate(self):
  import tarfile,copy
  fixture=HERE.parent.parent/'results/2026-09-20-nvlink/evidence.tar.gz'
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)
   with tarfile.open(fixture) as t:t.extractall(out/'fixture',filter='data')
   f=out/'fixture';suite=f/'suite/suite.json';plan=json.loads(suite.read_text())
   saved=json.loads((f/'responses.json').read_text());answers={x['case_id']:x['response'] for x in saved}
   ids=['GPU-local0','GPU-local1'];payload=[0]
   zero={m:0 for m in (client.COUNT,client.SIZE,*client.w.FAILURES,client.RUNNING,client.WAITING)}
   state={role:dict(zero) for role in ('prefill','local','remote')}
   def ev(config):
    lines=[]
    for index,gpu in enumerate(ids):
     lines.append(f'GPU {index}: (UUID: {gpu})')
     for link in range(18):
      for direction in ('Tx','Rx'):
       value=payload[0]//1024 if link==0 and ((index==0 and direction=='Tx') or (index==1 and direction=='Rx')) else 0
       lines.append(f'Link {link}: Data {direction}: {value} KiB')
    base={'identity':{'stdout':'stable'},'processes_alive':True,'nvlink':{'stdout':'\n'.join(lines)},'topology':{'stdout':'GPU0 X NV18\nGPU1 NV18 X'}}
    return {'local':{**base,'selected_gpus':ids,'protocol':'remote memory read into cuda/GPU0\nzero-copy cuda_ipc/cuda'},'remote':{**base,'selected_gpus':['GPU-remote'],'protocol':'remote memory read into cuda/GPU0\nzero-copy rc_mlx5/mlx5_0'}}
   def req(config,route,body):
    if route.startswith('pd-'):
     role=route[3:];state[role][client.COUNT]+=1;state[role][client.SIZE]+=1024
     if role=='local':payload[0]+=1024
    if body.get('stream'):return {'status':200,'ttft_seconds':.01,'completion_seconds':.02,'usage':{'prompt_tokens':len(body['prompt']),'completion_tokens':32}}
    case=next(x for x in plan['cases'] if x['request_body']==body)
    return {'status':200,'body':answers[case['case_id']]}
   original=client.plan_pairs
   with patch.object(client,'evidence',side_effect=ev),patch.object(client,'snapshot',side_effect=lambda _:copy.deepcopy(state)),patch.object(client,'request',side_effect=req),patch.object(client.time,'sleep'),patch.object(client,'plan_pairs',side_effect=lambda cases:[original(cases)[0],original(cases)[12]]):
    client.run({},suite,out/'result',10**12)
   self.assertTrue((out/'result/qualified.json').exists());result=json.loads((out/'result/complete.json').read_text());self.assertEqual(result['timed_requests'],4)
   self.assertEqual(len(json.loads((out/'result/qualification.json').read_text())),32)
   # Exercise the independent reader against a complete synthetic timing block.
   summarizer=load('paired_summary','summarize-paired-hosts.py')
   folder=out/'result';timing_plan=json.loads((folder/'timing-plan.json').read_text());timing_plan['pairs']=original(plan['cases']);(folder/'timing-plan.json').write_text(json.dumps(timing_plan))
   old=json.loads((folder/'timings.json').read_text());new=[r for r in old if r['warmup']]
   for pair in timing_plan['pairs']:
    for route in pair['order']:
     row=copy.deepcopy(next(r for r in old if not r['warmup'] and r['route']==route))
     row.update(pair_id=pair['id'],request=pair['body'],load=pair['load'],tokens=pair['tokens'],background=[{}]*pair['load'])
     row['before']['local'][client.RUNNING]=pair['load'];row['response']['usage']={'prompt_tokens':pair['tokens'],'completion_tokens':32}
     row['response']['events']=[{'elapsed_seconds':.01,'data':{'choices':[{'text':'first'}]}}]
     new.append(row)
   (folder/'timings.json').write_text(json.dumps(new));summary=summarizer.summarize(folder,suite)
   self.assertEqual(summary['complete_pairs'],48);self.assertTrue(summary['all_measurements_complete'])
   self.assertTrue(all(x['mean_remote_minus_local_ms']==0 for x in summary['groups']))
   new[-1]['request']=dict(new[-1]['request'],max_tokens=99);(folder/'timings.json').write_text(json.dumps(new))
   with self.assertRaisesRegex(ValueError,'Measurement request changed'):summarizer.summarize(folder,suite)


class ControllerReplay(unittest.TestCase):
 def replay(self,bad_guard=False):
  import subprocess,tarfile,time
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp);plan=run/'original.tfplan';plan.write_text('fixture');suite=run/'suite.json';suite.write_text('{}')
   session={'profile':controller.PROFILE,'project_id':'p','terraform_dir':'/no-real-tf','cleanup_start_deadline_unix':time.time()+2400,'terraform_plan_path':str(plan)}
   (run/'session.json').write_text(json.dumps(session));c=controller.Controller(run);calls=[];gpu_applied=[]
   addresses=['nebius_compute_v1_gpu_cluster.local','nebius_mk8s_v1_node_group.local','nebius_mk8s_v1_node_group.remote[0]']
   tfplan={'resource_changes':[{'address':a,'change':{'actions':['create']}} for a in addresses],'variables':{},'configuration':{}}
   buf=io.BytesIO()
   with tarfile.open(fileobj=buf,mode='w:gz') as t:
    data=b'{"fixture":true}';m=tarfile.TarInfo('fixture.json');m.size=len(data);t.addfile(m,io.BytesIO(data))
   def rpc(cmd,**kw):
    calls.append(cmd);text=''
    if 'tar' in cmd:return subprocess.CompletedProcess(cmd,0,buf.getvalue(),b'')
    if 'show' in cmd and str(plan) not in cmd and not any(x.endswith('gpu.tfplan') for x in cmd):
     text=json.dumps({'values':{'root_module':{'resources':[{'address':'nebius_mk8s_v1_cluster.topology','values':{'id':'c'}},{'address':'nebius_mk8s_v1_node_group.cpu[0]','values':{'id':'cpu'}}]}}})
    elif 'show' in cmd:text=json.dumps(tfplan)
    elif 'plan' in cmd:
     Path(next(x[5:] for x in cmd if x.startswith('-out='))).write_text('remaining')
    elif 'output' in cmd:text=json.dumps({'node_group_ids':{'value':{'cpu':'cpu','local':'local','remote':'remote'}}})
    elif 'nodes' in cmd:
     text=json.dumps({'items':[{'metadata':{'labels':{'nebius.com/node-group-id':r,'kubernetes.io/hostname':r}},'status':{'conditions':[{'type':'Ready','status':'True'},{'type':'NebiusGPUError','status':'False'}],'allocatable':{'nvidia.com/gpu':'8'}}} for r in ('cpu','local','remote')]})
    elif '/results/guard-ready.json' in cmd:
     text=json.dumps({'armed':True,'cluster':'wrong' if bad_guard else 'c','deadline':session['cleanup_start_deadline_unix'],'gpu_groups_present_at_arm':[]})
    elif 'pods' in cmd:text=json.dumps({'items':[{'metadata':{'name':r},'status':{'podIP':ip}} for r,ip in [('local','10.0.0.1'),('remote','10.0.0.2')]]})
    return subprocess.CompletedProcess(cmd,0,text,'')
   c.rpc=rpc;c.sleep=lambda _:None
   def gpu_apply(*args):
    self.assertTrue((run/'cloud-guard-ready.json').is_file());gpu_applied.append(True);return 0
   with patch.object(controller.pilot,'apply_plan',side_effect=gpu_apply):
    if bad_guard:
     with self.assertRaises(ValueError):c.execute(suite)
     self.assertFalse(gpu_applied)
    else:
     c.execute(suite);self.assertEqual(gpu_applied,[True]);self.assertTrue((c.out/'client/fixture.json').exists())
 def test_rehearsal_collects_all_artifacts(self):self.replay()
 def test_wrong_cloud_guard_prevents_gpu_allocation(self):self.replay(True)
