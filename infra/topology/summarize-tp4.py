#!/usr/bin/env python3
"""Independently reconcile the frozen TP4 request plan with saved client responses."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics


def digest(body):
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def check_response(row,case,qualification=False):
    expected=dict(case['request_body'])
    if qualification:expected['ignore_eos']=False
    if row.get('error') or row['request_sha256']!=digest(expected):raise ValueError('Failed or changed request')
    response=row['response'];usage=response['usage']
    if response['status']!=200 or usage['prompt_tokens']!=case['input_tokens']:raise ValueError('Wrong status/input count')
    if not 0<usage['completion_tokens']<=expected['max_tokens']:raise ValueError('Wrong output count')
    if not qualification and usage['completion_tokens']!=expected['max_tokens']:raise ValueError('Truncated timed output')
    first=next((event['elapsed_seconds'] for event in response['events']
                if any(choice.get('text','') for choice in event['data'].get('choices',[]))),None)
    if first is None or first!=response['ttft_seconds'] or first>response['completion_seconds']:
        raise ValueError('TTFT does not match first generated text event')
    chosen=row['route'][3:] if row['route'].startswith('pd-') else None
    for role,before_snapshot in row['before'].items():
        before=before_snapshot['values'];after=row['after'][role]['values']
        count=after['vllm:nixl_bytes_transferred_count']-before['vllm:nixl_bytes_transferred_count']
        size=after['vllm:nixl_bytes_transferred_sum']-before['vllm:nixl_bytes_transferred_sum']
        if count!=(4 if role==chosen else 0) or size!=(case['input_tokens']*262144 if role==chosen else 0):
            raise ValueError('Observed transfer count/bytes do not match selected TP4 engine')
        if any(after[name]!=before[name] for name in ['vllm:nixl_num_failed_transfers_total',
               'vllm:nixl_num_failed_notifications_total','vllm:nixl_num_kv_expired_reqs_total']):
            raise ValueError('Observed transfer errors')
    return response


def summarize(folder,suite_path):
    raw=gzip.decompress(suite_path.read_bytes());suite=json.loads(raw);sha=hashlib.sha256(raw).hexdigest()
    cases={c['id']:c for c in suite['cases']};qualified={};gold=0
    for phase in ['local','remote']:
        plan=json.loads((folder/phase/'plan.json').read_text())
        if plan['suite_sha256']!=sha:raise ValueError('Qualification suite changed')
        marker=json.loads((folder/phase/'complete.json').read_text())
        rows=json.loads((folder/phase/'requests.json').read_text())
        if len(rows)!=16 or marker['requests']!=16:raise ValueError('Qualification incomplete')
        observed={}
        for row in rows:
            key=(row['case_id'],row['route'])
            if key in observed:raise ValueError('Duplicate qualification request')
            response=check_response(row,cases[row['case_id']],True)
            observed[key]=response['text'].strip()
            gold+=observed[key]==cases[row['case_id']]['gold_path']
        for case in cases:
            direct=observed[(case,'direct-'+phase)];pd=observed[(case,'pd-'+phase)]
            if direct!=pd:raise ValueError('Direct/P-D qualification mismatch')
            if phase=='remote' and qualified[case]!=pd:raise ValueError('Cross-host qualification mismatch')
            qualified[case]=pd
    if json.loads((folder/'timings/plan.json').read_text())['suite_sha256']!=sha:raise ValueError('Timing suite changed')
    rows=json.loads((folder/'timings/requests.json').read_text())
    warmups=[r for r in rows if r.get('warmup')];timed=[r for r in rows if not r.get('warmup')]
    for row in warmups:check_response(row,cases[row['case_id']])
    expected=[(p,role) for p in suite['pairs'] for role in p['order']]
    if len(timed)>len(expected):raise ValueError('Extra timed requests')
    pairs=defaultdict(dict);records=[]
    for row,(pair,role) in zip(timed,expected):
        if row['pair_id']!=pair['id'] or row['route']!='pd-'+role or row['case_id']!=pair['case_id']:
            raise ValueError('Timing case, route or order differs from frozen plan')
        response=check_response(row,cases[row['case_id']])
        pairs[pair['id']][role]=response['ttft_seconds']*1000
    for pair in suite['pairs']:
        observed=pairs[pair['id']]
        if len(observed)==2:
            records.append({'pair':pair['id'],'block':pair['block'],'input_tokens':pair['input_tokens'],
                            'first':pair['order'][0],'local_ms':observed['local'],'remote_ms':observed['remote'],
                            'remote_minus_local_ms':observed['remote']-observed['local']})
    groups=[]
    for length in sorted({p['input_tokens'] for p in suite['pairs']}):
        selected=[r for r in records if r['input_tokens']==length]
        if not selected:continue
        groups.append({'input_tokens':length,'complete_pairs':len(selected),
                       'local_median_ms':statistics.median(r['local_ms'] for r in selected),
                       'remote_median_ms':statistics.median(r['remote_ms'] for r in selected),
                       'paired_mean_remote_minus_local_ms':statistics.mean(r['remote_minus_local_ms'] for r in selected),
                       'local_faster_pairs':sum(r['remote_minus_local_ms']>0 for r in selected),
                       'by_first_route':{role:statistics.mean(r['remote_minus_local_ms'] for r in selected if r['first']==role)
                                         for role in ['local','remote'] if any(r['first']==role for r in selected)}})
    complete=(folder/'timings/complete.json').exists() and len(timed)==96 and len(warmups)==16
    if (folder/'timings/complete.json').exists() and not complete:raise ValueError('Completion marker conflicts with request counts')
    return {'suite_sha256':sha,'qualification_requests':32,'matching_cases':len(qualified),'gold_matches':gold,
            'complete':complete,'timed_requests':len(timed),'complete_pairs':len(records),
            'warmups':len(warmups),'groups':groups,'pairs':records,
            'scope':'Forced-route idle screen; no load calibration or routing-policy performance claim.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--suite',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=summarize(a.folder,a.suite);a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='pairs'},indent=2))
