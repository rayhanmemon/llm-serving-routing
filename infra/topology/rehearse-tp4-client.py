#!/usr/bin/env python3
"""Run the complete TP4 client against synthetic HTTP/SSE endpoints, without GPUs."""
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
sp=importlib.util.spec_from_file_location('client',HERE/'tp4-client.py')
c=importlib.util.module_from_spec(sp);sp.loader.exec_module(c)


def rehearsal(suite_path,config_path,out):
    suite=json.loads(gzip.decompress(suite_path.read_bytes()));config,arch=c.settings.load_config(config_path)
    names=(c.e.COUNT,c.e.BYTES,c.e.TIME_COUNT,c.e.TIME_SUM,*c.e.FAILURES,c.RUNNING,c.WAITING)
    state={role:dict.fromkeys(names,0) for role in ['prefill','local','remote']}
    call_count=[0]
    def observed(role):
        devices={'decode':[f'GPU-{role}-d{i}' for i in range(4)]}
        if role=='local':devices['prefill']=[f'GPU-local-p{i}' for i in range(4)]
        log='\n'.join(f'(Worker_TP{i} pid={100+i}) ready' for i in range(4))
        transport='cuda_ipc/cuda' if role=='local' else 'rc_mlx5/mlx5_0:1'
        logs={}
        for i in range(4):
            prefix=f'[123] [{role}:{100+i}:0]'
            logs[f'ucx-decode.{100+i}.log']=(f'{prefix} | cfg#2 | remote memory read into cuda/GPU{i} from cuda/dev[0] |\n'
                f'{prefix} | 1..inf | zero-copy | {transport} |\n')
        return {'devices':devices,'identity':'stable','processes_alive':True,
                'topology':'\n'.join(f'GPU{i} '+' '.join('X' if i==j else 'NV18' for j in range(8)) for i in range(8)),
                'engine_logs':{'decode':log},'ucx_logs':logs}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _,host,port,path=self.path.split('/',3)
            role='local' if host=='local-fixture' else 'remote'
            if path=='evidence':body=json.dumps(observed(role)).encode()
            elif path=='metrics':
                target='prefill' if port=='8100' else role
                body='\n'.join(f'{k} {v}' for k,v in state[target].items()).encode()
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            _,host,port,path=self.path.split('/',3)
            role='local' if host=='local-fixture' else 'remote'
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            case=next((x for x in suite['cases'] if x['request_body']['prompt']==body['prompt']),None)
            if case is None:self.send_error(400);return
            call_count[0]+=1
            if port=='8000':
                if self.headers.get('x-prefiller-host-port')!='local-fixture:8100':self.send_error(400);return
                state[role][c.e.COUNT]+=4;state[role][c.e.TIME_COUNT]+=4
                state[role][c.e.BYTES]+=c.settings.cache_bytes_per_rank(arch,len(body['prompt']))*4
                state[role][c.e.TIME_SUM]+=.04
            completion=body['max_tokens'] if body.get('ignore_eos') else 8
            data=[{'choices':[{'text':case['gold_path']}]},
                  {'choices':[],'usage':{'prompt_tokens':len(body['prompt']),'completion_tokens':completion}}]
            raw=b''.join(b'data: '+json.dumps(event).encode()+b'\n\n' for event in data)+b'data: [DONE]\n\n'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    original=urllib.request.urlopen
    def redirected(value,*args,**kwargs):
        url=value.full_url if isinstance(value,urllib.request.Request) else value
        parsed=urllib.parse.urlsplit(url)
        actual=f'http://127.0.0.1:{server.server_port}/{parsed.hostname}/{parsed.port}{parsed.path}'
        if isinstance(value,urllib.request.Request):
            value=urllib.request.Request(actual,data=value.data,headers=dict(value.header_items()),method=value.get_method())
        else:value=actual
        return original(value,*args,**kwargs)
    try:
        with patch.object(c.urllib.request,'urlopen',side_effect=redirected):
            c.run({'local':'local-fixture'},suite_path,config_path,out,'local',time.time()+300)
            c.run({'local':'local-fixture','remote':'remote-fixture'},suite_path,config_path,out,'remote',time.time()+300)
            c.run({'local':'local-fixture','remote':'remote-fixture'},suite_path,config_path,out,'timings',time.time()+300)
        final=json.loads((out/'timings/complete.json').read_text())
        if call_count[0]!=144 or final['timed_requests']!=96 or final['warmups']!=16:raise ValueError('Incomplete HTTP rehearsal')
        result={'full_http_rehearsal_passed':True,'requests':144,'timed_requests':96,'gpu_execution':False,
                'scope':'Synthetic HTTP/SSE, metric and protocol fixtures; no serving performance result'}
        c.write(out/'rehearsal.json',result);return result
    finally:server.shutdown();server.server_close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--suite',type=Path,required=True);p.add_argument('--config',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(rehearsal(a.suite,a.config,a.out)))
