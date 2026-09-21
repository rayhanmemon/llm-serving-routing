#!/usr/bin/env python3
"""Summarize isolated-request vLLM metric deltas and observed descriptor counts."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def average(row,role,metric):
    before=row['before'][role]['values'];after=row['after'][role]['values']
    count=after[metric+'_count']-before[metric+'_count']
    if count<=0:raise ValueError('No observations for '+metric)
    return (after[metric+'_sum']-before[metric+'_sum'])/count


def analyze(folder):
    rows=json.loads((folder/'timings/requests.json').read_text())
    rows=[row for row in rows if not row.get('warmup')]
    if len(rows)!=96:raise ValueError('Full timing set required for this analysis')
    records=[]
    for row in rows:
        role=row['route'][3:]
        records.append({'pair':row['pair_id'],'block':row['block'],'input_tokens':row['input_tokens'],
            'route':role,'client_ttft_ms':row['response']['ttft_seconds']*1000,
            'descriptors_per_rank':average(row,role,'vllm:nixl_num_descriptors'),
            'post_ms_per_rank':average(row,role,'vllm:nixl_post_time_seconds')*1000,
            'transfer_ms_per_rank':average(row,role,'vllm:nixl_xfer_time_seconds')*1000,
            'prefill_compute_ms':average(row,'prefill','vllm:request_prefill_time_seconds')*1000})
    groups=[];modes=[]
    for length in sorted({r['input_tokens'] for r in records}):
        for route in ['local','remote']:
            selected=[r for r in records if r['input_tokens']==length and r['route']==route]
            groups.append({'input_tokens':length,'route':route,**{key:statistics.mean(r[key] for r in selected)
                for key in ['client_ttft_ms','descriptors_per_rank','post_ms_per_rank','transfer_ms_per_rank','prefill_compute_ms']}})
            for count in sorted({r['descriptors_per_rank'] for r in selected}):
                subset=[r for r in selected if r['descriptors_per_rank']==count]
                modes.append({'input_tokens':length,'route':route,'descriptors_per_rank':count,'requests':len(subset),
                              'post_ms_per_rank':statistics.mean(r['post_ms_per_rank'] for r in subset),
                              'transfer_ms_per_rank':statistics.mean(r['transfer_ms_per_rank'] for r in subset)})
    paired=defaultdict(dict)
    for row in records:paired[row['pair']][row['route']]=row
    if len(paired)!=48 or any(set(pair)!={'local','remote'} for pair in paired.values()):
        raise ValueError('Expected 48 complete local/remote pairs')
    low_descriptor_local_wins=0;other_local_wins=0;low_descriptor_local_requests=0
    for pair in paired.values():
        local,remote=pair['local'],pair['remote']
        low=local['descriptors_per_rank']==64
        low_descriptor_local_requests+=low
        if local['client_ttft_ms']<remote['client_ttft_ms']:
            low_descriptor_local_wins+=low;other_local_wins+=not low
    low_count=sum(r['descriptors_per_rank']==64 for r in records)
    low_first=sum(r['descriptors_per_rank']==64 and next(iter(paired[r['pair']]))==r['route'] for r in records)
    return {'groups':groups,'descriptor_modes':modes,'per_request':records,
            'exploratory_association':{'local_requests_with_64_descriptors':low_descriptor_local_requests,
                'local_wins_with_64_descriptors':low_descriptor_local_wins,'other_local_wins':other_local_wins,
                'all_requests_with_64_descriptors':low_count,'requests_with_64_descriptors_first_in_pair':low_first},
            'cautions':['Descriptor grouping is exploratory, not a randomized layout ablation.',
                        'Transfer duration includes posting; do not add them.',
                        'Data movement can overlap posting; subtraction does not measure pure bandwidth.',
                        'NIXL times are per-rank averages, not the four-rank critical path.']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=analyze(a.folder);a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['exploratory_association']))
