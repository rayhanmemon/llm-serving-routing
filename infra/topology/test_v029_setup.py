import copy,importlib.util,json,tempfile,unittest,time,subprocess
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent

def module(name):
 s=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
p=module('tp4-config');l=module('layout-client');router=module('render-tp4-router')
CFG=HERE.parent.parent/'workloads/tp4-v029/config.json'
controller=module('run-layout-local')

class V029Tests(unittest.TestCase):
 def capacity(self,available=1,state='DATA_STATE_FRESH',preset='8gpu-128vcpu-1600gb'):
  return {'items':[{'spec':{'region':'us-central1','fabric':'us-central1-a','compute_instance':{'platform':'gpu-h200-sxm','preset':{'name':preset}}},'status':{'preemptible':{'data_state':state,'available':available,'effective_at':'2026-09-22T05:00:00Z'}}}]}
 def test_capacity_distinguishes_one_eight_gpu_vm_from_single_gpu_count(self):
  controller.select_capacity(self.capacity())
  with self.assertRaises(ValueError):controller.select_capacity(self.capacity(8,preset='1gpu-16vcpu-200gb'))
 def test_capacity_rejects_nonpositive_or_stale_advice(self):
  for data in (self.capacity(0),self.capacity(8,state='DATA_STATE_STALE')):
   with self.assertRaises(ValueError):controller.select_capacity(data)
 def test_capacity_must_postdate_failed_placement(self):
  with self.assertRaises(ValueError):controller.select_capacity(self.capacity(),newer_than='2026-09-22T05:01:00Z')
  controller.select_capacity(self.capacity(),newer_than='2026-09-22T04:00:00Z')
 def test_v029_controller_runs_all_four_predeclared_epochs(self):
  with tempfile.TemporaryDirectory() as d:
   obj=object.__new__(controller.Controller);obj.run=Path(d);obj.k=['kubectl'];obj.cpu_node='cpu';obj.nodes_by_role={'local':'gpu'}
   obj.config_path=CFG;obj.session={'cleanup_start_deadline_unix':time.time()+5000};events=[]
   obj.allocate=lambda role:events.append('allocate-'+role);obj.apply=lambda *a:None
   obj.call=lambda cmd,*a,**k:events.append('advance' if 'touch' in cmd else 'wait') or subprocess.CompletedProcess(cmd,0,'','')
   obj.ready=lambda name:events.append('ready-'+name)
   obj.epoch=lambda name:events.append(name) or {'default_slow_reproduced':True}
   obj.collect=lambda:events.append('collect')
   with patch.object(controller.s.paired.Controller,'bootstrap',side_effect=lambda *a,**k:events.append('guard')):
    obj.execute(CFG.with_name('suite.json.gz'))
   self.assertEqual([x for x in events if x in ('default-a','packed-doc','packed','default-b')],['default-a','packed-doc','packed','default-b'])
   self.assertEqual(events.count('advance'),3);self.assertNotIn('allocate-remote',events)
 def test_explicit_runner_layout_image_and_probe_are_rendered(self):
  config,arch=p.load_config(CFG);config['kv_cache_layout']='BHLNC'
  for role in ('local','remote'):
   for spec in p.engine_specs(config,role,'10.0.0.1'):
    self.assertEqual(spec['env']['VLLM_USE_V2_MODEL_RUNNER'],'1')
    self.assertEqual(spec['env']['VLLM_KV_CACHE_LAYOUT'],'BHLNC')
    self.assertIn('v029_worker_probe.ProbedWorker',spec['command'])
  doc=p.render(CFG,{'local':'a','remote':'b'})
  self.assertIn('v029_worker_probe.py',doc['items'][1]['data'])
  for pod in doc['items'][2:]:self.assertEqual(pod['spec']['containers'][0]['image'],config['vllm_image'])
 def test_legacy_flag_or_wrong_runner_are_rejected(self):
  config,arch=p.load_config(CFG)
  for key,value in [('model_runner','V1'),('kv_cache_layout','HND'),('enable_cross_layers_blocks',True),('vllm_image','vllm:latest')]:
   bad=dict(config);bad[key]=value
   with self.assertRaises(ValueError):p.validate(bad,arch)
 def test_missing_live_rank_proof_stops_before_measurements(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.object(l.c,'evidence',return_value={'local':{'layout_probes':{}}}),patch.object(l.c,'request') as request:
    with self.assertRaisesRegex(ValueError,'Missing actual worker'):
     l.run('fixture',CFG,CFG.with_name('suite.json.gz'),Path(d),'packed',9999999999)
    request.assert_not_called()
 def test_prefill_proxy_exposes_real_host_and_only_forwards_http(self):
  cm,pod=router.prefill_proxy('actual-node','10.0.1.2')
  self.assertEqual(pod['metadata']['labels']['kubernetes.io/hostname'],'actual-node')
  self.assertEqual(pod['spec']['nodeSelector']['kubernetes.io/hostname'],'actual-node')
  self.assertEqual(pod['metadata']['labels']['llm-d.ai/role'],'prefill')
  config=router.yaml.safe_load(cm['data']['envoy.yaml'])
  socket=config['static_resources']['clusters'][0]['load_assignment']['endpoints'][0]['lb_endpoints'][0]['endpoint']['address']['socket_address']
  self.assertEqual(socket,{'address':'10.0.1.2','port_value':8100})
 def test_evaluated_policies_have_no_diagnostic_pin(self):
  with tempfile.TemporaryDirectory() as d:
   router.render('a','b','cpu','10.0.0.1',Path(d))
   for f in Path(d).glob('router-*.values.yaml'):
    self.assertNotIn('session-affinity-filter',f.read_text())
    self.assertNotIn('x-benchmark-decoder',f.read_text())
