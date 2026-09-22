#!/usr/bin/env python3
"""Restart both TP4 engines between fixed layout epochs without losing model cache."""
import argparse
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
EPOCHS = ('default-a', 'packed', 'default-b')


def main():
    p = argparse.ArgumentParser(); p.add_argument('--config', type=Path, required=True)
    a = p.parse_args(); base = json.loads(a.config.read_text())
    root = Path('/results'); root.mkdir(exist_ok=True)
    versions = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()
                if any(x in d.metadata['Name'].lower() for x in ('vllm', 'nixl', 'torch', 'cuda'))}
    import vllm
    vp = Path(vllm.__file__).parent
    modules = ('v1/worker/kv_connector_model_runner_mixin.py',
               'distributed/kv_transfer/kv_connector/v1/nixl/connector.py',
               'distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py')
    (root/'installed-software.json').write_text(json.dumps({'versions': versions,
        'source_sha256': {m: hashlib.sha256((vp/m).read_bytes()).hexdigest() for m in modules}}, indent=2))
    child = None; stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None: child.terminate()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        for epoch in EPOCHS:
            out = root/epoch; out.mkdir()
            config = dict(base, enable_cross_layers_blocks=epoch == 'packed')
            config_path = Path('/tmp')/f'{epoch}.json'
            config_path.write_text(json.dumps(config))
            (Path('/tmp')/'model-config.json').write_bytes(a.config.with_name('model-config.json').read_bytes())
            (out/'effective-config.json').write_text(json.dumps(config, indent=2))
            with (out/'supervisor.log').open('w') as log:
                child = subprocess.Popen(['python3','-u',str(HERE/'tp4-engines.py'),'--config',str(config_path)],
                    env={**os.environ,'TP4_RESULTS_DIR':str(out)},stdout=log,stderr=subprocess.STDOUT)
            (root/'current-epoch.json').write_text(json.dumps({'epoch':epoch,'pid':child.pid}))
            while not stopping:
                if child.poll() is not None: raise RuntimeError('Engine supervisor exited unexpectedly')
                if (root/(epoch+'.advance')).exists(): break
                time.sleep(1)
            if child.poll() is None: child.terminate()
            child.wait(timeout=45)
            if stopping: return
            # No next process until all CUDA workers and the evidence server exited.
            check = subprocess.run(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader'],
                                   capture_output=True,text=True,check=True)
            if check.stdout.strip(): raise RuntimeError('CUDA processes remain after epoch shutdown')
            time.sleep(2)
        (root/'all-epochs-stopped.json').write_text('{}')
        while not stopping: time.sleep(2)
    finally:
        stop()
        if child is not None:
            try: child.wait(timeout=45)
            except subprocess.TimeoutExpired: child.kill(); child.wait()


if __name__ == '__main__':
    try: main()
    except BaseException as error:
        import traceback
        Path('/results/layout-failure.json').write_text(json.dumps({'error':repr(error)}))
        traceback.print_exc()
        signal.signal(signal.SIGTERM,signal.SIG_DFL)
        while True: time.sleep(5)
