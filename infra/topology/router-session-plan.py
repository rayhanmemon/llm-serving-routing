#!/usr/bin/env python3
"""Freeze one bounded router comparison; no provisioning or requests."""
import argparse,copy,gzip,hashlib,json,random
from pathlib import Path
from datetime import datetime,timedelta,timezone

POLICIES=('none','hard','soft','absolute-cap','allowance')
GRID={'allowance':[0,1,2,4],'cap':[0,1,4,8,9,16,24], 'weight':[0.1,0.25,0.5,1,2],
      'idle_threshold':[0,1,2,8,9], 'max_busy_score':[0.5,1]}

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def calibration_trials(plan):
    trials=[]
    for repeat in range(2):
        routes=('local','remote') if repeat==0 else ('remote','local')
        for i,state in enumerate(plan['training_states']):
            for route in routes:
                trials.append({'id':f'train-{repeat}-{i}-{route}','mode':'calibration','state':state,
                               'route':route,'repeat':repeat,'seed':22092026+repeat,'max_seconds':60})
    return trials

def frozen_plan():
    rng=random.Random(22092026)
    trials=[]
    for repeat in range(2):
        traces=['low','high'];rng.shuffle(traces)
        for trace in traces:
            policies=list(POLICIES);rng.shuffle(policies)
            for policy in policies:
                trials.append({'id':f'eval-{repeat}-{trace}-{policy}','mode':'heldout','policy':policy,'trace':trace,'repeat':repeat,'seed':22092026+repeat,'max_seconds':90})
    for trace in ('low','high'):
        for policy in ('allowance','reference'):
            trials.append({'id':f'confirm-{trace}-{policy}','mode':'confirmation','policy':policy,'trace':trace,'repeat':2,'seed':22092028,'max_seconds':90})
    return {'schema_version':2,'layout':'BHLNC','input_tokens':[4096,122880], 'output_tokens':32,'decoder_max_num_seqs':8,
            'qualification_requests_per_host':6,'training_states':[[1,0],[9,8],[17,0],[17,8]],
            'training_repeats':2,'training_probes':32,'training_episodes':16,'training_trial_seconds':60,
            'background_output_tokens':1024,'grid':GRID,'trials':trials,
            'thresholds':{'minimum_ms':50,'minimum_percent':2,'maximum_regression_percent':5},
            'benchmark_engine':'inference-perf0.6.1; frozen-token adapter; native trace scheduler/HTTP/SSE/reports',
            'limitations':'Controlled traces and mean latency; no production or tail-latency claim.'}

def requests_for(plan,trial,cases):
    training=trial['mode']=='calibration'
    variant=0 if training else 1
    bysize={n:next(c for c in cases if c['input_tokens']==n and c['id'].endswith('-'+str(variant))) for n in plan['input_tokens']}
    short=bysize[4096];rows=[]
    def add(at,case,kind,pin=None,output=32):
        body=dict(case['request_body'],max_tokens=output,ignore_eos=True)
        rows.append({'at':at,'kind':kind,'body':body,'pin':pin,'case_id':case['id']})
    if training:
        local,remote=trial['state']
        for role,count in [('local',local),('remote',remote)]:
            for i in range(count):add(i*.15,short,'background',role,plan['background_output_tokens'])
        route=trial['route']
        if route not in ('local','remote'):raise ValueError('Calibration needs one route per episode')
        for at,size in zip((3,6),plan['input_tokens']):
            add(at,bysize[size],'foreground',route)
    else:
        count=2 if trial['trace']=='low' else 18
        for i in range(count):add(i*.2,short,'background',None,plan['background_output_tokens'])
        for i,n in enumerate([4096,122880,122880,4096]):add(3+i*12,bysize[n],'foreground')
        # Identical offered background burst for all policies; no decoder pin.
        for i in range(count):add(22+i*.2,short,'background',None,plan['background_output_tokens'])
    rows.sort(key=lambda r:r['at'])
    for i,row in enumerate(rows):row['index']=i;row['payload_sha256']=digest(row['body'])
    return rows

def make_trial(plan,trial,cases,out,url,pins):
    out.mkdir(parents=True,exist_ok=False)
    rows=requests_for(plan,trial,cases)
    doc={'trial':trial,'requests':rows,'route_tokens':pins,'plan_sha256':digest(plan)}
    (out/'requests.json').write_text(json.dumps(doc))
    epoch=datetime(2026,9,22,tzinfo=timezone.utc)
    csv=['TIMESTAMP,ContextTokens,GeneratedTokens']
    for r in rows:csv.append(f'{(epoch+timedelta(seconds=r["at"])).isoformat(timespec="milliseconds")},{len(r["body"]["prompt"])},{r["body"]["max_tokens"]}')
    (out/'trace.csv').write_text('\n'.join(csv)+'\n')
    trace={'file':str(out/'trace.csv'),'format':'AzurePublicDataset'}
    cfg={'api':{'type':'completion','streaming':True,'headers':{'x-benchmark-run':trial['id']}},
         'data':{'type':'mock','path':str(out/'requests.json'),'trace':trace},
         'load':{'type':'trace_replay','num_workers':1,'worker_max_concurrency':64,'base_seed':trial['seed'],
                 'request_timeout':min(80,trial['max_seconds']),'trace':trace,
                 'stages':[{'rate':len(rows)/50,'duration':50}]},
         'server':{'type':'vllm','model_name':cases[0]['request_body']['model'],'ignore_eos':True,'base_url':url},
         'storage':{'local_storage':{'path':str(out/'reports')}},
         'report':{'request_lifecycle':{'summary':False,'per_stage':False,'per_request':True}}}
    import yaml
    (out/'config.yaml').write_text(yaml.safe_dump(cfg,sort_keys=False))
    return doc

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    a.out.write_text(json.dumps(frozen_plan(),indent=2)+'\n')
