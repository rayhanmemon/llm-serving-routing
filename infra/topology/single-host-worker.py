#!/usr/bin/env python3
"""One-container, two-GPU qualification. Runs inside the owned test Pod only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

MODEL = 'Qwen/Qwen3-0.6B'
REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
UCX_TLS = 'cuda_ipc,cuda_copy,sm,self,tcp'
FAILURES = ('vllm:nixl_num_failed_transfers_total', 'vllm:nixl_num_failed_notifications_total',
            'vllm:nixl_num_kv_expired_reqs_total')
COUNT = 'vllm:nixl_bytes_transferred_count'
SIZE = 'vllm:nixl_bytes_transferred_sum'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def ipc_read_tables(log):
    lines = log.splitlines()
    return ['\n'.join(lines[i:i+8]) for i, line in enumerate(lines)
            if 'remote memory read' in line and 'cuda' in line
            and any('cuda_ipc' in row and 'zero-copy' in row for row in lines[i+1:i+8])]


def metrics(text):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name = line.split('{')[0].split()[0]
        if name in (COUNT, SIZE, *FAILURES):
            result[name] = result.get(name, 0) + float(line.rsplit(' ', 1)[1])
    for name in (COUNT, SIZE, *FAILURES):
        if name not in result:
            raise RuntimeError('Missing metric ' + name)
    return result


def prepare_suite(out):
    import importlib.util
    spec = importlib.util.spec_from_file_location('suite', Path(__file__).with_name('known-answer-suite.py'))
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    suite.MODEL, suite.REVISION = MODEL, REVISION
    suite.prepare(out, criterion='direct-parity')


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, default=Path('/results'))
    p.add_argument('--execute', action='store_true')
    a = p.parse_args(argv)
    if not a.execute:
        print(json.dumps({'model': MODEL, 'revision': REVISION, 'gpus': 2,
                          'ucx_tls': UCX_TLS, 'cloud_actions': False}))
        return
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    code = Path(__file__).resolve().parent
    processes = []
    opened = []
    deadline = time.monotonic() + 2100

    def phase(name):
        write(out/'status.json', {'phase': name, 'time': time.time()})
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('Worker deadline reached')
        return left
    def command(cmd, name, timeout=30, check=True):
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=min(timeout, remaining()))
        (out/(name+'.stdout')).write_text(r.stdout)
        (out/(name+'.stderr')).write_text(r.stderr)
        if check:
            r.check_returncode()
        return r
    def start(cmd, name, gpu):
        f = (out/(name+'.log')).open('w')
        opened.append(f)
        env = {**os.environ, 'CUDA_VISIBLE_DEVICES': str(gpu), 'HF_HOME': '/cache',
               'UCX_TLS': UCX_TLS, 'UCX_PROTO_INFO': 'y', 'UCX_LOG_LEVEL': 'info',
               'UCX_NET_DEVICES': 'all'}
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=env)
        processes.append(proc)
        return proc
    def get(url):
        with urllib.request.urlopen(url, timeout=min(10, remaining())) as response:
            return response.read().decode()
    def wait_ready(url, proc, seconds=900):
        end = time.monotonic() + min(seconds, remaining())
        while time.monotonic() < end:
            if proc.poll() is not None:
                raise RuntimeError('Server process exited: ' + str(proc.returncode))
            try:
                get(url)
                return
            except Exception:
                time.sleep(2)
        raise TimeoutError('Readiness: ' + url)
    def snap():
        return metrics(get('http://127.0.0.1:8200/metrics'))
    def counters(name):
        return command(['nvidia-smi','nvlink','-gt','d'], name, check=False)

    try:
        phase('topology')
        command(['nvidia-smi','-L'], 'gpu-identities')
        command(['nvidia-smi','topo','-m'], 'gpu-topology')
        command(['nvidia-smi','nvlink','-s'], 'nvlink-status')
        # This small helper tells CUDA's selected GPU identities, not nvidia-smi's host indexes.
        for gpu in (0, 1):
            command([sys.executable,str(code/'raw-ipc.py'),'inspect','--device',str(gpu),
                     '--stem',str(out/('inspect'+str(gpu))),'--session-id','single-host',
                     '--result',str(out/('gpu'+str(gpu)+'.json'))], 'inspect'+str(gpu))
        ids = [json.loads((out/('gpu'+str(g)+'.json')).read_text())['selected_gpu_uuid'] for g in (0,1)]
        if len(set(ids)) != 2:
            raise RuntimeError('Workers must use different physical GPUs')
        write(out/'selected-gpus.json', ids)
        phase('raw-cuda-ipc')
        stem = str(out/'raw')
        common = ['--device','0','--stem',stem,'--session-id','single-host','--timeout','90']
        producer = start([sys.executable,str(code/'raw-ipc.py'),'producer',*common,
                          '--result',str(out/'raw-producer.json')], 'raw-producer', 0)
        consumer = start([sys.executable,str(code/'raw-ipc.py'),'consumer',*common,
                          '--result',str(out/'raw-consumer.json')], 'raw-consumer', 1)
        if consumer.wait(timeout=110) or producer.wait(timeout=10):
            raise RuntimeError('Raw CUDA IPC failed')
        phase('nixl-read')
        producer = start([sys.executable,str(code/'rdma-read-probe.py'),'producer','--out',
                          str(out/'nixl-producer'),'--lifetime','240'], 'nixl-producer', 0)
        wait_ready('http://127.0.0.1:8123/metadata', producer, 120)
        consumer = start([sys.executable,str(code/'rdma-read-probe.py'),'consumer','--out',
                          str(out/'nixl-consumer'),'--peer','http://127.0.0.1:8123'], 'nixl-consumer', 1)
        if consumer.wait(timeout=180):
            raise RuntimeError('NIXL READ failed')
        if not ipc_read_tables((out/'nixl-consumer.log').read_text()):
            raise RuntimeError('Raw NIXL READ did not select CUDA IPC zero-copy')
        producer.terminate()
        producer.wait(timeout=10)
        phase('model-startup')
        servers = []
        for role, gpu, port, side in [('prefill',0,8100,5600),('decode',1,8200,5601)]:
            kv = json.dumps({'kv_connector':'NixlConnector','kv_role':'kv_both',
                             'kv_buffer_device':'cuda','kv_load_failure_policy':'fail',
                             'kv_connector_extra_config':{'backends':['UCX']}})
            cmd = ['env','VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1',
                   'VLLM_NIXL_SIDE_CHANNEL_PORT='+str(side),'vllm','serve',MODEL,
                   '--revision',REVISION,'--tokenizer-revision',REVISION,'--port',str(port),
                   '--tensor-parallel-size','1','--dtype','bfloat16','--block-size','64',
                   '--enforce-eager','--max-model-len','16384','--max-num-seqs','4',
                   '--max-num-batched-tokens','8192','--gpu-memory-utilization','0.2',
                   '--no-enable-prefix-caching','--kv-transfer-config',kv]
            servers.append(start(cmd, role, gpu))
        for proc, port in zip(servers, (8100,8200)):
            wait_ready(f'http://127.0.0.1:{port}/v1/models', proc)
        # Use the same prospective case-generation method with this model's pinned tokenizer.
        prepare_suite(out/'suite')
        plan = json.loads((out/'suite/suite.json').read_text())
        write(out/'scope.json', {'model':MODEL,'revision':REVISION,'routes':['direct','pd'],
                                'criterion':'same normalized output per case; gold separately reported',
                                'scope':'local transport qualification, no remote or router-policy claim'})
        processes_before = command(['nvidia-smi','--query-compute-apps=pid,gpu_uuid,process_name',
                                    '--format=csv,noheader'], 'compute-processes-before').stdout
        prefill_before = metrics(get('http://127.0.0.1:8100/metrics'))
        phase('real-pd')
        rows = []
        counters('nvlink-idle-before')
        time.sleep(1)
        counters('nvlink-idle-after')
        for case in plan['cases']:
            pair = {}
            for route, port in [('direct',8200),('pd',8000)]:
                before = snap()
                headers = {'Content-Type':'application/json'}
                if route == 'pd':
                    headers['x-prefiller-host-port'] = '127.0.0.1:8100'
                label = case['case_id']+'-'+route
                counters('nvlink-'+label+'-before')
                req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/completions',
                                             data=json.dumps(case['request_body']).encode(),headers=headers)
                start_at = time.monotonic()
                with urllib.request.urlopen(req,timeout=90) as response:
                    body = json.load(response)
                    status = response.status
                elapsed = time.monotonic()-start_at
                counters('nvlink-'+label+'-after')
                for _ in range(30):
                    after = snap()
                    if after[COUNT]-before[COUNT] == int(route=='pd'):
                        break
                    time.sleep(.2)
                row = {'case_id':case['case_id'],'route':route,'request':case['request_body'],
                       'status':status,'response':body,'completion_seconds':elapsed,
                       'before':before,'after':after,'gold':case['gold_text']}
                rows.append(row)
                write(out/'responses.json',rows)
                assert status == 200
                assert body['usage']['prompt_tokens'] == case['input_tokens']
                assert body['usage']['completion_tokens'] == len(case['gold_token_ids'])
                assert after[COUNT]-before[COUNT] == int(route=='pd')
                assert all(after[x] == before[x] for x in FAILURES)
                assert after[SIZE] > before[SIZE] if route=='pd' else after[SIZE] == before[SIZE]
                pair[route] = body['choices'][0]['text'].strip()
            assert pair['direct'] == pair['pd'], 'Output mismatch: '+case['case_id']
        prefill_after = metrics(get('http://127.0.0.1:8100/metrics'))
        write(out/'prefill-metrics.json', {'before':prefill_before,'after':prefill_after})
        assert all(prefill_before[x] == prefill_after[x] for x in FAILURES)
        processes_after = command(['nvidia-smi','--query-compute-apps=pid,gpu_uuid,process_name',
                                   '--format=csv,noheader'], 'compute-processes-after').stdout
        assert sorted(processes_before.splitlines()) == sorted(processes_after.splitlines()), 'GPU processes changed'
        assert all(identity in processes_after for identity in ids), 'GPU worker identity missing'
        tables = ipc_read_tables((out/'decode.log').read_text())
        if not tables:
            raise RuntimeError('Real KV READ did not select CUDA IPC zero-copy')
        write(out/'worker-result.json',{'inference_and_cuda_ipc_passed':True,'requests':len(rows),
               'pd_transfers':8,'physical_gpu_uuids':ids,'selected_protocol_tables':tables,
               'nvlink_hardware_counters_require_review':True,
               'limits':'No TTFT benchmark, remote comparison, or policy gain.'})
        phase('evidence-ready')
    except Exception as error:
        write(out/'worker-failure.json',{'error':repr(error),'time':time.time()})
        phase('failed-evidence-ready')
    finally:
        for proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for f in opened:
            f.close()
        # Leave the artifact volume available until the external collector finishes.
        (out/'DONE').write_text('Collection required; see worker-result or worker-failure.')
        time.sleep(7200)


if __name__ == '__main__':
    main()
