#!/usr/bin/env python3
"""Run pinned TP4 engines inside a prepared GPU Pod and retain diagnostic logs."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import time

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
    out = Path('/results')
    out.mkdir(exist_ok=True)
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
            env = {**os.environ, **command['env']}
            env.pop('UCX_TLS', None)
            env.pop('UCX_CUDA_IPC_GET_ZCOPY', None)
            with Path(command['log_path']).open('w') as log:
                child = subprocess.Popen(command['command'], stdout=log, stderr=subprocess.STDOUT,
                                         env=env, start_new_session=True)
            children.append(child)
        (out / 'supervisor-pids.json').write_text(json.dumps({c['role']: p.pid for c, p in zip(commands, children)}) + '\n')
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
    main()
