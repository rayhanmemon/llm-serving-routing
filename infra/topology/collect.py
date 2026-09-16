#!/usr/bin/env python3
"""Collect route logs, metrics and benchmark reports without deleting resources."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context', required=True)
    p.add_argument('--namespace', default='topology-measurement')
    p.add_argument('--out', type=Path, required=True, help='New collection directory')
    p.add_argument('--benchmark-pod', help='Collect completed reports as well as the observation snapshot')
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    k = ['kubectl', '--context', a.context, '--request-timeout=30s']
    errors = []
    started = time.time()

    def capture(args, filename):
        result = subprocess.run(k + args, capture_output=True, timeout=45)
        (a.out / filename).write_bytes(result.stdout)
        if result.returncode:
            (a.out / (filename + '.stderr')).write_bytes(result.stderr)
            errors.append(filename)
        return result

    pods_result = capture(['-n', a.namespace, 'get', 'pods', '-o', 'json'], 'pods.json')
    if pods_result.returncode:
        raise SystemExit('Pod inventory failed; collection is incomplete')
    pods = json.loads(pods_result.stdout)['items']
    capture(['get', 'nodes', '-o', 'json'], 'nodes.json')
    capture(['-n', a.namespace, 'get', 'events', '-o', 'json'], 'events.json')
    capture(['-n', a.namespace, 'get', 'configmaps', '-o', 'json'], 'configmaps.json')
    for pod in pods:
        name = pod['metadata']['name']
        containers = pod['spec'].get('containers', []) + pod['spec'].get('initContainers', [])
        for container in containers:
            if container['name'] in ('modelserver', 'routing-proxy', 'benchmark') or 'llm-d-router-endpoint-picker' in container['image'] or 'envoyproxy' in container['image']:
                capture(['-n', a.namespace, 'logs', name, '-c', container['name'], '--timestamps'], f'{name}-{container["name"]}.log')
        engine = next((c for c in containers if c['name'] == 'modelserver'), None)
        epp = next((c for c in containers if 'llm-d-router-endpoint-picker' in c['image']), None)
        port = next((x['containerPort'] for x in engine['ports'] if x['name'] == 'modelserver'), None) if engine else (9090 if epp else None)
        if engine:
            gpu_code = Path(__file__).with_name('gpu-inspect.py').read_text()
            capture(['-n', a.namespace, 'exec', name, '-c', 'modelserver', '--', 'python3', '-c', gpu_code], name + '-gpu.json')
        if port:
            path = f'/api/v1/namespaces/{a.namespace}/pods/http:{name}:{port}/proxy/metrics'
            capture(['get', '--raw', path], name + '-metrics.txt')
    if a.benchmark_pod:
        pod = next((x for x in pods if x['metadata']['name'] == a.benchmark_pod), None)
        if not pod or pod['metadata'].get('labels', {}).get('app.kubernetes.io/name') != 'topology-benchmark':
            raise SystemExit('Expected benchmark pod not found')
        base = ['-n', a.namespace, 'exec', a.benchmark_pod, '-c', 'benchmark', '--']
        reports = capture(base + ['tar', 'cf', '-', '-C', '/reports', '.'], 'reports.tar')
        if reports.returncode == 0:
            destination = a.out / 'reports'
            destination.mkdir()
            with tarfile.open(a.out / 'reports.tar') as archive:
                archive.extractall(destination, filter='data')
            exit_file = destination / 'exit-code'
            if not exit_file.exists() or exit_file.read_text().strip() != '0':
                errors.append('benchmark did not finish successfully')
            if not list(destination.rglob('*per_request*')):
                errors.append('per-request report missing')
    manifest = {'started_unix': started, 'finished_unix': time.time(), 'context': a.context,
                'namespace': a.namespace, 'collection_complete': not errors, 'errors': errors, 'sha256': {}}
    for file in sorted(a.out.rglob('*')):
        if file.is_file():
            with file.open('rb') as stream:
                manifest['sha256'][str(file.relative_to(a.out))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    (a.out / 'collection.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if errors:
        raise SystemExit('INCOMPLETE collection: ' + ', '.join(errors))
    print('PASS: collection saved to ' + str(a.out))
    print('Collection success does not establish request success, correct pins or improved performance.')


if __name__ == '__main__':
    main()
