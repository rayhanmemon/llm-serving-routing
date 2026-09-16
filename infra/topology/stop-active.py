#!/usr/bin/env python3
"""Stop the active guarded router rental. No cloud calls without --execute."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
STATE = Path.home() / '.codex/run-state/router-h100-pilot'


def active_session(state):
    budget_path = state / 'budget.json'
    if not budget_path.exists():
        return None
    pending = []
    for attempt in json.loads(budget_path.read_text()).get('attempts', []):
        if attempt.get('status') == 'completed':
            continue
        run = Path(attempt['run_dir']).resolve()
        if run.parent != (state / 'runs').resolve():
            raise ValueError('Run directory is outside the router session store')
        session = json.loads((run / 'session.json').read_text())
        if session['session_id'] != attempt['session_id'] or run.name != session['session_id']:
            raise ValueError('Session identity mismatch')
        verified = run / 'cleanup-verified.json'
        if verified.exists() and json.loads(verified.read_text()).get('session_id') == session['session_id']:
            continue
        pending.append((run, session))
    if len(pending) > 1:
        raise ValueError('Multiple active sessions; inspect them instead of guessing')
    return pending[0] if pending else None


def processes_to_stop(output, run, session):
    result = []
    launchers, applies = [], []
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        pid, command = int(fields[0]), fields[1]
        if pid == os.getpid():
            continue
        words = shlex.split(command)
        # Leave teardown and its independent deadline guard alive.
        if ('guard' in words or 'cleanup' in words or
                any(Path(w).name.endswith('-guard.py') for w in words)):
            continue
        if ('--execute' in words and session.get('terraform_plan_path') in words and
                any(Path(w).name == 'pilot-session.py' for w in words)):
            launchers.append((pid, signal.SIGINT))
        elif ('apply' in words and session.get('terraform_plan_path') in words and
                any(Path(w).name == 'terraform' for w in words)):
            applies.append((pid, signal.SIGINT))
        elif str(run) in command and any(Path(w).name.startswith(('run-', 'resume-', 'qualify-'))
                                        and w.endswith('.py') for w in words):
            result.append((pid, signal.SIGTERM))
    # The launcher already forwards one interrupt to Terraform; do not send two.
    return result + (launchers if launchers else applies)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / 'manual-stop.json').write_text(json.dumps({
            'requested_unix': time.time(), 'reason': 'User emergency stop; explicit resume required',
        }) + '\n')
    active = active_session(STATE)
    if active is None:
        print('No active guarded router rental is recorded. No cloud changes made.')
        if args.execute:
            print('Future launches are paused by the manual-stop record.')
        return
    run, session = active
    print('Router session:', run.name, flush=True)
    if not args.execute:
        print('Preview only. Add --execute to stop work and destroy this rental.')
        return
    spec = importlib.util.spec_from_file_location('pilot', HERE / 'pilot-session.py')
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    tf_root, _, _ = pilot.session_infrastructure_settings(session)
    (run / 'manual-stop-requested.json').write_text(json.dumps({
        'session_id': session['session_id'], 'requested_unix': time.time(),
        'reason': 'User emergency stop',
    }) + '\n')
    processes = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True)
    for pid, sig in processes_to_stop(processes, run, session):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
    # Only the dedicated cluster's system disruption budgets are removed.
    # Without this, its final node can remain billed while drain is blocked.
    config = next((p for p in (run / 'serving/kubeconfig', run / 'kubeconfig') if p.is_file()), None)
    if config:
        try:
            outputs = json.loads(subprocess.check_output(
                ['terraform', f'-chdir={tf_root}', 'output', '-json'], text=True, timeout=20))
            cluster = outputs['cluster_id']['value']
            k = ['kubectl', '--kubeconfig', str(config), '--context', 'router-topology', '--request-timeout=15s']
            view = json.loads(subprocess.check_output(k + ['config', 'view', '--minify', '-o', 'json'],
                                                      text=True, timeout=20))
            suffix = cluster.removeprefix('mk8scluster-')
            if not any(item['name'] == cluster or item['name'].endswith('-' + suffix)
                       for item in view.get('clusters', [])):
                raise ValueError('Kubeconfig does not match the Terraform cluster')
            pdbs = json.loads(subprocess.check_output(k + ['-n', 'kube-system', 'get', 'pdb', '-o', 'json'],
                                                      text=True, timeout=20))
            (run / 'manual-stop-pdbs.json').write_text(json.dumps(pdbs, indent=2) + '\n')
            names = [item['metadata']['name'] for item in pdbs.get('items', [])]
            if names:
                subprocess.run(k + ['-n', 'kube-system', 'delete', 'pdb', *names, '--wait=false'],
                               check=True, timeout=25)
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            print('Cluster drain preparation could not finish:', error, flush=True)
            print('Proceeding with scoped Terraform cleanup; inspect its log if drain stalls.', flush=True)
    print('Deleting the rental. Keep this terminal and your Mac awake until PASS.', flush=True)
    log_path = run / 'cleanup.log'
    offset = log_path.stat().st_size if log_path.exists() else 0
    child = subprocess.Popen([sys.executable, str(HERE / 'pilot-session.py'), 'cleanup', str(run), '--execute'])
    while True:
        if log_path.exists():
            with log_path.open() as stream:
                stream.seek(offset)
                print(stream.read(), end='', flush=True)
                offset = stream.tell()
        if child.poll() is not None:
            break
        time.sleep(1)
    if child.returncode != 0 or not (run / 'cleanup-verified.json').exists():
        raise SystemExit('Cleanup is not verified. Inspect ' + str(log_path))
    print('PASS — experiment teardown independently verified.', flush=True)


if __name__ == '__main__':
    main()
