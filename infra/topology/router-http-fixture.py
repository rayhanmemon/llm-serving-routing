#!/usr/bin/env python3
"""Synthetic HTTP only: rehearse EPP/sidecar/proxy routing, never GPU performance."""
import json,os,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
MODEL='Qwen/Qwen3-32B'
ROLE=os.environ.get('ENGINE_ROLE','local')
ACTIVE=0
ACTIVE_LOCK=threading.Lock()

def handler(role):
 class Handler(BaseHTTPRequestHandler):
  def send(self,value):
   body=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  def do_GET(self):
   if self.path=='/metrics':
    body=('\n'.join(f'{n}{{model_name="{MODEL}"}} 0' for n in ['vllm:num_requests_running','vllm:num_requests_waiting','vllm:kv_cache_usage_perc','vllm:gpu_cache_usage_perc','vllm:num_preemptions_total'])+'\nvllm:cache_config_info{block_size="64",num_gpu_blocks="26164",enable_prefix_caching="False"} 1\nvllm:lora_requests_info{max_lora="0",running_lora_adapters="",waiting_lora_adapters=""} 0\n').encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
   elif self.path=='/v1/models':self.send({'object':'list','data':[{'id':MODEL,'object':'model'}]})
   else:self.send({'status':'ok'})
  def do_POST(self):
   global ACTIVE
   if self.headers.get('Transfer-Encoding','').lower()=='chunked':
    chunks=[]
    while True:
     n=int(self.rfile.readline().split(b';')[0],16)
     if n==0:
      while self.rfile.readline().strip():pass
      break
     chunks.append(self.rfile.read(n));self.rfile.read(2)
    raw=b''.join(chunks)
   else:raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
   body=json.loads(raw);prompt=body.get('prompt',[]);n=len(prompt);count=body.get('max_tokens',32)
   print(json.dumps({'role':role,'run':self.headers.get('x-benchmark-run'),'request':self.headers.get('x-request-id'),'prompt_tokens':n,'remote_prefill':bool(body.get('kv_transfer_params'))}),flush=True)
   usage={'prompt_tokens':n,'completion_tokens':count,'total_tokens':n+count}
   if role=='prefill':
    self.send({'id':'fixture','object':'text_completion','model':MODEL,'choices':[{'index':0,'text':'x','finish_reason':'length'}],'usage':usage,'kv_transfer_params':{'remote_engine_id':'fixture-prefill','remote_block_ids':[[1]],'remote_host':os.environ['POD_IP'],'remote_port':5600,'do_remote_prefill':True,'do_remote_decode':False}});return
   if not body.get('stream'):
    self.send({'choices':[{'index':0,'text':role,'finish_reason':'length'}],'usage':usage});return
   background=count==1024
   with ACTIVE_LOCK:
    if background:ACTIVE+=1
    active=ACTIVE
   try:
    # Explicit synthetic tradeoff for exercising the real calibration gate.
    if os.environ.get('SYNTHETIC_TRADEOFF')=='1' and count==32:
     time.sleep(.03+.08*active if ROLE=='local' else .4+.03*active)
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
    for i in range(count):
     event={'id':'fixture','object':'text_completion','model':MODEL,'choices':[{'index':0,'text':role if i==0 else ' x','finish_reason':None}]}
     self.wfile.write(b'data: '+json.dumps(event).encode()+b'\n\n');self.wfile.flush();time.sleep(.01)
    self.wfile.write(b'data: '+json.dumps({'choices':[{'index':0,'text':'','finish_reason':'length'}],'usage':usage}).encode()+b'\n\ndata: [DONE]\n\n');self.wfile.flush()
   finally:
    if background:
     with ACTIVE_LOCK:ACTIVE-=1
  def log_message(self,*_):pass
 return Handler

servers=[(8200,'decode-'+ROLE)]
if ROLE=='local':servers.append((8100,'prefill'))
for port,role in servers:
 server=ThreadingHTTPServer(('0.0.0.0',port),handler(role));threading.Thread(target=server.serve_forever,daemon=True).start()
while True:time.sleep(10)
