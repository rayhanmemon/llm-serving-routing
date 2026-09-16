#!/usr/bin/env python3
"""Create a benchmark Pod and configuration locally; never submit a request."""
import argparse
import base64
from pathlib import Path
import yaml
from render import MODEL, REVISION

IMAGE = 'quay.io/inference-perf/inference-perf:v0.6.1@sha256:e29328cc223ebae58d9022d60ad651cc3c4cbd534885a78b28f54086aa4b9c9e'
TOKENIZER_CACHE = f'/var/cache/router-topology/tokenizer-{REVISION[:12]}'


def _bundle_trace_files(cfg, config_path, configmap_data):
    """Add local trace files to the config map and rewrite their in-pod paths."""
    bundled = {}
    root = config_path.parent.resolve()
    for section_name in ('data', 'load'):
        trace = cfg.get(section_name, {}).get('trace')
        if not trace:
            continue
        source = Path(trace['file'])
        if source.is_absolute():
            raise ValueError('trace paths must be relative to the benchmark config')
        source = (root / source).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError('trace paths must remain inside the rendered directory') from exc
        if not source.is_file():
            raise ValueError(f'trace file does not exist: {source}')
        key = source.name
        content = source.read_text()
        if key in bundled and bundled[key] != content:
            raise ValueError(f'trace file name collision: {key}')
        bundled[key] = content
        trace['file'] = f'/cfg/{key}'
    configmap_data.update(bundled)


def render_workload(config_path, name, cpu_node, namespace='topology-measurement', decoder_pod=None):
    cfg = yaml.safe_load(config_path.read_text())
    cfg['tokenizer']['pretrained_model_name_or_path'] = '/tokenizer'
    cfg['storage']['local_storage']['path'] = '/reports'
    cfg['api'].setdefault('headers', {})['x-benchmark-run'] = name
    if decoder_pod:
        # The fixture has one target port; datastore endpoint IDs append its index.
        token = base64.b64encode(f'{namespace}/{decoder_pod}-rank-0'.encode()).decode()
        cfg['api']['headers']['x-benchmark-decoder'] = token

    configmap_data = {}
    _bundle_trace_files(cfg, config_path, configmap_data)
    configmap_data['config.yaml'] = yaml.safe_dump(cfg, sort_keys=False)
    configmap = {'apiVersion': 'v1', 'kind': 'ConfigMap',
                 'metadata': {'name': name, 'namespace': namespace}, 'data': configmap_data}

    marker = f'/tokenizer/.ready-{REVISION}'
    download = '\n'.join([
        'from pathlib import Path',
        'import fcntl',
        'from huggingface_hub import snapshot_download',
        "lock = Path('/tokenizer/.download.lock')",
        'lock.touch(exist_ok=True)',
        "with lock.open('r+') as handle:",
        '    fcntl.flock(handle, fcntl.LOCK_EX)',
        f'    marker = Path({marker!r})',
        '    if not marker.exists():',
        f'        snapshot_download({MODEL!r}, revision={REVISION!r}, local_dir="/tokenizer",',
        '                          allow_patterns=["config.json", "tokenizer*", "vocab.json",',
        '                                          "merges.txt", "special_tokens_map.json"])',
        f'        marker.write_text({REVISION!r})',
    ])
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': namespace,
           'labels': {'app.kubernetes.io/name': 'topology-benchmark'}}, 'spec': {
        'nodeSelector': {'kubernetes.io/hostname': cpu_node}, 'restartPolicy': 'Never',
        'activeDeadlineSeconds': 1200,
        'initContainers': [{'name': 'tokenizer', 'image': IMAGE, 'command': ['python', '-c', download],
                            'volumeMounts': [{'name': 'tokenizer', 'mountPath': '/tokenizer'}]}],
        'containers': [{'name': 'benchmark', 'image': IMAGE, 'workingDir': '/workspace',
            'command': ['sh', '-c', 'python inference_perf/main.py --config_file /cfg/config.yaml; rc=$?; '
                        'echo "$rc" > /reports/exit-code; echo "BENCHMARK_EXIT=$rc"; sleep 600; exit "$rc"'],
            'env': [{'name': 'TOKENIZERS_PARALLELISM', 'value': 'false'}],
            'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'memory': '4Gi'}},
            'volumeMounts': [{'name': 'config', 'mountPath': '/cfg', 'readOnly': True},
                             {'name': 'tokenizer', 'mountPath': '/tokenizer', 'readOnly': True},
                             {'name': 'reports', 'mountPath': '/reports'}]}],
        'volumes': [{'name': 'config', 'configMap': {'name': name}},
                    {'name': 'tokenizer', 'hostPath': {'path': TOKENIZER_CACHE, 'type': 'DirectoryOrCreate'}},
                    {'name': 'reports', 'emptyDir': {}}]}}
    return configmap, pod


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('config', type=Path)
    p.add_argument('--name', required=True)
    p.add_argument('--cpu-node', required=True)
    p.add_argument('--namespace', default='topology-measurement')
    p.add_argument('--decoder-pod', help='Optional diagnostic pin; omit for policy-evaluated requests')
    a = p.parse_args()
    print(yaml.safe_dump_all(render_workload(a.config, a.name, a.cpu_node, a.namespace, a.decoder_pod),
                             sort_keys=False))


if __name__ == '__main__':
    main()
