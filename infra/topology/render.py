#!/usr/bin/env python3
"""Render the three-worker deployment and measurement fixtures offline."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import yaml

MODEL = 'Qwen/Qwen3-8B'
REVISION = 'b968826d9c46dd6066d109eabc6255188de91218'
ENGINE = 'docker.io/vllm/vllm-openai:v0.26.0@sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b'
SIDECAR = 'ghcr.io/llm-d/llm-d-router-disagg-sidecar:v0.10.0@sha256:1344adff65f96f76fc00c6003a1c63c1e9bb70f3628307f61f748a89a265b260'
EPP_TAG = 'topology-0217d299-amd64'
PROXY_IMAGE = 'docker.io/envoyproxy/envoy:distroless-v1.33.2@sha256:85500e28ed088ec39ff0adc1be3d358a8ad062926aaa62c36b28bde00919e4e8'
EVALUATED_POLICIES = ('none', 'hard', 'soft', 'absolute-cap', 'allowance')
POLICIES = ('diagnostic',) + EVALUATED_POLICIES


def model_deployment(name, role, node, namespace, ipc_mode="host", attention_backend=None):
    labels = {'app.kubernetes.io/name': name, 'llm-d.ai/guide': 'topology-measurement',
              'llm-d.ai/role': role, 'kubernetes.io/hostname': node}
    port = 8000 if role == 'prefill' else 8200
    args = [MODEL, '--revision=' + REVISION, '--tokenizer-revision=' + REVISION,
            '--tensor-parallel-size=1', '--dtype=bfloat16', '--block-size=64',
            '--max-model-len=16384', '--max-num-seqs=32', '--max-num-batched-tokens=8192',
            '--gpu-memory-utilization=0.85', '--no-enable-prefix-caching',
            '--kv-transfer-config', json.dumps({'kv_connector': 'NixlConnector',
            'kv_role': 'kv_both', 'kv_load_failure_policy': 'fail'}), '--port=' + str(port)]
    if attention_backend:
        args.append('--attention-backend=' + attention_backend)
    engine = {'name': 'modelserver', 'image': ENGINE, 'command': ['vllm', 'serve'], 'args': args,
              'ports': [{'name': 'modelserver', 'containerPort': port},
                        {'name': 'nixl', 'containerPort': 5600}],
              'env': [{'name': 'USER', 'value': 'llm-d'}, {'name': 'HF_HOME', 'value': '/cache'},
                      {'name': 'UCX_PROTO_INFO', 'value': 'yes'},
                      {'name': 'UCX_TLS', 'value': 'tcp,cuda_copy,cuda_ipc,self'},
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


def epp_config(policy, allowance, weight, absolute_cap, absolute_cap_fallback_on_empty):
    if policy not in POLICIES:
        raise ValueError(f'unknown policy: {policy}')
    if allowance < 0 or not math.isfinite(weight) or weight < 0 or absolute_cap < 0:
        raise ValueError('allowance, soft weight, and absolute cap must be non-negative')
    plugins = [{'type': t} for t in ['topology-extractor', 'utilization-detector', 'prefill-filter',
                                    'decode-filter', 'inflight-load-producer', 'token-load-scorer',
                                    'active-request-scorer', 'max-score-picker']]
    plugins.append({'type': 'disagg-profile-handler', 'parameters': {'stageOrder': 'prefill-first'}})
    decode = [{'pluginRef': p} for p in ['decode-filter', 'utilization-detector']]
    if policy == 'diagnostic':
        plugins.append({'type': 'session-affinity-filter', 'name': 'diagnostic-pin',
                        'parameters': {'encodedEndpointHeaderConfig': {'header': 'x-benchmark-decoder'}}})
        decode.append({'pluginRef': 'diagnostic-pin'})
    if policy in ('hard', 'allowance'):
        params = {'minAffinity': 'host'}
        if policy == 'allowance':
            params['loadAllowance'] = allowance
        plugins.append({'type': 'topology-affinity-filter', 'parameters': params})
        decode.append({'pluginRef': 'topology-affinity-filter'})
    elif policy == 'soft':
        plugins.append({'type': 'topology-affinity-scorer'})
        decode.append({'pluginRef': 'topology-affinity-scorer', 'weight': weight})
    elif policy == 'absolute-cap':
        plugins += [
            {'type': 'utilization-filter', 'name': 'absolute-active-request-cap',
             'parameters': {'conditions': [{'metric': 'active-requests', 'maxValue': absolute_cap}],
                            'fallbackOnEmpty': absolute_cap_fallback_on_empty}},
            {'type': 'topology-affinity-filter', 'parameters': {'minAffinity': 'host'}},
        ]
        decode += [{'pluginRef': 'absolute-active-request-cap'}, {'pluginRef': 'topology-affinity-filter'}]
    decode += [{'pluginRef': 'active-request-scorer', 'weight': 1}, {'pluginRef': 'max-score-picker'}]
    return {'apiVersion': 'llm-d.ai/v1alpha1', 'kind': 'EndpointPickerConfig', 'plugins': plugins,
            'schedulingProfiles': [{'name': 'prefill', 'plugins': [
                {'pluginRef': 'prefill-filter'}, {'pluginRef': 'utilization-detector'},
                {'pluginRef': 'token-load-scorer', 'weight': 1}, {'pluginRef': 'max-score-picker'}]},
                {'name': 'decode', 'plugins': decode}]}


def benchmark(tokens, namespace, num_requests=12, concurrency=1, seed=15092026):
    dist = lambda n: {'min': n, 'max': n, 'mean': n, 'std_dev': 0}
    return {'api': {'type': 'completion', 'streaming': True},
            'data': {'type': 'random', 'input_distribution': dist(tokens), 'output_distribution': dist(128)},
            'load': {'type': 'concurrent', 'num_workers': 1, 'base_seed': seed, 'request_timeout': 60,
                     'stages': [{'num_requests': num_requests, 'concurrency_level': concurrency}]},
            'server': {'type': 'vllm', 'model_name': MODEL, 'ignore_eos': True,
                       'base_url': f'http://topology-epp.{namespace}.svc.cluster.local'},
            'tokenizer': {'pretrained_model_name_or_path': MODEL},
            'storage': {'local_storage': {'path': '/workspace/reports'}},
            'report': {'request_lifecycle': {'summary': True, 'per_stage': True, 'per_request': True}}}


def calibration(tokens, namespace):
    cfg = benchmark(tokens, namespace, seed=16092026)
    # One worker makes random prompt content repeat exactly across policies.
    # Concurrent stages still raise that worker's dynamic concurrency limit.
    cfg['load']['num_workers'] = 1
    cfg['load']['worker_max_concurrency'] = 16
    cfg['load']['request_timeout'] = 120
    cfg['load']['stages'] = [
        {'num_requests': max(12, concurrency * 4), 'concurrency_level': concurrency}
        for concurrency in (1, 2, 4, 8)
    ]
    return cfg


def trace_workload(trace_name, request_count, duration_seconds, namespace, seed):
    trace = {'file': trace_name, 'format': 'AzurePublicDataset'}
    return {'api': {'type': 'completion', 'streaming': True},
            'data': {'type': 'random', 'trace': trace.copy()},
            'load': {'type': 'trace_replay', 'num_workers': 1, 'worker_max_concurrency': 128,
                     'base_seed': seed, 'request_timeout': 120,
                     'trace': trace.copy(),
                     'stages': [{'rate': request_count / duration_seconds,
                                 'duration': duration_seconds}]},
            'server': {'type': 'vllm', 'model_name': MODEL, 'ignore_eos': True,
                       'base_url': f'http://topology-epp.{namespace}.svc.cluster.local'},
            'tokenizer': {'pretrained_model_name_or_path': MODEL},
            'storage': {'local_storage': {'path': '/workspace/reports'}},
            'report': {'request_lifecycle': {'summary': True, 'per_stage': True, 'per_request': True}}}


def trace_rows(segments):
    """Return a deterministic alternating 512/8192-token trace."""
    epoch = datetime(2026, 9, 16, tzinfo=timezone.utc)
    rows = [('TIMESTAMP', 'ContextTokens', 'GeneratedTokens')]
    offset = 0.0
    request_index = 0
    for duration, spacing in segments:
        if not math.isfinite(duration) or not math.isfinite(spacing) or duration <= 0 or spacing <= 0:
            raise ValueError('trace segment duration and spacing must be positive')
        end = offset + duration
        while offset < end:
            timestamp = (epoch + timedelta(seconds=offset)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            rows.append((timestamp, str(512 if request_index % 2 == 0 else 8192), '128'))
            request_index += 1
            offset += spacing
        offset = end
    return rows


def write_trace(path, rows):
    path.write_text('\n'.join(','.join(row) for row in rows) + '\n')


def router_values(cfg, cpu_node):
    return {'router': {'inferencePool': {'create': False},
        'extraServicePorts': [{'name': 'http', 'port': 80, 'protocol': 'TCP', 'targetPort': 8081}],
        'modelServers': {'matchLabels': {'llm-d.ai/guide': 'topology-measurement'}, 'targetPorts': [{'number': 8000}]},
        'monitoring': {'prometheus': {'auth': {'enabled': False}}},
        'proxy': {'image': PROXY_IMAGE, 'failOpen': False, 'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'memory': '4Gi'}}},
        'epp': {'replicas': 1, 'image': {'tag': EPP_TAG, 'pullPolicy': 'Never'},
            'flags': {'allow-experimental-plugins': True, 'secure-serving': False, 'health-checking': True, 'v': 5},
            'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'memory': '4Gi'}},
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [cpu_node]}]}]}}},
            'pluginsConfigFile': 'topology.yaml', 'pluginsConfig': cfg}}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--local-node', required=True)
    p.add_argument('--remote-node', required=True)
    p.add_argument('--cpu-node', required=True)
    p.add_argument('--namespace', default='topology-measurement')
    p.add_argument('--ipc-mode', choices=('host', 'isolated'), default='host',
                   help='Host mode shares IPC/PID namespaces and /dev/shm for transfer qualification; no privileged mode')
    p.add_argument('--attention-backend',
                   help='Optional vLLM attention backend applied identically to all three model workers')
    p.add_argument('--allowance', type=int, default=2, help='Uncalibrated starting value')
    p.add_argument('--soft-weight', type=float, default=0.5, help='Uncalibrated starting value')
    p.add_argument('--absolute-cap', type=int, default=2, help='Uncalibrated active-request cap')
    p.add_argument('--absolute-cap-fallback-on-empty', action=argparse.BooleanOptionalAction, default=True,
                   help='Whether the absolute-cap baseline fails open when every decoder exceeds its cap')
    p.add_argument('--heldout-low-spacing', type=float, default=4.0,
                   help='Candidate seconds between held-out low-load requests; freeze after calibration')
    p.add_argument('--heldout-burst-spacing', type=float, default=0.25,
                   help='Candidate seconds between held-out burst requests; freeze after calibration')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if (len({a.local_node, a.remote_node, a.cpu_node}) != 3 or a.allowance < 0
            or not math.isfinite(a.soft_weight) or a.soft_weight < 0 or a.absolute_cap < 0
            or not math.isfinite(a.heldout_low_spacing) or a.heldout_low_spacing <= 0
            or not math.isfinite(a.heldout_burst_spacing) or a.heldout_burst_spacing <= 0):
        p.error('Use three distinct nodes, non-negative policy parameters, and positive trace spacing')
    a.out.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        (a.out / name).write_text(yaml.safe_dump(value, sort_keys=False))
    docs = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': a.namespace}}]
    if (a.attention_backend is not None and
            (not a.attention_backend.strip() or any(c.isspace() for c in a.attention_backend))):
        p.error('attention backend must be one non-empty CLI value without whitespace')
    docs += [model_deployment(n, r, node, a.namespace, a.ipc_mode, a.attention_backend) for n, r, node in [
        ('prefill', 'prefill', a.local_node), ('decode-local', 'decode', a.local_node),
        ('decode-remote', 'decode', a.remote_node)]]
    (a.out / 'modelservers.yaml').write_text(yaml.safe_dump_all(docs, sort_keys=False))
    for policy in POLICIES:
        cfg = epp_config(policy, a.allowance, a.soft_weight, a.absolute_cap,
                         a.absolute_cap_fallback_on_empty)
        write(f'epp-{policy}.yaml', cfg)
        write(f'router-{policy}.values.yaml', router_values(cfg, a.cpu_node))
    for n in (512, 8192):
        write(f'benchmark-{n}.yaml', benchmark(n, a.namespace))
        write(f'warmup-{n}.yaml', benchmark(n, a.namespace, num_requests=1, seed=15092027))
        write(f'calibration-{n}.yaml', calibration(n, a.namespace))

    low_rows = trace_rows([(120, a.heldout_low_spacing)])
    burst_rows = trace_rows([(30, 2.0), (30, a.heldout_burst_spacing), (30, 0.5), (30, 2.0)])
    for name, rows, seed in [('heldout-low', low_rows, 17092026),
                             ('heldout-burst', burst_rows, 17092027)]:
        trace_name = f'{name}.trace.csv'
        write_trace(a.out / trace_name, rows)
        write(f'{name}.yaml', trace_workload(trace_name, len(rows) - 1, 120, a.namespace, seed))
    print(a.out)


if __name__ == '__main__':
    main()
