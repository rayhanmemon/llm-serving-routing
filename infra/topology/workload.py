#!/usr/bin/env python3
"""Create a benchmark Pod and configuration locally; never submit a request."""
import argparse
import base64
from pathlib import Path
import yaml
from render import MODEL, REVISION

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('config', type=Path)
p.add_argument('--name', required=True)
p.add_argument('--cpu-node', required=True)
p.add_argument('--namespace', default='topology-measurement')
p.add_argument('--decoder-pod', help='Optional diagnostic pin; omit for policy-evaluated requests')
a = p.parse_args()
cfg = yaml.safe_load(a.config.read_text())
cfg['tokenizer']['pretrained_model_name_or_path'] = '/tokenizer'
cfg['storage']['local_storage']['path'] = '/reports'
cfg['api'].setdefault('headers', {})['x-benchmark-run'] = a.name
if a.decoder_pod:
    token = base64.b64encode(f'{a.namespace}/{a.decoder_pod}'.encode()).decode()
    cfg['api']['headers']['x-benchmark-decoder'] = token
image = 'quay.io/inference-perf/inference-perf:v0.6.1@sha256:e29328cc223ebae58d9022d60ad651cc3c4cbd534885a78b28f54086aa4b9c9e'
configmap = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': a.name, 'namespace': a.namespace},
             'data': {'config.yaml': yaml.safe_dump(cfg, sort_keys=False)}}
download = ('from huggingface_hub import snapshot_download; '
            f'snapshot_download({MODEL!r}, revision={REVISION!r}, local_dir="/tokenizer", '
            'allow_patterns=["config.json", "tokenizer*", "vocab.json", "merges.txt", "special_tokens_map.json"])')
pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': a.name, 'namespace': a.namespace,
       'labels': {'app.kubernetes.io/name': 'topology-benchmark'}}, 'spec': {
    'nodeSelector': {'kubernetes.io/hostname': a.cpu_node}, 'restartPolicy': 'Never',
    'activeDeadlineSeconds': 1200,
    'initContainers': [{'name': 'tokenizer', 'image': image, 'command': ['python', '-c', download],
                        'volumeMounts': [{'name': 'tokenizer', 'mountPath': '/tokenizer'}]}],
    'containers': [{'name': 'benchmark', 'image': image, 'workingDir': '/workspace',
        'command': ['sh', '-c', 'python inference_perf/main.py --config_file /cfg/config.yaml; rc=$?; '
                    'echo "$rc" > /reports/exit-code; echo "BENCHMARK_EXIT=$rc"; sleep 600; exit "$rc"'],
        'env': [{'name': 'TOKENIZERS_PARALLELISM', 'value': 'false'}],
        'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'memory': '4Gi'}},
        'volumeMounts': [{'name': 'config', 'mountPath': '/cfg', 'readOnly': True},
                         {'name': 'tokenizer', 'mountPath': '/tokenizer', 'readOnly': True},
                         {'name': 'reports', 'mountPath': '/reports'}]}],
    'volumes': [{'name': 'config', 'configMap': {'name': a.name}},
                {'name': 'tokenizer', 'emptyDir': {}}, {'name': 'reports', 'emptyDir': {}}]}}
print(yaml.safe_dump_all([configmap, pod], sort_keys=False))
