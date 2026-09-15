#!/usr/bin/env python3
"""Summarize aggregate NIXL histogram deltas; transport selection needs log evidence."""
import argparse
import json
import math
from pathlib import Path
from evidence import require_collection
from prometheus_client.parser import text_string_to_metric_families


def values(path):
    result = {}
    for family in text_string_to_metric_families(path.read_text()):
        for sample in family.samples:
            if sample.name.startswith('vllm:nixl_'):
                result[sample.name] = result.get(sample.name, 0) + sample.value
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', required=True, type=Path)
    p.add_argument('--after', required=True, type=Path)
    p.add_argument('--decoder-pod', required=True)
    p.add_argument('--expected-transfers', required=True, type=int, help='Qualified operations/request times measured request count')
    a = p.parse_args()
    if a.expected_transfers <= 0:
        p.error('expected-transfers must be positive')
    require_collection(a.before)
    require_collection(a.after, measured=True)
    before = values(a.before / (a.decoder_pod + '-metrics.txt'))
    after = values(a.after / (a.decoder_pod + '-metrics.txt'))
    names = {'bytes': 'vllm:nixl_bytes_transferred_sum', 'operations': 'vllm:nixl_bytes_transferred_count',
             'seconds': 'vllm:nixl_xfer_time_seconds_sum', 'timed_operations': 'vllm:nixl_xfer_time_seconds_count'}
    result = {}
    for label, metric in names.items():
        if metric not in before or metric not in after:
            raise SystemExit('Missing required metric: ' + metric)
        delta = after[metric] - before[metric]
        if not math.isfinite(delta) or delta <= 0:
            raise SystemExit('No positive transfer delta or counter reset: ' + metric)
        result[label] = delta
    if result['operations'] != a.expected_transfers or result['timed_operations'] != a.expected_transfers:
        raise SystemExit('Transfer counts do not match the qualified expected count')
    counters = ['vllm:nixl_num_failed_transfers_total', 'vllm:nixl_num_kv_expired_reqs_total',
                'vllm:nixl_num_failed_notifications_total']
    result['failure_observations'] = {}
    for path in sorted(a.before.glob('*-metrics.txt')):
        other = a.after / path.name
        if not other.exists():
            raise SystemExit('After snapshot missing: ' + path.name)
        old, new = values(path), values(other)
        for metric in counters:
            if metric in old or metric in new:
                if metric not in old or metric not in new:
                    raise SystemExit('Failure counter series changed: ' + metric)
                delta = new[metric] - old[metric]
                result['failure_observations'][path.name + ':' + metric] = delta
                if not math.isfinite(delta) or delta != 0:
                    raise SystemExit('Transfer failure/expiry or counter reset: ' + path.name + ':' + metric)
    for metric in set(before) | set(after):
        if metric.endswith('_created') and before.get(metric) != after.get(metric):
            raise SystemExit('Metric creation timestamp changed: ' + metric)
    result['effective_bytes_per_second'] = result['bytes'] / result['seconds']
    result['mean_observed_transfer_seconds'] = result['seconds'] / result['operations']
    result['interpretation'] = 'Aggregate connector observations, not per-request transfer time or physical-link bandwidth. Verify UCX payload path separately.'
    result['unexposed_failure_counters'] = 'Missing counters are not proof of zero failures; inspect engine/sidecar logs too.'
    (a.after / 'transfer-delta.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
