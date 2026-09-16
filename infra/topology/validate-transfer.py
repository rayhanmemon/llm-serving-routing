#!/usr/bin/env python3
"""Validate collected forced-route diagnostics; do not generate or send requests."""
import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
from evidence import require_collection


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_stream(raw, input_tokens, output_tokens):
    require(isinstance(raw, str) and raw.strip(), 'missing raw streamed response')
    done = False
    finishes = []
    usage = None
    text = []
    content_chunks = []
    for block in raw.replace('\r\n', '\n').split('\n\n'):
        lines = [line for line in block.splitlines() if line and not line.startswith(':')]
        if not lines:
            continue
        require(not done, 'data after terminal [DONE]')
        require(all(line.startswith('data:') for line in lines), 'unexpected SSE field')
        data = '\n'.join(line[5:].lstrip() for line in lines)
        if data == '[DONE]':
            done = True
            continue
        value = json.loads(data)
        require(isinstance(value, dict) and not value.get('error'), 'error or invalid SSE payload')
        require(isinstance(value.get('choices'), list), 'missing choices')
        has_content = False
        for choice in value['choices']:
            require(choice.get('index', 0) == 0, 'unexpected completion index')
            if choice.get('text'):
                require(not finishes, 'generated text after finish reason')
                text.append(choice['text'])
                has_content = True
            if choice.get('finish_reason'):
                finishes.append(choice['finish_reason'])
        if value.get('usage') is not None:
            usage = value['usage']
        if has_content:
            content_chunks.append(value)
    require(done and finishes == ['length'], 'missing completion terminator or incorrect finish reason')
    require(bool(''.join(text)), 'empty generated output')
    require(isinstance(usage, dict), 'missing server usage')
    require(usage.get('prompt_tokens') == input_tokens, 'server prompt token count mismatch')
    require(usage.get('completion_tokens') == output_tokens, 'server output token count mismatch')
    return content_chunks


def pod_identity(snapshot):
    result = {}
    for pod in json.loads(snapshot.read_text())['items']:
        containers = pod['spec'].get('containers', []) + pod['spec'].get('initContainers', [])
        if not any(c['name'] == 'modelserver' or 'llm-d-router-endpoint-picker' in c['image'] for c in containers):
            continue
        statuses = pod['status'].get('containerStatuses', []) + pod['status'].get('initContainerStatuses', [])
        require(statuses and all(s.get('containerID') and s.get('ready') and s.get('state', {}).get('running') for s in statuses), 'worker container not running')
        result[pod['metadata']['name']] = {
            'uid': pod['metadata']['uid'], 'node': pod['spec']['nodeName'],
            'containers': sorted((s['name'], s['containerID'], s.get('imageID'), s['restartCount']) for s in statuses),
        }
    require(len(result) == 4, 'expected three model workers and one EPP')
    return result


def validate_records(records, input_tokens, output_tokens, count):
    require(isinstance(records, list) and len(records) == count, 'per-request record count mismatch')
    hashes, ttfts = [], []
    for row in records:
        require(not row.get('error'), 'harness recorded a request error')
        request = json.loads(row['request']) if isinstance(row['request'], str) else row['request']
        require(request.get('stream') is True and request.get('ignore_eos') is True, 'request settings mismatch')
        require(request.get('max_tokens') == output_tokens, 'output limit mismatch')
        require(isinstance(request.get('prompt'), str), 'prompt missing')
        content_chunks = validate_stream(row['response'], input_tokens, output_tokens)
        metrics = row['info']['response_metrics']
        recorded_chunks = metrics.get('response_chunks')
        chunk_times = metrics.get('chunk_times')
        require(isinstance(recorded_chunks, list) and isinstance(chunk_times, list) and
                len(recorded_chunks) == len(chunk_times) == len(content_chunks) and content_chunks,
                'content chunk/timestamp count mismatch')
        try:
            parsed_chunks = [json.loads(chunk) for chunk in recorded_chunks]
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError('invalid recorded response chunk') from error
        require(parsed_chunks == content_chunks, 'recorded response chunks differ from raw SSE content')
        require(all(isinstance(value, (int, float)) and math.isfinite(value) for value in chunk_times) and
                all(x <= y for x, y in zip(chunk_times, chunk_times[1:])),
                'invalid content timestamps')
        require(row['start_time'] <= chunk_times[0] <= chunk_times[-1] <= row['end_time'],
                'invalid client timing interval')
        estimated = metrics.get('output_token_times')
        require(isinstance(estimated, list) and estimated and
                all(isinstance(value, (int, float)) and math.isfinite(value) for value in estimated) and
                all(x <= y for x, y in zip(estimated, estimated[1:])) and
                row['start_time'] <= estimated[0] <= estimated[-1] <= row['end_time'],
                'invalid estimated output-token timestamps')
        ttfts.append(chunk_times[0] - row['start_time'])
        hashes.append(hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    return hashes, ttfts


def validate_routes(paths, run_id, decoder, namespace, count):
    expected = base64.b64encode(f'{namespace}/{decoder}-rank-0'.encode()).decode()
    rows = []
    for path in paths:
        for line in path.read_text().splitlines():
            at = line.find('{')
            if at < 0:
                continue
            try:
                row = json.loads(line[at:])
            except json.JSONDecodeError:
                continue
            if row.get('run_id') == run_id:
                rows.append(row)
    require(len(rows) == count, 'route-log count mismatch')
    require(len({r.get('request_id') for r in rows}) == count, 'duplicate route request IDs')
    for row in rows:
        require(row.get('request_id') not in (None, '', '-'), 'missing route request ID')
        require(str(row.get('status')) == '200', 'unsuccessful proxy response')
        require(row.get('response_flags') == '-', 'proxy response flags indicate failure')
        require(row.get('requested_decoder') == expected and row.get('selected_decoder') == expected, 'decoder pin mismatch')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--decoder-pod', required=True)
    p.add_argument('--namespace', default='topology-measurement')
    p.add_argument('--input-tokens', type=int, required=True)
    p.add_argument('--output-tokens', type=int, default=128)
    p.add_argument('--count', type=int, default=12)
    p.add_argument('--paired-with', type=Path, help='validation.json from the matched opposite-route arm')
    a = p.parse_args()
    require_collection(a.before)
    require_collection(a.after, measured=True)
    require(pod_identity(a.before / 'pods.json') == pod_identity(a.after / 'pods.json'), 'worker/EPP identity or restart changed')
    gpu_sets = []
    for folder in (a.before, a.after):
        files = sorted(folder.glob('*-gpu.json'))
        require(len(files) == 3, 'expected GPU identity records for three model workers')
        identities = {f.name: json.loads(f.read_text())['gpu_uuids'] for f in files}
        require(all(len(v) == 1 for v in identities.values()), 'worker does not see exactly one GPU')
        require(len({v[0] for v in identities.values()}) == 3, 'model workers share a physical GPU')
        gpu_sets.append(identities)
    require(gpu_sets[0] == gpu_sets[1], 'GPU identity changed during arm')
    paths = list((a.after / 'reports').rglob('per_request_lifecycle_metrics.json'))
    require(len(paths) == 1, 'expected one per-request report')
    hashes, ttfts = validate_records(json.loads(paths[0].read_text()), a.input_tokens, a.output_tokens, a.count)
    validate_routes(a.after.glob('*envoy-proxy.log'), a.run_id, a.decoder_pod, a.namespace, a.count)
    if a.paired_with:
        require(hashes == json.loads(a.paired_with.read_text())['request_hashes'], 'paired request payloads differ')
    result = {'run_id': a.run_id, 'request_hashes': hashes, 'client_ttft_seconds': ttfts,
              'request_route_and_identity_checks_passed': True,
              'transfer_qualification': 'Requires separate NIXL metric deltas, UCX payload-path evidence and GPU identity review.'}
    (a.after / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS: requests, decoder pins and unchanged worker identities; review actual transfer evidence separately.')


if __name__ == '__main__':
    main()
