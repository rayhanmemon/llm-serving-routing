#!/usr/bin/env python3
"""Render and inspect the pinned TP4 configuration without creating resources."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
DEFAULT = HERE.parent.parent / 'workloads/tp4-locality/config.json'
NS = 'router-tp4'


def load_config(path=DEFAULT):
    config = json.loads(path.read_text())
    raw = path.with_name('model-config.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != config['model_config_sha256']:
        raise ValueError('Model architecture digest changed')
    architecture = json.loads(raw)
    validate(config, architecture)
    return config, architecture


def validate(config, architecture):
    if config['model'] != 'Qwen/Qwen3-32B' or not re.fullmatch(r'[a-f0-9]{40}', config['revision']):
        raise ValueError('Pinned Qwen3-32B revision required')
    if config['tensor_parallel_size'] != 4:
        raise ValueError('This deployment requires TP4')
    if config['dtype'] != 'bfloat16' or config['kv_cache_dtype'] != 'auto':
        raise ValueError('Memory calculations require BF16 weights and KV')
    if architecture['num_key_value_heads'] % 4 or architecture['num_attention_heads'] % 4:
        raise ValueError('Attention heads must shard evenly without KV replication')
    if architecture.get('use_sliding_window') or architecture.get('sliding_window'):
        raise ValueError('Memory formula requires full attention')
    if config['rope_scaling'] != {'rope_type': 'yarn', 'factor': 4.0, 'original_max_position_embeddings': 32768}:
        raise ValueError('All lengths require the same declared YaRN settings')
    if config['max_model_len'] != 131072:
        raise ValueError('Declared context limit is 131072')
    if config['max_num_seqs'] != 8 or not 0 < config['gpu_memory_utilization'] < 1:
        raise ValueError('Invalid sequence or memory limit')
    if config['enable_prefix_caching'] or config['enforce_eager']:
        raise ValueError('Screening uses unique uncached inputs and default graph execution')
    if config['block_size'] != 64 or config['max_num_batched_tokens'] < config['max_num_seqs']:
        raise ValueError('Invalid token budget or cache block size')
    lengths = config['input_tokens']
    if not lengths or lengths != sorted(set(lengths)):
        raise ValueError('Prompt lengths must be unique and increasing')
    output = config['screening_output_tokens']
    if type(output) is not int or output < 1:
        raise ValueError('Positive output token budget required')
    for length in lengths:
        if type(length) is not int or length < 1 or length + output > config['max_model_len']:
            raise ValueError('Prompt plus output exceeds context capacity')
    if config['concurrency_candidates'] != [1, 2, 4, 8]:
        raise ValueError('Unreviewed concurrency sweep')
    expected = {'local': {'prefill': [0, 1, 2, 3], 'decode': [4, 5, 6, 7]},
                'remote': {'decode': [0, 1, 2, 3]}}
    if config['placement'] != expected:
        raise ValueError('Require disjoint TP4 groups on the local host and matched remote TP4')


def cache_bytes_per_rank(architecture, tokens):
    return 2 * architecture['num_hidden_layers'] * (architecture['num_key_value_heads'] // 4) * architecture['head_dim'] * 2 * tokens


def sizing(config, architecture):
    rows = []
    weights = config['parameter_count'] * 2 / 4
    for length in config['input_tokens']:
        # Account for generated tokens and rounding to an allocated cache block.
        total = length + config['screening_output_tokens']
        allocated = ((total + config['block_size'] - 1) // config['block_size']) * config['block_size']
        for count in config['concurrency_candidates']:
            kv = cache_bytes_per_rank(architecture, allocated) * count
            rows.append({'input_tokens': length, 'total_concurrent_requests': count,
                         'allocated_tokens_per_request': allocated,
                         'weights_gib_per_rank_approx': weights / 2**30,
                         'kv_gib_per_rank': kv / 2**30,
                         'weights_plus_kv_gib_per_rank_approx': (weights + kv) / 2**30,
                         'runtime_overhead_included': False})
    return rows


def engine_specs(config, host_role, pod_ip):
    if host_role not in ('local', 'remote') or not pod_ip:
        raise ValueError('Host role and Pod IP required')
    specs = []
    for role, devices in config['placement'][host_role].items():
        port, side = (8100, 5600) if role == 'prefill' else (8200, 5700)
        kv = {'kv_connector': 'NixlConnector', 'kv_role': config['kv_role'],
              'kv_buffer_device': 'cuda', 'kv_load_failure_policy': 'fail',
              'kv_connector_extra_config': {'backends': ['UCX']}}
        args = ['vllm', 'serve', config['model'], '--revision', config['revision'],
                '--tokenizer-revision', config['revision'], '--port', str(port),
                '--tensor-parallel-size', '4', '--distributed-executor-backend', 'mp',
                '--dtype', config['dtype'],
                '--kv-cache-dtype', config['kv_cache_dtype'],
                '--block-size', str(config['block_size']),
                '--max-model-len', str(config['max_model_len']),
                '--max-num-seqs', str(config['max_num_seqs']),
                '--max-num-batched-tokens', str(config['max_num_batched_tokens']),
                '--gpu-memory-utilization', str(config['gpu_memory_utilization']),
                '--enable-chunked-prefill', '--no-enable-prefix-caching',
                '--rope-scaling', json.dumps(config['rope_scaling'], sort_keys=True),
                '--kv-transfer-config', json.dumps(kv, sort_keys=True)]
        specs.append({'role': role, 'devices': devices, 'http_port': port, 'command': args,
                      'env': {'CUDA_VISIBLE_DEVICES': ','.join(map(str, devices)),
                              'HF_HOME': '/cache', 'VLLM_NIXL_SIDE_CHANNEL_HOST': pod_ip,
                              'VLLM_NIXL_SIDE_CHANNEL_PORT': str(side),
                              'UCX_PROTO_INFO': 'y', 'UCX_LOG_LEVEL': 'info',
                              'UCX_LOG_FILE': f'/results/ucx-{role}.%p.log'},
                      'log_path': f'/results/{role}.log'})
    return specs


def render(config_path, nodes, work_seconds=7200):
    """Offline manifest; the explicit role list permits local-only qualification."""
    config, architecture = load_config(config_path)
    if not nodes or not set(nodes) <= {'local', 'remote'} or 'local' not in nodes:
        raise ValueError('Local host required; remote is optional')
    if len(set(nodes.values())) != len(nodes) or not all(nodes.values()):
        raise ValueError('Local and remote must be distinct named hosts')
    if not 60 <= work_seconds <= 8100:
        raise ValueError('Manifest lifetime must fit the bounded session')
    spec = importlib.util.spec_from_file_location('single_manifest', HERE / 'run-single-host.py')
    single = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(single)
    data = {name: (HERE / name).read_text() for name in ('tp4-config.py', 'tp4-engines.py')}
    data.update({'config.json': json.dumps(config),
                 'model-config.json': config_path.with_name('model-config.json').read_text()})
    items = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS}},
             {'apiVersion': 'v1', 'kind': 'ConfigMap',
              'metadata': {'name': 'tp4-code', 'namespace': NS}, 'data': data}]
    for host_role, node in nodes.items():
        pod = copy.deepcopy(single.manifest(node)['items'][-1])
        pod['metadata'] = {'name': host_role, 'namespace': NS}
        pod_spec = pod['spec']
        pod_spec['activeDeadlineSeconds'] = work_seconds
        engine = pod_spec['containers'][0]
        engine['command'] = ['python3', '-u', '/probe/tp4-engines.py', '--config', '/probe/config.json']
        engine['env'] = [{'name': 'ENGINE_ROLE', 'value': host_role},
                         {'name': 'POD_IP', 'valueFrom': {'fieldRef': {'fieldPath': 'status.podIP'}}}]
        gpu_count = 8 if host_role == 'local' else 4
        engine['resources'] = {
            'requests': {'nvidia.com/gpu': str(gpu_count), 'cpu': str(gpu_count * 4), 'memory': f'{gpu_count * 16}Gi'},
            'limits': {'nvidia.com/gpu': str(gpu_count), 'cpu': str(gpu_count * 8), 'memory': f'{gpu_count * 32}Gi'}}
        for volume in pod_spec['volumes']:
            if volume['name'] == 'code':
                volume['configMap']['name'] = 'tp4-code'
            elif volume['name'] == 'shm':
                volume['emptyDir']['sizeLimit'] = '32Gi'
            elif volume['name'] == 'cache':
                volume['emptyDir']['sizeLimit'] = '100Gi'
        items.append(pod)
    return {'apiVersion': 'v1', 'kind': 'List', 'items': items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT)
    parser.add_argument('--local-node')
    parser.add_argument('--remote-node')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    config, architecture = load_config(args.config)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'memory.json').write_text(json.dumps(sizing(config, architecture), indent=2) + '\n')
    commands = {role: engine_specs(config, role, 'POD_IP_AT_DEPLOYMENT') for role in ('local', 'remote')}
    (args.out / 'engine-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    if args.remote_node and not args.local_node:
        parser.error('--remote-node requires --local-node')
    if args.local_node:
        nodes = {'local': args.local_node}
        if args.remote_node:
            nodes['remote'] = args.remote_node
        (args.out / 'engines.json').write_text(json.dumps(render(args.config, nodes), indent=2) + '\n')
    print(json.dumps({'rendered': str(args.out), 'cloud_actions': False, 'gpu_execution_validated': False}))


if __name__ == '__main__':
    main()
