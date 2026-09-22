#!/usr/bin/env python3
"""Replay four restarts with detached TERM-ignoring Linux workers; no GPUs."""
import json,os,shutil,subprocess,tempfile,time
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='layout-lifecycle-') as td:
 p=Path(td);code=p/'code';out=p/'out';code.mkdir();out.mkdir()
 shutil.copy(str(Path(__file__).resolve().with_name('layout-engines.py')),code/'layout-engines.py')
 v=code/'vllm';v.mkdir();(v/'__init__.py').write_text('')
 for rel in ('v1/worker/kv_connector_model_runner_mixin.py','distributed/kv_transfer/kv_connector/v1/nixl/connector.py','distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py','v1/worker/gpu/model_runner.py','v1/worker/gpu/attn_utils.py','v1/worker/utils.py','v1/core/kv_cache_utils.py'):
  f=v/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('# lifecycle fixture\n')
 (code/'config.json').write_text('{"vllm_version":"0.29.0","kv_cache_layout":"LBHNC"}');(code/'model-config.json').write_text('{}')
 (code/'nvidia-smi').write_text('#!/bin/sh\nexit 0\n');(code/'nvidia-smi').chmod(0o755)
 (code/'tp4-engines.py').write_text('''import argparse,json,os,signal,time,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--config');a=p.parse_args()
o=Path(os.environ['TP4_RESULTS_DIR']);c=json.loads(Path(a.config).read_text())
assert c['kv_cache_layout']=={'packed':'BHLNC','packed-doc':'BLHNC'}.get(o.name,'LBHNC')
subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],start_new_session=True)
(o/'fixture-ready.json').write_text(json.dumps({'pid':os.getpid(),'layout':c['kv_cache_layout']}))
def stop(*_):
 (o/'fixture-stopped.json').write_text('{}');raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
while True:time.sleep(.1)
''')
 name='router-layout-lifecycle'
 proc=subprocess.Popen(['docker','run','--rm','--name',name,'--network','none','-e','PATH=/probe:/usr/local/bin:/usr/bin:/bin','-v',str(code)+':/probe:ro','-v',str(out)+':/results','--entrypoint','python3','python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b','/probe/layout-engines.py','--config','/probe/config.json'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 try:
  for epoch in ('default-a','packed-doc','packed','default-b'):
   until=time.time()+35
   while not (out/epoch/'fixture-ready.json').exists():
    if time.time()>until:raise RuntimeError('Lifecycle ready timeout '+epoch)
    time.sleep(.2)
   (out/(epoch+'.advance')).touch()
   until=time.time()+25
   while not (out/epoch/'shutdown.json').exists():
    if time.time()>until:raise RuntimeError('Lifecycle shutdown timeout')
    time.sleep(.2)
  result={'restart_lifecycle_passed':True,'epochs':4,'gpu_execution':False,'scope':'Actual wrapper and real detached TERM-ignoring Linux children; substitute engine and GPU inventory command'}
  Path('/tmp/v029-lifecycle.json').write_text(json.dumps(result));print(json.dumps(result))
 finally:
  subprocess.run(['docker','stop','--time','5',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
  proc.wait(timeout=15)
