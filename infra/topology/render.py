#!/usr/bin/env python3
"""Render the three-worker diagnostic deployment without contacting Kubernetes."""
import argparse
import json
from pathlib import Path
import yaml

MODEL = 'Qwen/Qwen3-8B'
REVISION = 'b968826d9c46dd6066d109eabc6255188de91218'
ENGINE = 'docker.io/vllm/vllm-openai:v0.26.0@sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b'
SIDECAR = 'ghcr.io/llm-d/llm-d-router-disagg-sidecar:v0.10.0@sha256:1344adff65f96f76fc00c6003a1c63c1e9bb70f3628307f61f748a89a265b260'
EPP_TAG = 'topology-0217d299-amd64'
PROXY_IMAGE = 'docker.io/envoyproxy/envoy:distroless-v1.33.2@sha256:85500e28ed088ec39ff0adc1be3d358a8ad062926aaa62c36b28bde00919e4e8'


def model_deployment(name, role, node, namespace, ipc_mode="host"):
    labels = {'app.kubernetes.io/name': name, 'llm-d.ai/guide': 'topology-measurement',
              'llm-d.ai/role': role, 'kubernetes.io/hostname': node}
    port = 8000 if role == 'prefill' else 8200
    args = [MODEL, '--revision=' + REVISION, '--tokenizer-revision=' + REVISION,
            '--tensor-parallel-size=1', '--dtype=bfloat16', '--block-size=64',
            '--max-model-len=16384', '--max-num-seqs=32', '--max-num-batched-tokens=8192',
            '--gpu-memory-utilization=0.85', '--no-enable-prefix-caching',
            '--kv-transfer-config', json.dumps({'kv_connector': 'NixlConnector',
            'kv_role': 'kv_both', 'kv_load_failure_policy': 'fail'}), '--port=' + str(port)]
    engine = {'name': 'modelserver', 'image': ENGINE, 'command': ['vllm', 'serve'], 'args': args,
              'ports': [{'name': 'modelserver', 'containerPort': port},
                        {'name': 'nixl', 'containerPort': 5600}],
              'env': [{'name': 'USER', 'value': 'llm-d'}, {'name': 'HF_HOME', 'value': '/cache'},
                      {'name': 'UCX_PROTO_INFO', 'value': 'yes'},
                      {'name': 'UCX_CUDA_IPC_ENABLE_GET_ZCOPY', 'value': 'on'},
                      {'name': 'VLLM_NIXL_SIDE_CHANNEL_HOST', 'valueFrom': {'fieldRef': {'fieldPath': 'status.podIP'}}},
                      {'name': 'VLLM_NIXL_SIDE_CHANNEL_PORT', 'value': '5600'},
                      {'name': 'VLLM_HTTP_TIMEOUT_KEEP_ALIVE', 'value': '120'}],
              'resources': {'requests': {'cpu': '6', 'memory': '32Gi', 'nvidia.com/gpu': '1'},
                            'limits': {'cpu': '6', 'memory': '48Gi', 'nvidia.com/gpu': '1'}},
              'startupProbe': {'httpGet': {'path': '/v1/models', 'port': 'modelserver'},
                               'periodSeconds': 10, 'timeoutSeconds': 5, 'failureThreshold': 90},
              'readinessProbe': {'httpGet': {'path': '/health', 'port': 'modelserver'}, 'timeoutSeconds': 5},
              'volumeMounts': [{'name': 'shm', 'mountPath': '/dev/shm'}, {'name': 'cache', 'mountPath': '/cache'}]}
    spec = {'nodeSelector': {'kubernetes.io/hostname': node}, 'containers': [engine],
            'terminationGracePeriodSeconds': 30,
            'volumes': [{'name': 'shm', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '8Gi'}},
                        {'name': 'cache', 'emptyDir': {}}]}
    if ipc_mode == 'host':
        spec.update({'hostIPC': True, 'hostPID': True})
        spec['volumes'][0] = {'name': 'shm', 'hostPath': {'path': '/dev/shm', 'type': 'Directory'}}
    if role == 'decode':
        spec['initContainers'] = [{'name': 'routing-proxy', 'image': SIDECAR, 'restartPolicy': 'Always',
                                  'args': ['--port=8000', '--model-server-port=8200', '--kv-connector=nixlv2',
                                           '--zap-log-level=4', '--secure-proxy=false'],
                                  'ports': [{'name': 'sidecar', 'containerPort': 8000}],
                                  'resources': {'requests': {'cpu': '250m', 'memory': '256Mi'},
                                                'limits': {'memory': '1Gi'}}}]
    return {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name, 'namespace': namespace},
            'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'},
                     'selector': {'matchLabels': {'app.kubernetes.io/name': name}},
                     'template': {'metadata': {'labels': labels}, 'spec': spec}}}


def epp_config(policy, allowance, weight):
    plugins = [{'type': t} for t in ['topology-extractor', 'utilization-detector', 'prefill-filter',
                                    'decode-filter', 'inflight-load-producer', 'token-load-scorer',
                                    'active-request-scorer', 'max-score-picker']]
    plugins += [{'type': 'session-affinity-filter', 'name': 'diagnostic-pin',
                 'parameters': {'encodedEndpointHeaderConfig': {'header': 'x-benchmark-decoder'}}},
                {'type': 'disagg-profile-handler', 'parameters': {'stageOrder': 'prefill-first'}}]
    decode = [{'pluginRef': p} for p in ['decode-filter', 'utilization-detector', 'diagnostic-pin']]
    if policy in ('hard', 'allowance'):
        params = {'minAffinity': 'host'}
        if policy == 'allowance':
            params['loadAllowance'] = allowance
        plugins.append({'type': 'topology-affinity-filter', 'parameters': params})
        decode.append({'pluginRef': 'topology-affinity-filter'})
    elif policy == 'soft':
        plugins.append({'type': 'topology-affinity-scorer'})
        decode.append({'pluginRef': 'topology-affinity-scorer', 'weight': weight})
    decode += [{'pluginRef': 'active-request-scorer', 'weight': 1}, {'pluginRef': 'max-score-picker'}]
    return {'apiVersion': 'llm-d.ai/v1alpha1', 'kind': 'EndpointPickerConfig', 'plugins': plugins,
            'schedulingProfiles': [{'name': 'prefill', 'plugins': [
                {'pluginRef': 'prefill-filter'}, {'pluginRef': 'utilization-detector'},
                {'pluginRef': 'token-load-scorer', 'weight': 1}, {'pluginRef': 'max-score-picker'}]},
                {'name': 'decode', 'plugins': decode}]}


def benchmark(tokens, namespace):
    dist = lambda n: {'min': n, 'max': n, 'mean': n, 'std_dev': 0}
    return {'api': {'type': 'completion', 'streaming': True},
            'data': {'type': 'random', 'input_distribution': dist(tokens), 'output_distribution': dist(128)},
            'load': {'type': 'concurrent', 'num_workers': 1, 'base_seed': 15092026, 'request_timeout': 60,
                     'stages': [{'num_requests': 12, 'concurrency_level': 1}]},
            'server': {'type': 'vllm', 'model_name': MODEL, 'ignore_eos': True,
                       'base_url': f'http://topology-epp.{namespace}.svc.cluster.local'},
            'tokenizer': {'pretrained_model_name_or_path': MODEL},
            'storage': {'local_storage': {'path': '/workspace/reports'}},
            'report': {'request_lifecycle': {'summary': True, 'per_stage': True, 'per_request': True}}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--local-node', required=True)
    p.add_argument('--remote-node', required=True)
    p.add_argument('--cpu-node', required=True)
    p.add_argument('--namespace', default='topology-measurement')
    p.add_argument('--ipc-mode', choices=('host', 'isolated'), default='host',
                   help='Host mode shares IPC/PID namespaces and /dev/shm for transfer qualification; no privileged mode')
    p.add_argument('--allowance', type=int, default=2, help='Uncalibrated starting value')
    p.add_argument('--soft-weight', type=float, default=0.5, help='Uncalibrated starting value')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if len({a.local_node, a.remote_node, a.cpu_node}) != 3 or a.allowance < 0 or a.soft_weight < 0:
        p.error('Use three distinct node names and non-negative policy parameters')
    a.out.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        (a.out / name).write_text(yaml.safe_dump(value, sort_keys=False))
    docs = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': a.namespace}}]
    docs += [model_deployment(n, r, node, a.namespace, a.ipc_mode) for n, r, node in [
        ('prefill', 'prefill', a.local_node), ('decode-local', 'decode', a.local_node),
        ('decode-remote', 'decode', a.remote_node)]]
    (a.out / 'modelservers.yaml').write_text(yaml.safe_dump_all(docs, sort_keys=False))
    for policy in ('none', 'hard', 'soft', 'allowance'):
        cfg = epp_config(policy, a.allowance, a.soft_weight)
        write(f'epp-{policy}.yaml', cfg)
        values = {'router': {'inferencePool': {'create': False},
            'extraServicePorts': [{'name': 'http', 'port': 80, 'protocol': 'TCP', 'targetPort': 8081}],
            'modelServers': {'matchLabels': {'llm-d.ai/guide': 'topology-measurement'}, 'targetPorts': [{'number': 8000}]},
            'monitoring': {'prometheus': {'auth': {'enabled': False}}},
            'proxy': {'image': PROXY_IMAGE, 'failOpen': False, 'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'memory': '4Gi'}}},
            'epp': {'replicas': 1, 'image': {'tag': EPP_TAG, 'pullPolicy': 'Never'},
                'flags': {'allow-experimental-plugins': True, 'secure-serving': False, 'health-checking': True, 'v': 4},
                'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'memory': '4Gi'}},
                'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                    {'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [a.cpu_node]}]}]}}},
                'pluginsConfigFile': 'topology.yaml', 'pluginsConfig': cfg}}}
        write(f'router-{policy}.values.yaml', values)
    for n in (512, 8192):
        write(f'benchmark-{n}.yaml', benchmark(n, a.namespace))
    print(a.out)


if __name__ == '__main__':
    main()
