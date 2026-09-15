#!/usr/bin/env python3
"""Add structured route evidence to rendered standalone Envoy manifests."""
import sys
import yaml

resources = list(yaml.safe_load_all(sys.stdin))
changed = 0
for resource in resources:
    if not resource or resource.get('kind') != 'ConfigMap':
        continue
    data = resource.get('data', {})
    if 'envoy.yaml' not in data:
        continue
    config = yaml.safe_load(data['envoy.yaml'])
    for listener in config['static_resources']['listeners']:
        if listener['name'] != 'vllm':
            continue
        for chain in listener['filter_chains']:
            for item in chain['filters']:
                hcm = item['typed_config']
                for access in hcm.get('access_log', []):
                    access['typed_config']['log_format'] = {'json_format': {
                        'request_id': '%REQ(X-REQUEST-ID)%',
                        'run_id': '%REQ(X-BENCHMARK-RUN)%',
                        'requested_decoder': '%REQ(X-BENCHMARK-DECODER)%',
                        'selected_decoder': '%RESP(X-BENCHMARK-DECODER)%',
                        'upstream_host': '%UPSTREAM_HOST%', 'status': '%RESPONSE_CODE%',
                        'duration_ms': '%DURATION%', 'response_flags': '%RESPONSE_FLAGS%'}}
                    changed += 1
    data['envoy.yaml'] = yaml.safe_dump(config, sort_keys=False)
if changed != 1:
    raise SystemExit(f'Expected one request access log, found {changed}')
sys.stdout.write(yaml.safe_dump_all(resources, sort_keys=False))
