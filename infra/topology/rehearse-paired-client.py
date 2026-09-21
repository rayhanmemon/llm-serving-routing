#!/usr/bin/env python3
"""Exercise the real client over HTTP/SSE against a local synthetic server.

No GPU, cloud or performance claims. Tests the full qualification -> warmup ->
96 foreground timings -> independent summary path and concurrent load control.
"""
import argparse,copy,importlib.util,json,threading,time,tarfile,tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
HERE=Path(__file__).resolve().parent

def load(name,file):
 s=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
c=load('paired','paired-client.py');summary=load('summary','summarize-paired-hosts.py')

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
 suite=HERE.parent.parent/'workloads/paired-locality/suite.json';plan=json.loads(suite.read_text())
 with tempfile.TemporaryDirectory() as temp:
  with tarfile.open(HERE.parent.parent/'results/2026-09-20-nvlink/evidence.tar.gz') as t:t.extractall(temp,filter='data')
  answers={x['case_id']:x['response'] for x in json.loads((Path(temp)/'responses.json').read_text())}
 prompts={tuple(x['request_body']['prompt']):x['case_id'] for x in plan['cases']}
 state={r:{k:0 for k in (c.COUNT,c.SIZE,*c.w.FAILURES,c.RUNNING,c.WAITING)} for r in ('prefill','local','remote')};nv=[0];lock=threading.Lock()
 def ev(role):
  ids=['GPU-a','GPU-b'] if role=='local' else ['GPU-c'];lines=[]
  for i,gpu in enumerate(ids):
   lines.append(f'GPU {i}: (UUID: {gpu})')
   for link in range(18):
    for direction in ('Tx','Rx'):
     value=nv[0]//1024 if role=='local' and link==0 and ((i==0 and direction=='Tx') or (i==1 and direction=='Rx')) else 0
     lines.append(f'Link {link}: Data {direction}: {value} KiB')
  return {'selected_gpus':ids,'identity':{'stdout':'stable '+role},'processes_alive':True,'topology':{'stdout':'GPU0 X NV18\nGPU1 NV18 X'},'nvlink':{'stdout':'\n'.join(lines)},'protocol':'remote memory read into cuda/GPU0\nzero-copy '+('cuda_ipc/cuda' if role=='local' else 'rc_mlx5/mlx5_0')}
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def send(self,body,kind='application/json'):
   if not isinstance(body,bytes):body=json.dumps(body).encode()
   self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  def do_GET(self):
   _,kind,role=self.path.split('/')
   with lock:
    data=(('\n'.join(f'{k} {v}' for k,v in state[role].items())+'\n').encode() if kind=='metrics' else ev(role))
   self.send(data,'text/plain' if kind=='metrics' else 'application/json')
  def do_POST(self):
   route=self.path.split('/')[1];role=route.split('-')[1];body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
   with lock:
    state[role][c.RUNNING]+=1
    if route.startswith('pd-'):
     state[role][c.COUNT]+=1;state[role][c.SIZE]+=1024
     if role=='local':nv[0]+=1024
   try:
    if not body.get('stream'):
     self.send(answers[prompts[tuple(body['prompt'])]]);return
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
    def event(value):self.wfile.write(b'data: '+json.dumps(value).encode()+b'\n\n');self.wfile.flush()
    event({'choices':[{'text':''}]})
    # Background lasts comfortably through a foreground request; all real
    # HTTP streams, thread futures, metrics checks and response parsing run.
    time.sleep(.35 if body['max_tokens']>32 else .005)
    event({'choices':[{'text':'generated'}]})
    event({'choices':[],'usage':{'prompt_tokens':len(body['prompt']),'completion_tokens':body['max_tokens']}})
    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
   finally:
    with lock:state[role][c.RUNNING]-=1
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
 config={'prefill':'fixture:8100','endpoints':{r:base+'/'+r for r in ('direct-local','direct-remote','pd-local','pd-remote')},'metrics':{r:base+'/metrics/'+r for r in state},'evidence':{r:base+'/evidence/'+r for r in ('local','remote')}}
 try:
  c.run(config,suite,a.out,time.time()+400)
  result=summary.summarize(a.out,suite)
  assert result['all_measurements_complete'] and result['complete_pairs']==48
  (a.out/'rehearsal-summary.json').write_text(json.dumps(result,indent=2)+'\n')
  print('OFFLINE PASS: real HTTP/SSE,32 qualification calls,8 warmups,96 foreground timings,96 background requests,48 complete pairs; simulated engines only.')
 finally:server.shutdown()
if __name__=='__main__':main()
