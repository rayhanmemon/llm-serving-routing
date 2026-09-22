#!/usr/bin/env python3
"""Replay four restarts with detached TERM-ignoring Linux workers; no GPUs."""
import json,os,shutil,subprocess,tempfile,time
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='layout-lifecycle-') as td:
 p=Path(td);code=p/'code';out=p/'out';code.mkdir();out.mkdir()
 source=Path(__file__).resolve().parent
 for name in ('layout-engines.py','tp4-engines.py'):shutil.copy(source/name,code/name)
 v=code/'vllm';v.mkdir();(v/'__init__.py').write_text('')
 for rel in ('v1/worker/kv_connector_model_runner_mixin.py','distributed/kv_transfer/kv_connector/v1/nixl/connector.py','distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py','v1/worker/gpu/model_runner.py','v1/worker/gpu/attn_utils.py','v1/worker/utils.py','v1/core/kv_cache_utils.py'):
  f=v/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('# lifecycle fixture\n')
 (code/'config.json').write_text('{"vllm_version":"0.29.0","kv_cache_layout":"LBHNC","model":"fixture","revision":"fixture"}');(code/'model-config.json').write_text('{}')
 (code/'nvidia-smi').write_text('#!/bin/sh\nexit 0\n');(code/'nvidia-smi').chmod(0o755)
 (code/'validate-tp4-args.py').write_text('# Native parser is tested separately; no GPU here.\n')
 (code/'huggingface_hub.py').write_text('def snapshot_download(**kwargs):pass\n')
 (code/'torch.py').write_text('''import os
class Device:
 def __init__(self,i):self.uuid=os.environ['TP4_ROLE']+str(i)
class cuda:
 @staticmethod
 def device_count():return 4
 @staticmethod
 def get_device_properties(i):return Device(i)
''')
 (code/'tp4-config.py').write_text('''import json
from pathlib import Path
def load_config(path):return json.loads(Path(path).read_text()),{}
def engine_specs(config,role,ip):
 return [{'role':r,'command':['python3','/probe/fixture-engine.py'],'env':{}} for r in ('prefill','decode')]
''')
 (code/'fixture-engine.py').write_text('''import json,os,signal,time,subprocess,sys
from pathlib import Path
o=Path(os.environ['TP4_RESULTS_DIR']);role=os.environ['TP4_ROLE']
subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],start_new_session=True)
(o/(role+'-fixture-ready.json')).write_text(json.dumps({'pid':os.getpid()}))
def stop(*_):
 (o/(role+'-fixture-stopped.json')).write_text('{}');raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
while True:time.sleep(.1)
''')
 name='router-layout-lifecycle'
 proc=subprocess.Popen(['docker','run','--rm','--name',name,'--network','none','-e','PYTHONPATH=/probe','-e','ENGINE_ROLE=local','-e','POD_IP=127.0.0.1','-e','PATH=/probe:/usr/local/bin:/usr/bin:/bin','-v',str(code)+':/probe:ro','-v',str(out)+':/results','--entrypoint','python3','python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b','/probe/layout-engines.py','--config','/probe/config.json'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 try:
  for epoch in ('default-a','packed-doc','packed','default-b'):
   until=time.time()+35
   while not all((out/epoch/(role+'-fixture-ready.json')).exists() for role in ('prefill','decode')):
    if time.time()>until:raise RuntimeError('Lifecycle ready timeout '+epoch)
    time.sleep(.2)
   actual=json.loads((out/epoch/'effective-config.json').read_text())
   assert actual['kv_cache_layout']=={'packed':'BHLNC','packed-doc':'BLHNC'}.get(epoch,'LBHNC')
   (out/(epoch+'.advance')).touch()
   until=time.time()+25
   while not (out/epoch/'shutdown.json').exists():
    if time.time()>until:raise RuntimeError('Lifecycle shutdown timeout')
    time.sleep(.2)
   shutdown=json.loads((out/epoch/'shutdown.json').read_text());assert shutdown['drained']
   assert any(x['signal']==9 for x in shutdown['signals'])
  result={'restart_lifecycle_passed':True,'epochs':4,'both_supervisors_real':True,'gpu_execution':False,'scope':'Actual layout and TP4 supervisors; real detached TERM-ignoring Linux children. Model/download/parser/CUDA inventory substituted; native parser checked separately.',
          'sha256':{n:__import__('hashlib').sha256((source/n).read_bytes()).hexdigest() for n in ('layout-engines.py','tp4-engines.py','rehearse-layout-lifecycle.py')}}
  Path('/tmp/v029-lifecycle.json').write_text(json.dumps(result));print(json.dumps(result))
 finally:
  subprocess.run(['docker','stop','--time','5',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
  proc.wait(timeout=15)
