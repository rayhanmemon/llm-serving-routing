#!/usr/bin/env python3
"""Check observed TP rank logs and isolated per-request transfer counters."""
import math
import re

COUNT = 'vllm:nixl_bytes_transferred_count'
BYTES = 'vllm:nixl_bytes_transferred_sum'
TIME_COUNT = 'vllm:nixl_xfer_time_seconds_count'
TIME_SUM = 'vllm:nixl_xfer_time_seconds_sum'
FAILURES = ('vllm:nixl_num_failed_transfers_total', 'vllm:nixl_num_failed_notifications_total',
            'vllm:nixl_num_kv_expired_reqs_total')


def worker_pids(log):
    """A restarted/missing worker cannot be certified by an earlier rank's log."""
    clean = re.sub(r'\x1b\[[0-9;]*m', '', log)
    found = {}
    for rank, pid in re.findall(r'Worker_TP(\d+)\s+pid=(\d+)', clean):
        found.setdefault(int(rank), set()).add(int(pid))
    if set(found) != set(range(4)) or any(len(pids) != 1 for pids in found.values()):
        raise ValueError('Need exactly one observed process for each of four TP ranks')
    result = {rank: next(iter(pids)) for rank, pids in found.items()}
    if len(set(result.values())) != 4:
        raise ValueError('TP ranks must have distinct processes')
    return result


def read_protocol_rows(log):
    """Keep thread identity so interleaved UCX tables cannot borrow a lane."""
    contexts = {}
    rows = []
    for line in log.splitlines():
        prefix = re.search(r'\[([^\[\]]+):(\d+)\s*:\s*(\d+)\]', line)
        if not prefix:
            continue
        thread = prefix.groups()
        if 'remote memory ' in line or ('ucp_context_' in line and 'cfg#' in line and '|' in line):
            contexts[thread] = None
            match = re.search(r'remote memory read.*into cuda/GPU(\d+).*from cuda/', line)
            if match:
                contexts[thread] = {'device': int(match.group(1)), 'header': line,
                                    'pid': int(thread[1]), 'thread': int(thread[2])}
            continue
        context = contexts.get(thread)
        if context and context.get('seen_row') and '+---' in line:
            contexts[thread] = None
            continue
        if not context or '|' not in line:
            continue
        fields = [s.strip() for s in line.split('|')]
        if len(fields) < 4 or not re.fullmatch(r'\d+(?:\.\.(?:\d+|inf))?', fields[1]):
            continue
        context['seen_row'] = True
        if fields[1] == '0':
            continue
        rows.append({**context, 'range': fields[1], 'method': fields[2], 'lane': fields[3]})
    return rows


def validate_rank_transports(engine_log, process_logs, route, min_descriptor_bytes=32768):
    if route not in ('local', 'remote'):
        raise ValueError('Unknown KV route')
    if min_descriptor_bytes <= 0:
        raise ValueError('Positive minimum KV descriptor size required')
    ranks = worker_pids(engine_log)
    proof = {}
    for rank, pid in ranks.items():
        rows = read_protocol_rows(process_logs.get(str(pid), ''))
        if not rows or any(row['pid'] != pid or row['device'] != rank for row in rows):
            raise ValueError(f'Missing or mismatched GPU READ table for rank {rank}')
        payload_rows = [row for row in rows if row['range'].endswith('inf') or
                        int(row['range'].split('..')[-1]) >= min_descriptor_bytes]
        if not payload_rows or not any(row['range'].endswith('inf') and
                                       int(row['range'].split('..')[0]) <= min_descriptor_bytes
                                       for row in payload_rows):
            raise ValueError(f'No complete bulk KV protocol range for rank {rank}')
        for row in payload_rows:
            lane = row['lane']
            correct = 'cuda_ipc/cuda' in lane if route == 'local' else bool(re.search(r'\b(?:rc_mlx5|dc_mlx5|rc_verbs)/', lane))
            if row['method'] != 'zero-copy' or not correct or 'tcp/' in lane:
                raise ValueError(f'Unexpected {route} KV payload protocol for rank {rank}: {lane}')
        proof[str(rank)] = {'pid': pid, 'read_rows': rows,
                            'minimum_descriptor_bytes': min_descriptor_bytes}
    return proof


def transfer_delta(before, after, selected, expected_bytes):
    """Only valid for isolated requests; aggregate counters mix concurrent work."""
    if set(before) != set(after) or selected not in before or expected_bytes <= 0:
        raise ValueError('Invalid isolated request snapshots')
    result = {}
    for role in before:
        for name in (COUNT, BYTES, TIME_COUNT, TIME_SUM, *FAILURES):
            if name not in before[role] or name not in after[role]:
                raise ValueError(f'Missing required metric {name}')
            if not all(math.isfinite(s[role][name]) for s in (before, after)):
                raise ValueError('Non-finite transfer metric')
            if after[role][name] < before[role][name]:
                raise ValueError('Transfer counter reset')
        if any(after[role][name] != before[role][name] for name in FAILURES):
            raise ValueError('Transfer error counter changed')
        counts = after[role][COUNT] - before[role][COUNT]
        time_count = after[role][TIME_COUNT] - before[role][TIME_COUNT]
        payload = after[role][BYTES] - before[role][BYTES]
        time_sum = after[role][TIME_SUM] - before[role][TIME_SUM]
        wanted_count = 4 if role == selected else 0
        wanted_bytes = expected_bytes if role == selected else 0
        if counts != wanted_count or time_count != wanted_count or payload != wanted_bytes:
            raise ValueError('Wrong selected engine, rank transfer count, or total KV bytes')
        if (time_sum > 0) != (role == selected):
            raise ValueError('Missing or unexpected transfer duration')
        result[role] = {'rank_transfers': counts, 'total_bytes': payload,
                        'mean_rank_transfer_seconds': time_sum / counts if counts else None}
    return result
