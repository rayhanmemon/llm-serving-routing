#!/usr/bin/env python3
"""Fixed one-host layout diagnostic; all requests retained, including null results."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import time

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('client',HERE/'tp4-client.py')
c=importlib.util.module_from_spec(sp);sp.loader.exec_module(c)
METRICS=('vllm:nixl_num_descriptors','vllm:nixl_post_time_seconds','vllm:nixl_xfer_time_seconds')


def metric_delta(row):
    result={}
    for name in METRICS:
        a=row['before']['local']['values'];b=row['after']['local']['values']
        count=b[name+'_count']-a[name+'_count']
        if count!=4:raise ValueError('Expected four isolated rank observations: '+name)
        result[name]=(b[name+'_sum']-a[name+'_sum'])/count
    return result


def run(host,config_path,suite_path,out,epoch,deadline):
    config,arch=c.settings.load_config(config_path)
    suite=json.loads(gzip.decompress(suite_path.read_bytes()))
    if suite['config_sha256']!=hashlib.sha256(config_path.read_bytes()).hexdigest():
        raise ValueError('Frozen suite/base configuration mismatch')
    out=out/epoch;out.mkdir(parents=True,exist_ok=False)
    hosts={'local':host};rows=[];base=c.evidence(hosts);c.write(out/'baseline.json',base)
    if epoch=='packed':
        for role,log in base['local']['engine_logs'].items():
            if log.count('Allocating a cross layer KV cache of shape')<4:
                raise ValueError('No positive cross-layer allocation evidence for every '+role+' rank')
    c.write(out/'plan.json',{'epoch':epoch,'suite_sha256':hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        'sequence':'4K/120K two-case direct/P-D qualification; two short warmups; eight repeated120K; four P-only/P-D pairs',
        'counterfactual_note':'P-only churn changes producer history; it is not a simulated remote performance result'})
    def measure(case,route,stage,body=None):
        body=dict(case['request_body'] if body is None else body)
        row={'case_id':case['id'],'input_tokens':case['input_tokens'],'route':route,'stage':stage,
             'started_unix':time.time(),'before':c.idle(hosts,deadline),
             'request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        rows.append(row)
        try:
            row['response']=c.request(hosts,route,body,deadline)
            for _ in range(40):
                row['after']=c.snapshot(hosts)
                a={r:x['values'] for r,x in row['before'].items()};b={r:x['values'] for r,x in row['after'].items()}
                try:
                    if route=='pd-local':
                        row['transfers']=c.e.transfer_delta(a,b,'local',c.settings.cache_bytes_per_rank(arch,case['input_tokens'])*4)
                        row['components']=metric_delta(row)
                    else:
                        if any(b[r][n]!=a[r][n] for r in a for n in (c.e.COUNT,c.e.BYTES,*c.e.FAILURES)):
                            raise ValueError('Direct control caused a transfer/error')
                    break
                except ValueError:time.sleep(.25)
            else:raise ValueError('Isolated counters did not settle')
            return row
        finally:c.write(out/'requests.json',rows)
    try:
        selected=[x for x in suite['cases'] if x['input_tokens'] in (4096,122880)]
        parity=[]
        for case in selected:
            body=dict(case['request_body'],ignore_eos=False)
            direct=measure(case,'direct-local','qualification',body)
            pd=measure(case,'pd-local','qualification',body)
            ok=direct['response']['text'].strip()==pd['response']['text'].strip()
            parity.append({'case':case['id'],'matches':ok,'gold_matches':pd['response']['text'].strip()==case['gold_path']})
            if not ok:raise ValueError('Direct/P-D correctness mismatch')
        short=selected[0];long=next(x for x in selected if x['input_tokens']==122880)
        for _ in range(2):measure(short,'pd-local','warmup')
        for _ in range(8):measure(long,'pd-local','repeated')
        # Frozen alternate history, executed in every epoch regardless of outcome.
        # A direct P request changes its allocation phase without touching D.
        for _ in range(4):
            measure(long,'prefill-local','churn',dict(long['request_body'],max_tokens=1,ignore_eos=True))
            measure(long,'pd-local','churn-timed')
        final=c.evidence(hosts);c.write(out/'final-evidence.json',final);c.stable(base,final)
        transport=c.qualify_evidence(final,'local')
        timed=[r for r in rows if r['stage'] in ('repeated','churn-timed')]
        high=[r for r in timed if r['components'][METRICS[0]]>=60000 and r['components'][METRICS[1]]>=.1]
        result={'requests':len(rows),'timed_requests':len(timed),'parity':parity,'transport':transport,
                'default_slow_reproduced':len(high)>=2,'slow_requests':len(high),
                'mean_ttft_seconds':statistics.mean(r['response']['ttft_seconds'] for r in timed),
                'mean_descriptors_per_rank':statistics.mean(r['components'][METRICS[0]] for r in timed),
                'mean_post_seconds_per_rank':statistics.mean(r['components'][METRICS[1]] for r in timed)}
        c.write(out/'complete.json',result);print(json.dumps(result),flush=True)
    except Exception as error:
        c.write(out/'failure.json',{'error':repr(error)});raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--local',required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--suite',type=Path,required=True);p.add_argument('--out',type=Path,default=Path('/results'))
    p.add_argument('--epoch',choices=['default-a','packed','default-b'],required=True);p.add_argument('--deadline',type=float,required=True)
    a=p.parse_args();run(a.local,a.config,a.suite,a.out,a.epoch,a.deadline)
