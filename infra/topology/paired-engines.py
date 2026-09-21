#!/usr/bin/env python3
"""Fixed small-model engines, with internal evidence endpoints for the client."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('worker', HERE/'single-host-worker.py')
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
OUT = Path('/results'); OUT.mkdir(exist_ok=True)
PROCESSES = []
ROLE = os.environ['ENGINE_ROLE']
ROLES = [('prefill',0,8100,5600),('decode',1,8200,5601)] if ROLE == 'local' else [('decode',0,8200,5601)]


def evidence():
    result = {}
    for name, cmd in {
        'identity': ['nvidia-smi','--query-compute-apps=pid,gpu_uuid,process_name','--format=csv,noheader'],
        'gpu_list': ['nvidia-smi','-L'], 'topology': ['nvidia-smi','topo','-m'],
        'nvlink': ['nvidia-smi','nvlink','-gt','d'],
    }.items():
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        result[name] = {'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    result['protocol'] = (OUT/'decode.log').read_text(errors='replace')
    result['processes_alive'] = all(p.poll() is None for p in PROCESSES)
    result['selected_gpus'] = [json.loads((OUT/f'gpu{g}.json').read_text())['selected_gpu_uuid'] for _,g,_,_ in ROLES]
    return result


def main():
    # Leave UCX transport selection unrestricted, on both hosts.
    for role,gpu,port,side in ROLES:
        subprocess.run(['python3',str(HERE/'raw-ipc.py'),'inspect','--device',str(gpu),
                        '--stem',str(OUT/f'inspect{gpu}'),'--session-id','paired',
                        '--result',str(OUT/f'gpu{gpu}.json')],check=True)
        kv = {'kv_connector':'NixlConnector','kv_role':'kv_both','kv_buffer_device':'cuda',
              'kv_load_failure_policy':'fail','kv_connector_extra_config':{'backends':['UCX']}}
        cmd=['vllm','serve',w.MODEL,'--revision',w.REVISION,'--tokenizer-revision',w.REVISION,
             '--port',str(port),'--tensor-parallel-size','1','--dtype','bfloat16','--block-size','64',
             '--enforce-eager','--max-model-len','16384','--max-num-seqs','4',
             '--max-num-batched-tokens','8192','--gpu-memory-utilization','0.2',
             '--no-enable-prefix-caching','--kv-transfer-config',json.dumps(kv)]
        env={**os.environ,'CUDA_VISIBLE_DEVICES':str(gpu),'HF_HOME':'/cache',
             'VLLM_NIXL_SIDE_CHANNEL_HOST':os.environ['POD_IP'],
             'VLLM_NIXL_SIDE_CHANNEL_PORT':str(side),'UCX_PROTO_INFO':'y','UCX_LOG_LEVEL':'info'}
        env.pop('UCX_TLS',None)
        log=(OUT/(role+'.log')).open('w')
        PROCESSES.append(subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=env))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.path != '/evidence':
                    self.send_error(404); return
                body=json.dumps(evidence()).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as e:
                self.send_error(500,type(e).__name__)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('0.0.0.0',8300),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    while all(p.poll() is None for p in PROCESSES): time.sleep(2)
    raise RuntimeError('An engine process exited')


if __name__=='__main__': main()
