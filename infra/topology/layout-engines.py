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


def write_json(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value));temporary.replace(path)


def epoch_processes(out, proc=Path('/proc')):
    """Only processes carrying this unique epoch's inherited environment marker."""
    marker = ('TP4_RESULTS_DIR=' + str(out)).encode()
    found = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in (1, os.getpid()):
            continue
        try:
            if marker not in (entry/'environ').read_bytes().split(b'\0'):
                continue
            # starttime disambiguates a reused PID; ')' may occur in comm.
            stat = (entry/'stat').read_text().rsplit(')', 1)[1].split()
            if stat[0] != 'Z': found[int(entry.name)] = stat[19]
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return found


def drain_epoch(out, timeout=50, kill_after=10, interval=1, gpu_query=None):
    """Drain marked descendants, including workers outliving their API parent."""
    if gpu_query is None:
        def gpu_query():
            return subprocess.run(['nvidia-smi','--query-compute-apps=pid',
                '--format=csv,noheader'],capture_output=True,text=True,
                check=True,timeout=10).stdout.strip()
    started = time.monotonic(); events = []
    while time.monotonic() - started < timeout:
        live = epoch_processes(out)
        sig = signal.SIGTERM if time.monotonic()-started < kill_after else signal.SIGKILL
        for pid, start in live.items():
            # Never signal a different process if it replaced the observed PID.
            if epoch_processes(out).get(pid) != start: continue
            try:
                os.kill(pid, sig)
                events.append({'pid':pid,'starttime':start,'signal':int(sig)})
            except ProcessLookupError:
                pass
        remaining_gpu = gpu_query()
        if not epoch_processes(out) and not remaining_gpu:
            write_json(out/'shutdown.json',{'drained':True,'signals':events})
            return
        time.sleep(interval)
    write_json(out/'shutdown.json',{'drained':False,'signals':events,
        'remaining_epoch_processes':epoch_processes(out),'gpu_processes':remaining_gpu})
    raise RuntimeError('CUDA or epoch processes remain after bounded shutdown')


def stop_epoch(child,out,timeout=45):
    """A stuck supervisor must not prevent descendant and CUDA cleanup."""
    if child.poll() is None:child.terminate()
    try:child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill();child.wait(timeout=5)
    drain_epoch(out)


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
               'distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py',
               'v1/worker/gpu/model_runner.py','v1/worker/gpu/attn_utils.py',
               'v1/worker/utils.py','v1/core/kv_cache_utils.py')
    (root/'installed-software.json').write_text(json.dumps({'versions': versions,
        'source_sha256': {m: hashlib.sha256((vp/m).read_bytes()).hexdigest() for m in modules}}, indent=2))
    child = None; out = None; stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None: child.terminate()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        epochs=('default-a','packed-doc','packed','default-b') if base.get('vllm_version')=='0.29.0' else EPOCHS
        (root/'layout-matrix.json').write_text(json.dumps({'epochs':epochs}))
        for epoch in epochs:
            out = root/epoch; out.mkdir()
            config = (dict(base,kv_cache_layout={'packed':'BHLNC','packed-doc':'BLHNC'}.get(epoch,'LBHNC'))
                      if base.get('vllm_version')=='0.29.0'
                      else dict(base, enable_cross_layers_blocks=epoch == 'packed'))
            config_path = Path('/tmp')/f'{epoch}.json'
            config_path.write_text(json.dumps(config))
            (Path('/tmp')/'model-config.json').write_bytes(a.config.with_name('model-config.json').read_bytes())
            (out/'effective-config.json').write_text(json.dumps(config, indent=2))
            with (out/'supervisor.log').open('w') as log:
                child = subprocess.Popen(['python3','-u',str(HERE/'tp4-engines.py'),'--config',str(config_path)],
                    env={**os.environ,'TP4_RESULTS_DIR':str(out)},stdout=log,stderr=subprocess.STDOUT)
            write_json(root/'current-epoch.json',{'epoch':epoch,'pid':child.pid})
            while not stopping:
                if child.poll() is not None: raise RuntimeError('Engine supervisor exited unexpectedly')
                if (root/(epoch+'.advance')).exists(): break
                time.sleep(1)
            stop_epoch(child,out)
            child=None
            if stopping: return
            time.sleep(2)
        (root/'all-epochs-stopped.json').write_text('{}')
        while not stopping: time.sleep(2)
    finally:
        stop()
        if child is not None:
            stop_epoch(child,out)


if __name__ == '__main__':
    try: main()
    except BaseException as error:
        import traceback
        write_json(Path('/results/layout-failure.json'),{'error':repr(error)})
        traceback.print_exc()
        signal.signal(signal.SIGTERM,signal.SIG_DFL)
        while True: time.sleep(5)
