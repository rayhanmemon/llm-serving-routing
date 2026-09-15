#!/usr/bin/env python3
"""Import the local EPP archive onto an explicitly selected experiment CPU node."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import uuid

IMAGE = 'ghcr.io/llm-d/llm-d-router-endpoint-picker:topology-0217d299-amd64'
HELPER = 'docker.io/library/alpine:3.22@sha256:7c8cb692ae09657cbc4a3f3cbd0e8d5a2690ba38386aaaf252dbb060bf5eb2e6'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context', required=True)
    p.add_argument('--node', required=True)
    p.add_argument('--namespace', default='topology-measurement')
    p.add_argument('--archive', required=True, type=Path)
    p.add_argument('--sha256', required=True)
    p.add_argument('--execute', action='store_true', help='Create the temporary helper and import the image')
    a = p.parse_args()
    with a.archive.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != a.sha256:
            p.error('Archive checksum mismatch')
    with tarfile.open(a.archive) as archive:
        manifest = json.load(archive.extractfile('manifest.json'))
        if len(manifest) != 1 or IMAGE not in manifest[0].get('RepoTags', []):
            p.error('Archive must contain the expected EPP image only')
        config = json.load(archive.extractfile(manifest[0]['Config']))
        if (config.get('os'), config.get('architecture')) != ('linux', 'amd64'):
            p.error('Archive is not linux/amd64')
    name = 'epp-image-import-' + uuid.uuid4().hex[:10]
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': a.namespace,
           'labels': {'app.kubernetes.io/name': 'topology-image-import'}}, 'spec': {
        'nodeName': a.node, 'restartPolicy': 'Never', 'activeDeadlineSeconds': 600,
        'automountServiceAccountToken': False,
        'containers': [{'name': 'import', 'image': HELPER, 'command': ['sleep', '600'],
            'securityContext': {'privileged': True},
            'volumeMounts': [{'name': 'host', 'mountPath': '/host'}]}],
        'volumes': [{'name': 'host', 'hostPath': {'path': '/', 'type': 'Directory'}}]}}
    if not a.execute:
        print(json.dumps(pod, indent=2))
        return
    k = ['kubectl', '--context', a.context, '--request-timeout=30s']
    node = json.loads(subprocess.check_output(k + ['get', 'node', a.node, '-o', 'json']))
    if int(node.get('status', {}).get('allocatable', {}).get('nvidia.com/gpu', '0')):
        p.error('Choose the CPU utility node, not a GPU worker')
    created = json.loads(subprocess.check_output(k + ['create', '-f', '-', '-o', 'json'], input=json.dumps(pod).encode()))
    try:
        subprocess.run(k + ['-n', a.namespace, 'wait', '--for=condition=Ready', 'pod/' + name, '--timeout=120s'], check=True)
        exec_cmd = k + ['-n', a.namespace, 'exec', '-i', name, '--']
        ctr_path = subprocess.check_output(exec_cmd + ['chroot', '/host', '/bin/sh', '-c', 'command -v ctr'], text=True).strip()
        if not ctr_path.startswith('/') or '\n' in ctr_path:
            raise RuntimeError('No unambiguous installed containerd client found')
        subprocess.run(exec_cmd + ['test', '-S', '/host/run/containerd/containerd.sock'], check=True)
        print('Using installed containerd client: ' + ctr_path, flush=True)
        ctr = ['chroot', '/host', ctr_path, '--address', '/run/containerd/containerd.sock', '--namespace', 'k8s.io']
        with a.archive.open('rb') as source:
            subprocess.run(exec_cmd + ctr + ['images', 'import', '--platform', 'linux/amd64', '-'], stdin=source, check=True, timeout=180)
        images = subprocess.check_output(exec_cmd + ctr + ['images', 'list', '--quiet'], text=True)
        if IMAGE not in images.splitlines():
            raise RuntimeError('Expected EPP image not found after import')
        print('PASS: reviewed AMD64 image imported on ' + a.node)
    finally:
        current = json.loads(subprocess.check_output(k + ['-n', a.namespace, 'get', 'pod', name, '-o', 'json']))
        if current['metadata']['uid'] != created['metadata']['uid']:
            raise RuntimeError('Helper UID changed; refusing to delete a different pod')
        subprocess.run(k + ['-n', a.namespace, 'delete', 'pod', name, '--wait=true', '--timeout=60s'], check=True)


if __name__ == '__main__':
    main()
