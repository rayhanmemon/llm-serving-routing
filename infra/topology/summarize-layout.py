#!/usr/bin/env python3
"""Independently summarize complete one-host layout epochs without dropping requests."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

METRICS=('vllm:nixl_num_descriptors','vllm:nixl_post_time_seconds','vllm:nixl_xfer_time_seconds')

def summarize(folder):
    epochs={}
    for name in ('default-a','packed-doc','packed','default-b'):
        path=folder/name
        if not (path/'requests.json').exists():continue
        rows=json.loads((path/'requests.json').read_text())
        completed=(path/'complete.json').exists()
        mode=json.loads((path/'plan.json').read_text()).get('mode','comparison') if (path/'plan.json').exists() else 'comparison'
        timed=[r for r in rows if r['stage'] in ('repeated','churn-timed') and 'components' in r]
        expected=(4,0) if mode=='qualification' else (26,12)
        if completed and (len(rows),len(timed))!=expected:raise ValueError('Wrong complete-epoch request count')
        for row in timed:
            for metric in METRICS:
                a=row['before']['local']['values'];b=row['after']['local']['values']
                count=b[metric+'_count']-a[metric+'_count']
                if count!=4:raise ValueError('Partial rank counter flush')
                value=(b[metric+'_sum']-a[metric+'_sum'])/count
                if abs(value-row['components'][metric])>1e-8*max(1,abs(value)):raise ValueError('Component mismatch')
            if row['response']['usage']!={'prompt_tokens':122880,'completion_tokens':32,'total_tokens':122912}:
                # Some vLLM releases add detailed usage fields; check core counts only.
                if any(row['response']['usage'].get(k)!=v for k,v in {'prompt_tokens':122880,'completion_tokens':32}.items()):raise ValueError('Unexpected input/output')
        result={'complete':completed,'mode':mode,'requests':len(rows),'timed_requests':len(timed),
                'requests_sha256':hashlib.sha256((path/'requests.json').read_bytes()).hexdigest()}
        if timed:
            result.update(mean_ttft_ms=statistics.mean(r['response']['ttft_seconds']*1000 for r in timed),
                          median_ttft_ms=statistics.median(r['response']['ttft_seconds']*1000 for r in timed),
                          mean_post_ms_per_rank=statistics.mean(r['components'][METRICS[1]]*1000 for r in timed),
                          mean_transfer_ms_per_rank=statistics.mean(r['components'][METRICS[2]]*1000 for r in timed),
                          descriptor_counts=sorted({r['components'][METRICS[0]] for r in timed}),
                          slow_requests=sum(r['components'][METRICS[0]]>=60000 and r['components'][METRICS[1]]>=.1 for r in timed))
        result['direct_controls']=[{'case':r['case_id'],'input_tokens':r['input_tokens'],
                                   'output_tokens':r['response']['usage']['completion_tokens'],
                                   'ttft_ms':r['response']['ttft_seconds']*1000}
                                  for r in rows if r['route']=='direct-local' and 'response' in r]
        epochs[name]=result
    plan_path=folder/'default-a/plan.json'
    planned=json.loads(plan_path.read_text()).get('planned_epochs',['default-a','packed','default-b']) if plan_path.exists() else ['default-a','packed','default-b']
    compared=bool(epochs) and all(x['complete'] and x['mode']=='comparison' for x in epochs.values())
    return {'epochs':epochs,'planned_epochs':planned,'complete_layout_comparison':set(epochs)==set(planned) and compared,'complete_three_epoch_comparison':len(epochs)==3 and compared,
            'complete_restart_qualification':set(epochs)==set(planned) and all(x['complete'] and x['mode']=='qualification' for x in epochs.values()),
            'cautions':['One local GPU host; no remote path or router policy comparison.',
                        'Transfer includes posting; per-rank averages are not TP-group critical-path times.',
                        'Fixed repeated prompt and request history, not production traffic or broad replication.',
                        'Direct controls have few observations; inspect computation and correctness before attributing all TTFT changes to transfer.']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=summarize(a.folder);a.out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
