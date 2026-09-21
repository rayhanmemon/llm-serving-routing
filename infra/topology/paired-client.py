#!/usr/bin/env python3
"""Client-side forced-route qualification and paired TTFT. No router policy test."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import re
import time
import urllib.request

HERE=Path(__file__).resolve().parent

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
w=load('worker','single-host-worker.py')
v=load('validator','validate-single-host.py')
COUNT=w.COUNT;SIZE=w.SIZE
RUNNING='vllm:num_requests_running';WAITING='vllm:num_requests_waiting'


def get(url):
    with urllib.request.urlopen(url,timeout=15) as r:return r.read().decode()


def snapshot(config):
    result={}
    for role,url in config['metrics'].items():
        text=get(url);values=w.metrics(text)
        for metric in (RUNNING,WAITING):
            matches=[float(line.rsplit(' ',1)[1]) for line in text.splitlines() if line.startswith(metric+'{') or line.startswith(metric+' ')]
            if not matches:raise ValueError('Missing load gauge')
            values[metric]=sum(matches)
        if not all(math.isfinite(x) for x in values.values()):raise ValueError('Non-finite metric')
        result[role]=values
    return result


def evidence(config):return {role:json.loads(get(url)) for role,url in config['evidence'].items()}


def protocol_tables(log,transport):
    lines=log.splitlines()
    return ['\n'.join(lines[i:i+8]) for i,line in enumerate(lines)
            if 'remote memory read' in line and 'cuda' in line
            and any('zero-copy' in row and transport in row for row in lines[i+1:i+8])]


def check_transfer(before,after,route):
    chosen=route.removeprefix('pd-') if route.startswith('pd-') else None
    for role in before:
        if any(after[role][m]!=before[role][m] for m in w.FAILURES):raise ValueError('Transfer failure')
        if after[role][COUNT]-before[role][COUNT] != int(role==chosen):raise ValueError('Wrong transfer count or route')
        change=after[role][SIZE]-before[role][SIZE]
        if (change>0) != (role==chosen):raise ValueError('Wrong payload delta')


def check_nvlink(a,b,route,payload):
    ids=a['local']['selected_gpus']
    if len(ids)!=2 or len(set(ids))!=2:raise ValueError('Expected distinct local GPUs')
    for role in ('local','remote'):
        if a[role]['selected_gpus']!=b[role]['selected_gpus'] or a[role]['identity']!=b[role]['identity'] or not b[role]['processes_alive']:
            raise ValueError('Worker identities changed')
    x=v.parse_counters(a['local']['nvlink']['stdout'],ids)
    y=v.parse_counters(b['local']['nvlink']['stdout'],ids)
    delta={g:{k:y[g][k]-x[g][k] for k in ('Tx','Rx')} for g in ids}
    if route=='pd-local':
        if abs(delta[ids[0]]['Tx']-payload/1024)>18 or abs(delta[ids[1]]['Rx']-payload/1024)>18:
            raise ValueError('Local KV payload did not cross NVLink')
        if delta[ids[0]]['Rx'] or delta[ids[1]]['Tx']:raise ValueError('Unexpected reverse NVLink traffic')
    elif any(x for d in delta.values() for x in d.values()):raise ValueError('Control/remote request unexpectedly used local NVLink')
    return delta


def request(config,route,body):
    headers={'Content-Type':'application/json'}
    if route.startswith('pd-'):headers['x-prefiller-host-port']=config['prefill']
    req=urllib.request.Request(config['endpoints'][route]+'/v1/completions',data=json.dumps(body).encode(),headers=headers)
    start=time.perf_counter();first=None;events=[];usage=None;text='';done=False
    with urllib.request.urlopen(req,timeout=90) as response:
        if not body.get('stream'):
            result=json.load(response)
            return {'status':response.status,'body':result,'completion_seconds':time.perf_counter()-start}
        for line in response:
            if not line.startswith(b'data:'):continue
            data=line[5:].strip()
            if data==b'[DONE]':done=True;break
            event=json.loads(data);now=time.perf_counter();events.append({'elapsed_seconds':now-start,'data':event})
            if event.get('usage'):usage=event['usage']
            for choice in event.get('choices',[]):
                fragment=choice.get('text','')
                if fragment and first is None:first=now
                text+=fragment
        elapsed=time.perf_counter()-start
    if not done or first is None or usage is None:raise ValueError('Incomplete SSE stream')
    if usage['prompt_tokens']!=len(body['prompt']) or usage['completion_tokens']!=body['max_tokens']:
        raise ValueError('Stream token count mismatch')
    return {'status':200,'ttft_seconds':first-start,'completion_seconds':elapsed,'usage':usage,'text':text,'events':events}


def idle(config):
    for _ in range(50):
        s=snapshot(config)
        if all(not x[RUNNING] and not x[WAITING] for x in s.values()):return s
        time.sleep(.2)
    raise ValueError('Engines did not become idle')


def plan_pairs(cases,repeats=12,seed=21092026):
    rng=random.Random(seed);pairs=[]
    for level,n in [(0,repeats),(1,6),(3,6)]:
        for tokens in (512,8192):
            choices=[c for c in cases if c['input_tokens']==tokens]
            orders=[['pd-local','pd-remote'],['pd-remote','pd-local']]*(n//2)
            rng.shuffle(orders)
            for i,order in enumerate(orders):
                body=dict(choices[i%len(choices)]['request_body'])
                body.update(max_tokens=32,stream=True,stream_options={'include_usage':True})
                body.pop('logprobs',None);body.pop('return_token_ids',None)
                pairs.append({'id':f'load{level}-{tokens}-{i}','load':level,'tokens':tokens,'order':order,'body':body})
    return pairs


def run(config,suite,out,deadline):
    out.mkdir(exist_ok=True)
    raw=suite.read_bytes();plan=json.loads(raw)
    if plan['model']!=w.MODEL or plan['revision']!=w.REVISION:raise ValueError('Wrong frozen model')
    frozen={'suite_sha256':hashlib.sha256(raw).hexdigest(),'pairs':plan_pairs(plan['cases']),
            'ttft_definition':'HTTP request start to first nonempty generated text fragment',
            'scope':'Forced sidecar routes; no EPP policy comparison. Qwen3-0.6B eager.',
            'load_method':'1 or 3 concurrent direct-local 4096-token requests; fresh cohort per foreground request; observed gauges retained.'}
    w.write(out/'timing-plan.json',frozen)
    baseline=evidence(config);rows=[];texts={}
    local_ids=baseline['local']['selected_gpus'];remote_ids=baseline['remote']['selected_gpus']
    if len(local_ids)!=2 or len(remote_ids)!=1 or len(set(local_ids+remote_ids))!=3:
        raise ValueError('Three distinct physical GPUs required')
    if not re.search(r'^GPU0\s+X\s+NV18',baseline['local']['topology']['stdout'],re.M):
        raise ValueError('Local physical NVLink topology missing')
    if len(plan['cases'])!=8:raise ValueError('Expected eight qualification cases')
    idle(config);time.sleep(1);check_nvlink(baseline,evidence(config),'idle',0)
    def left():
        if time.time()+150>=deadline:raise TimeoutError('Collection reserve reached')
    def measured(route,body,qualification=False):
        left();before=snapshot(config);a=evidence(config) if qualification else None
        response=request(config,route,body)
        for _ in range(30):
            after=snapshot(config)
            try:check_transfer(before,after,route);break
            except ValueError:
                time.sleep(.1)
        check_transfer(before,after,route)
        row={'route':route,'request':body,'response':response,'before':before,'after':after}
        if qualification:
            b=evidence(config);payload=after['local' if route=='pd-local' else 'remote'][SIZE]-before['local' if route=='pd-local' else 'remote'][SIZE] if route.startswith('pd-') else 0
            row.update(evidence_before=a,evidence_after=b,nvlink_delta=check_nvlink(a,b,route,payload))
        return row
    for case in plan['cases']:
        for route in ('direct-local','direct-remote','pd-local','pd-remote'):
            idle(config);row=measured(route,case['request_body'],True);row['case_id']=case['case_id'];rows.append(row)
            w.write(out/'qualification.json',rows)
            body=row['response']['body']
            if body['usage']['prompt_tokens']!=case['input_tokens'] or body['usage']['completion_tokens']!=8:raise ValueError('Wrong qualification tokens')
            texts.setdefault(case['case_id'],[]).append(body['choices'][0]['text'].strip())
        if len(set(texts[case['case_id']]))!=1:raise ValueError('Cross-route parity failed: '+case['case_id'])
    final=evidence(config)
    if not protocol_tables(final['local']['protocol'],'cuda_ipc'):raise ValueError('Local engine READ not CUDA IPC')
    if not protocol_tables(final['remote']['protocol'],'rc_mlx5'):raise ValueError('Remote engine READ not RDMA')
    w.write(out/'qualified.json',{'requests':32,'cases':8,'evidence':final,'baseline':baseline})
    print('Both transports and 8/8 four-route parity passed; starting warmups.',flush=True)
    timings=[]
    for tokens in (512,8192):
        body=next(p['body'] for p in frozen['pairs'] if p['tokens']==tokens)
        for route in ('pd-local','pd-remote'):
            for i in range(2):
                idle(config);row=measured(route,body);row.update(warmup=True,tokens=tokens,repeat=i);timings.append(row)
                w.write(out/'timings.json',timings)
    for pair in frozen['pairs']:
        for route in pair['order']:
            left();idle(config)
            futures=[]
            with ThreadPoolExecutor(max_workers=3) as pool:
                if pair['load']:
                    bg=dict(next(c['request_body'] for c in plan['cases'] if c['input_tokens']==512))
                    bg.update(max_tokens=4096,stream=True,stream_options={'include_usage':True});bg.pop('logprobs',None);bg.pop('return_token_ids',None)
                    futures=[pool.submit(request,config,'direct-local',bg) for _ in range(pair['load'])]
                    for _ in range(100):
                        observed=snapshot(config)
                        if observed['local'][RUNNING]>=pair['load']:break
                        if any(f.done() for f in futures):raise ValueError('Background cohort ended before measurement')
                        time.sleep(.05)
                    else:raise ValueError('Requested local load not observed')
                row=measured(route,pair['body']);row.update(pair_id=pair['id'],load=pair['load'],tokens=pair['tokens'],warmup=False)
                if futures and any(f.done() for f in futures):raise ValueError('Background cohort ended during foreground request')
                row['background']=[f.result(timeout=90) for f in futures]
                timings.append(row);w.write(out/'timings.json',timings)
        print('Completed '+pair['id'],flush=True)
    idle(config);end=evidence(config)
    for role in ('local','remote'):
        if baseline[role]['identity']!=end[role]['identity'] or not end[role]['processes_alive']:raise ValueError('Workers changed during timing')
    w.write(out/'complete.json',{'complete':True,'timed_requests':sum(not x['warmup'] for x in timings),'warmups':sum(x['warmup'] for x in timings),'final_evidence':end})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--suite',type=Path,required=True);p.add_argument('--out',type=Path,default=Path('/results'));p.add_argument('--deadline',type=float,required=True);a=p.parse_args()
    try:run(json.loads(a.config.read_text()),a.suite,a.out,a.deadline)
    except Exception as e:
        w.write(a.out/'failure.json',{'error':repr(e),'time':time.time()});raise
