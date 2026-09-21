#!/usr/bin/env python3
"""Independently check saved paired results; summarize complete pairs only."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import statistics

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('client',HERE/'paired-client.py');c=importlib.util.module_from_spec(s);s.loader.exec_module(c)


def require(ok,message):
    if not ok:raise ValueError(message)


def summarize(folder,suite):
    read=lambda name:json.loads((folder/name).read_text())
    plan=read('timing-plan.json');raw=suite.read_bytes();frozen=json.loads(raw)
    require(plan['suite_sha256']==hashlib.sha256(raw).hexdigest(),'Frozen suite changed')
    require(plan['pairs']==c.plan_pairs(frozen['cases']),'Timing plan differs from frozen construction')
    cases={x['case_id']:x for x in frozen['cases']};q=read('qualification.json')
    expected={(case,route) for case in cases for route in ('direct-local','direct-remote','pd-local','pd-remote')}
    require(len(q)==32 and {(r['case_id'],r['route']) for r in q}==expected,'Incomplete qualification')
    texts={};gold=0
    for row in q:
        case=cases[row['case_id']];require(row['request']==case['request_body'],'Qualification request changed')
        response=row['response'];require(response['status']==200,'Qualification HTTP error')
        body=response['body'];require(body['usage']['prompt_tokens']==case['input_tokens'] and body['usage']['completion_tokens']==8,'Qualification token mismatch')
        text=body['choices'][0]['text'].strip();texts.setdefault(row['case_id'],set()).add(text);gold+=text==case['gold_text']
        c.check_transfer(row['before'],row['after'],row['route'])
        role='local' if row['route']=='pd-local' else 'remote'
        payload=row['after'][role][c.SIZE]-row['before'][role][c.SIZE]
        c.check_nvlink(row['evidence_before'],row['evidence_after'],row['route'],payload)
    require(all(len(x)==1 for x in texts.values()),'Cross-route parity failed')
    evidence=read('qualified.json')['evidence']
    require(c.protocol_tables(evidence['local']['protocol'],'cuda_ipc'),'Local transport unproven')
    require(c.protocol_tables(evidence['remote']['protocol'],'rc_mlx5'),'Remote transport unproven')
    rows=read('timings.json');lookup={p['id']:p for p in plan['pairs']};pairs={};seen=set();warmups=[]
    for row in rows:
        if row['warmup']:
            warmups.append(row);continue
        pair=lookup[row['pair_id']];key=(row['pair_id'],row['route'])
        require(key not in seen,'Duplicate measurement');seen.add(key)
        require(row['request']==pair['body'] and row['load']==pair['load'] and row['tokens']==pair['tokens'],'Measurement request changed')
        c.check_transfer(row['before'],row['after'],row['route'])
        r=row['response'];require(r['status']==200 and 0<r['ttft_seconds']<=r['completion_seconds'],'Invalid TTFT')
        require(r['usage']['prompt_tokens']==pair['tokens'] and r['usage']['completion_tokens']==32,'Timing token mismatch')
        events=r['events'];generated=[e for e in events if any(x.get('text') for x in e['data'].get('choices',[]))]
        require(generated and abs(generated[0]['elapsed_seconds']-r['ttft_seconds'])<1e-9,'TTFT not first generated text')
        require(len(row['background'])==pair['load'],'Wrong background cohort')
        if pair['load']:
            require(row['before']['local'][c.RUNNING]>=pair['load'],'Local load was not observed')
        pairs.setdefault(pair['id'],[]).append(row)
    require(len(warmups)==8,'Warmup block incomplete')
    groups={};incomplete=[]
    for key,entries in pairs.items():
        pair=lookup[key]
        if len(entries)!=2:
            incomplete.append(key);continue
        require([x['route'] for x in entries]==pair['order'],'Route order changed')
        values={x['route']:x['response']['ttft_seconds']*1000 for x in entries}
        groups.setdefault((pair['load'],pair['tokens']),[]).append({'pair':key,'first':pair['order'][0],
            'local_ms':values['pd-local'],'remote_ms':values['pd-remote'],
            'remote_minus_local_ms':values['pd-remote']-values['pd-local']})
    summaries=[];rng=random.Random(21092026)
    for (load,tokens),pairs_in_group in sorted(groups.items()):
        differences=[x['remote_minus_local_ms'] for x in pairs_in_group];n=len(differences)
        boots=sorted(statistics.mean(rng.choices(differences,k=n)) for _ in range(5000))
        summaries.append({'background_requests':load,'prompt_tokens':tokens,'complete_pairs':n,
            'local_median_ms':statistics.median(x['local_ms'] for x in pairs_in_group),
            'remote_median_ms':statistics.median(x['remote_ms'] for x in pairs_in_group),
            'mean_remote_minus_local_ms':statistics.mean(differences),'median_remote_minus_local_ms':statistics.median(differences),
            'exploratory_paired_bootstrap_95pct_ms':[boots[125],boots[4874]],
            'local_faster_pairs':sum(x>0 for x in differences),
            'mean_difference_by_first_route':{route:statistics.mean(x['remote_minus_local_ms'] for x in pairs_in_group if x['first']==route) for route in ('pd-local','pd-remote') if any(x['first']==route for x in pairs_in_group)},'pairs':pairs_in_group})
    return {'qualified':True,'gold_matches':gold,'gold_total':32,'timed_requests_saved':len(rows)-len(warmups),
            'planned_timed_requests':96,'complete_pairs':sum(len(x) for x in groups.values()),'incomplete_pairs':incomplete,
            'all_measurements_complete':len(seen)==96 and (folder/'complete.json').exists(),
            'groups':summaries,'sign':'Positive remote-minus-local means local TTFT was lower.',
            'limits':'Small-model forced-route fixture, small samples, exploratory intervals. No router policy gain or production-scale conclusion.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--suite',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=summarize(a.folder,a.suite);a.out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='groups'}))
