#!/usr/bin/env python3
"""Run pinned TP4 engines inside a prepared GPU Pod and retain diagnostic logs."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('tp4_config', HERE / 'tp4-config.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config, _ = settings.load_config(args.config)
    commands = settings.engine_specs(config, os.environ['ENGINE_ROLE'], os.environ['POD_IP'])
    out = Path(os.environ.get('TP4_RESULTS_DIR', '/results'))
    out.mkdir(parents=True, exist_ok=True)
    for command in commands:
        command["log_path"] = str(out / (command["role"] + ".log"))
        command["env"]["UCX_LOG_FILE"] = str(out / ("ucx-" + command["role"] + ".%p.log"))
    # Use the installed parser before incurring a large model download.
    subprocess.run(['python3',str(HERE/'validate-tp4-args.py'),'--config',str(args.config)],check=True)
    # One download per Pod; both local instances share the same immutable cache.
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=config['model'], revision=config['revision'],
                      cache_dir='/cache/hub', allow_patterns=['*.json', '*.safetensors', '*.jinja', '*.txt'])
    identity = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid,name,memory.total',
                               '--format=csv,noheader'], capture_output=True, text=True, check=True)
    (out / 'gpu-inventory.txt').write_text(identity.stdout)
    (out / 'engine-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    children = []
    stopping = False
    device_map = {}

    class EvidenceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/evidence':
                self.send_error(404)
                return
            try:
                observed = {}
                for name, query in {
                    'identity': ['nvidia-smi', '--query-compute-apps=pid,gpu_uuid,process_name', '--format=csv,noheader'],
                    'topology': ['nvidia-smi', 'topo', '-m'],
                    'memory': ['nvidia-smi', '--query-gpu=uuid,memory.total,memory.used', '--format=csv,noheader'],
                    'nvlink': ['nvidia-smi', 'nvlink', '-gt', 'd'],
                }.items():
                    p = subprocess.run(query, capture_output=True, text=True, timeout=15, check=True)
                    observed[name] = p.stdout
                observed.update(devices=device_map, processes_alive=all(p.poll() is None for p in children),
                                engine_logs={s['role']: Path(s['log_path']).read_text(errors='replace') for s in commands},
                                layout_probes={p.name: json.loads(p.read_text()) for p in out.glob('layout-probe-*.json')},
                                ucx_logs={p.name: p.read_text(errors='replace') for p in out.glob('ucx-*.log')})
                body = json.dumps(observed).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as error:
                self.send_error(500, type(error).__name__)
        def log_message(self, *_):
            pass

    def stop(*_):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command in commands:
            env = {**os.environ, **command['env'],'TP4_ROLE':command['role'],'TP4_RESULTS_DIR':str(out)}
            env.pop('UCX_TLS', None)
            env.pop('UCX_CUDA_IPC_GET_ZCOPY', None)
            inspect = subprocess.run(['python3', '-c',
                'import torch,json; print(json.dumps([str(torch.cuda.get_device_properties(i).uuid) for i in range(torch.cuda.device_count())]))'],
                env=env, capture_output=True, text=True, check=True, timeout=60)
            device_map[command['role']] = json.loads(inspect.stdout)
            if len(device_map[command['role']]) != 4:
                raise ValueError('TP4 group does not see exactly four CUDA devices')
            with Path(command['log_path']).open('w') as log:
                child = subprocess.Popen(command['command'], stdout=log, stderr=subprocess.STDOUT,
                                         env=env, start_new_session=True)
            children.append(child)
        (out / 'supervisor-pids.json').write_text(json.dumps({c['role']: p.pid for c, p in zip(commands, children)}) + '\n')
        (out / 'device-map.json').write_text(json.dumps(device_map) + '\n')
        server = ThreadingHTTPServer(('0.0.0.0', 8300), EvidenceHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        while not stopping and all(p.poll() is None for p in children):
            time.sleep(2)
        if not stopping:
            raise RuntimeError('A TP4 engine exited; inspect retained role logs')
    finally:
        stop()
        deadline = time.monotonic() + 20
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        import traceback
        out=Path(os.environ.get('TP4_RESULTS_DIR', '/results'));out.mkdir(parents=True,exist_ok=True)
        (out/'engine-failure.json').write_text(json.dumps({'error':repr(error)})+'\n')
        traceback.print_exc()
        for path in out.glob('*.log'):
            print('\n'+path.name+'\n'+path.read_text(errors='replace')[-16000:],flush=True)
        signal.signal(signal.SIGTERM,signal.SIG_DFL)
        signal.signal(signal.SIGINT,signal.SIG_DFL)
        # Keep the evidence volume readable until the controller collects it.
        # The independent cloud/session deadline still bounds VM lifetime.
        while True:time.sleep(5)
