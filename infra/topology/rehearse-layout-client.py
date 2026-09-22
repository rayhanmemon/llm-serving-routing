#!/usr/bin/env python3
"""Exercise all layout-client epochs through real loopback HTTP/SSE fixtures."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import urllib.parse
import urllib.request

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('layout',HERE/'layout-client.py');l=importlib.util.module_from_spec(sp);sp.loader.exec_module(l)
c=l.c

def rehearsal(suite_path,config_path,out):
    suite=json.loads(gzip.decompress(suite_path.read_bytes()));config,arch=c.settings.load_config(config_path)
    names=(c.e.COUNT,c.e.BYTES,c.e.TIME_COUNT,c.e.TIME_SUM,*c.e.FAILURES,c.RUNNING,c.WAITING,
           *[m+suffix for m in l.METRICS for suffix in ('_sum','_count')])
    state={r:dict.fromkeys(names,0) for r in ('prefill','local')};epoch=['default-a'];calls=[0]
    def observed():
        log='\n'.join(f'(Worker_TP{i} pid={100+i}) ready' for i in range(4))
        if epoch[0]=='packed':log+='\n'+('\nAllocating a cross layer KV cache of shape (26147, 2, 64, 64, 256)'*4)
        logs={f'ucx-decode.{100+i}.log':f'[123] [local:{100+i}:0] | cfg#2 | remote memory read into cuda/GPU{i} from cuda/dev[0] |\n[123] [local:{100+i}:0] | 1..inf | zero-copy | cuda_ipc/cuda |\n' for i in range(4)}
        return {'devices':{'prefill':[f'GPU-P{i}' for i in range(4)],'decode':[f'GPU-D{i}' for i in range(4)]},
                'identity':'stable','processes_alive':True,
                'topology':'\n'.join(f'GPU{i} '+' '.join('X' if i==j else 'NV18' for j in range(8)) for i in range(8)),
                'engine_logs':{'prefill':log,'decode':log},'ucx_logs':logs}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _,host,port,path=self.path.split('/',3)
            if path=='evidence':body=json.dumps(observed()).encode()
            elif path=='metrics':body='\n'.join(f'{k} {v}' for k,v in state['prefill' if port=='8100' else 'local'].items()).encode()
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            _,host,port,path=self.path.split('/',3)
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            case=next((x for x in suite['cases'] if x['request_body']['prompt']==body['prompt']),None)
            if case is None:self.send_error(400);return
            calls[0]+=1
            if port=='8000':
                if self.headers.get('x-prefiller-host-port')!='fixture:8100':self.send_error(400);return
                n=len(body['prompt']);desc=n//64 if epoch[0]=='packed' else n
                times=(desc,.006 if epoch[0]=='packed' else .413,.03 if epoch[0]=='packed' else .459)
                for name,value in zip(l.METRICS,times):state['local'][name+'_count']+=4;state['local'][name+'_sum']+=4*value
                # Transfer-duration count is already incremented above.
                state['local'][c.e.COUNT]+=4
                state['local'][c.e.BYTES]+=c.settings.cache_bytes_per_rank(arch,n)*4
            completion=body['max_tokens'] if body.get('ignore_eos') else min(8,body['max_tokens'])
            events=[{'choices':[{'text':case['gold_path']}]},{'choices':[],'usage':{'prompt_tokens':len(body['prompt']),'completion_tokens':completion}}]
            raw=b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)+b'data: [DONE]\n\n'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    original=urllib.request.urlopen
    def redirect(value,*args,**kwargs):
        url=value.full_url if isinstance(value,urllib.request.Request) else value;p=urllib.parse.urlsplit(url)
        target=f'http://127.0.0.1:{server.server_port}/{p.hostname}/{p.port}{p.path}'
        v=urllib.request.Request(target,data=value.data,headers=dict(value.header_items()),method=value.get_method()) if isinstance(value,urllib.request.Request) else target
        return original(v,*args,**kwargs)
    try:
        with patch.object(c.urllib.request,'urlopen',side_effect=redirect):
            for name in ('default-a','packed','default-b'):
                epoch[0]=name
                for metrics in state.values():metrics.update(dict.fromkeys(names,0))
                l.run('fixture',config_path,suite_path,out,name,time.time()+600)
                result=json.loads((out/name/'complete.json').read_text())
                assert result['default_slow_reproduced']==(name!='packed')
                assert result['requests']==26 and result['timed_requests']==12
        assert calls[0]==78
        return {'full_http_rehearsal_passed':True,'requests':78,'gpu_execution':False}
    finally:server.shutdown();server.server_close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--suite',type=Path,required=True);p.add_argument('--config',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(rehearsal(a.suite,a.config,a.out)))
