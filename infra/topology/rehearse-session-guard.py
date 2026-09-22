#!/usr/bin/env python3
"""Exercise the actual cloud guard in isolated Docker with an in-memory SDK substitute."""
import json,subprocess,tempfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
FAKE='''import asyncio,importlib.util,json,sys,types
from pathlib import Path
from types import SimpleNamespace as N
root=Path('/results')
class SDK:
 def __init__(self,**kwargs):pass
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
class Client:
 def __init__(self,sdk):pass
 async def list(self,request,**kwargs):
  names=json.loads((root/'groups.json').read_text())
  return N(next_page_token='',items=[N(metadata=N(id=n,name=n,parent_id='cluster')) for n in names])
 async def delete(self,request,**kwargs):
  names=json.loads((root/'groups.json').read_text());names.remove(request.id)
  (root/'groups.json').write_text(json.dumps(names))
  p=root/'deleted.json';deleted=json.loads(p.read_text()) if p.exists() else [];deleted.append(request.id);p.write_text(json.dumps(deleted))
  if request.id=='router-cpu':raise SystemExit(0)
  return N(id='fake-delete')
for name in ['nebius','nebius.sdk','nebius.api','nebius.api.nebius','nebius.api.nebius.mk8s','nebius.api.nebius.mk8s.v1']:
 sys.modules[name]=types.ModuleType(name)
sys.modules['nebius.sdk'].SDK=SDK
api=sys.modules['nebius.api.nebius.mk8s.v1'];api.NodeGroupServiceClient=Client;api.ListNodeGroupsRequest=N;api.DeleteNodeGroupRequest=N
s=importlib.util.spec_from_file_location('guard','/probe/cloud-deadline-guard.py');g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
g.metadata=lambda path:json.dumps({'parent_id':'project','service_account_id':'fake'}) if path=='instance-data' else 'fake-token'
sys.argv=['guard','--cluster','cluster','--project','project','--deadline',sys.argv[1]]
asyncio.run(g.main())
'''

def main():
 with tempfile.TemporaryDirectory(prefix='router-guard-rehearsal-') as td:
  root=Path(td);(root/'groups.json').write_text('["router-cpu"]');(root/'fake.py').write_text(FAKE)
  deadline=time.time()+120
  cmd=['docker','run','--rm','--name','router-guard-rehearsal','--network','none','-v',str(HERE)+':/probe:ro','-v',str(root)+':/results','--entrypoint','python3','python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b','/results/fake.py',str(deadline)]
  proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
  try:
   until=time.time()+20
   while not (root/'guard-ready.json').exists():
    if time.time()>until:raise TimeoutError('Initial guard acknowledgement')
    time.sleep(.1)
   first=json.loads((root/'guard-ready.json').read_text());assert first['gpu_groups_present_at_arm']==[]
   (root/'groups.json').write_text('["router-cpu","router-local"]')
   shorter=time.time()+18
   (root/'deadline-request.json').write_text(json.dumps({'cluster':'cluster','deadline':shorter}))
   until=time.time()+30
   while True:
    ack=json.loads((root/'guard-ready.json').read_text())
    if ack['deadline']==shorter:break
    if time.time()>until:raise TimeoutError('Reduced guard acknowledgement')
    time.sleep(.1)
   assert ack['gpu_groups_present_at_arm']==['router-local']
   output,_=proc.communicate(timeout=30)
   if proc.returncode:raise RuntimeError(output)
   assert json.loads((root/'deleted.json').read_text())==['router-local','router-cpu']
   print(json.dumps({'guard_protocol_rehearsed':True,'initial_arm_before_gpu':True,'shorter_deadline_acknowledged':True,'gpu_deleted_before_cpu':True,'network_disabled':True,'provider_sdk_substituted':True}))
  finally:
   subprocess.run(['docker','rm','-f','router-guard-rehearsal'],capture_output=True,timeout=10)
   proc.wait(timeout=10)

if __name__=='__main__':main()
