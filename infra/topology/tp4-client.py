#!/usr/bin/env python3
"""Isolated TP4 qualification and paired client timings with immediate evidence saves."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import time
import urllib.request

HERE=Path(__file__).resolve().parent
def module(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
e=module('tp4-evidence');settings=module('tp4-config')
RUNNING='vllm:num_requests_running';WAITING='vllm:num_requests_waiting'


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)


def get(url):
    with urllib.request.urlopen(url,timeout=30) as response:return response.read().decode()


def metrics(url):
    raw=get(url);values={}
    for line in raw.splitlines():
        if not line or line.startswith('#'):continue
        name=line.split('{')[0].split()[0]
        try:value=float(line.rsplit(' ',1)[1])
        except ValueError:continue
        values[name]=values.get(name,0)+value
    for name in (e.COUNT,e.BYTES,e.TIME_COUNT,e.TIME_SUM,*e.FAILURES,RUNNING,WAITING):
        if name not in values or not math.isfinite(values[name]):raise ValueError('Missing/nonfinite '+name)
    return {'raw':raw,'values':values}


def snapshot(hosts):
    urls={'prefill':f'http://{hosts["local"]}:8100/metrics',
          **{r:f'http://{host}:8200/metrics' for r,host in hosts.items()}}
    return {role:metrics(url) for role,url in urls.items()}


def idle(hosts,deadline):
    while time.time()<deadline-180:
        snap=snapshot(hosts)
        if all(x['values'][RUNNING]==0 and x['values'][WAITING]==0 for x in snap.values()):return snap
        time.sleep(.5)
    raise TimeoutError('Idle wait reached collection reserve')


def request(hosts,route,body,deadline):
    if time.time()+180>=deadline:raise TimeoutError('Collection reserve reached')
    role=route.split('-')[-1];port=8000 if route.startswith('pd-') else 8200
    headers={'Content-Type':'application/json'}
    if port==8000:headers['x-prefiller-host-port']=hosts['local']+':8100'
    req=urllib.request.Request(f'http://{hosts[role]}:{port}/v1/completions',data=json.dumps(body).encode(),headers=headers)
    started=time.perf_counter();events=[];text='';usage=None;first=None;done=False
    with urllib.request.urlopen(req,timeout=min(300,deadline-time.time()-120)) as response:
        if response.status!=200:raise ValueError('Non-200 response')
        for line in response:
            if time.time()+120>=deadline:raise TimeoutError('Streaming reached collection reserve')
            if not line.startswith(b'data:'):continue
            raw=line[5:].strip()
            if raw==b'[DONE]':done=True;break
            event=json.loads(raw);elapsed=time.perf_counter()-started
            events.append({'elapsed_seconds':elapsed,'data':event})
            if 'error' in event:raise ValueError('Streaming error')
            if event.get('usage'):usage=event['usage']
            for choice in event.get('choices',[]):
                fragment=choice.get('text','');text+=fragment
                if fragment and first is None:first=elapsed
    if not done or first is None or usage is None:raise ValueError('Incomplete generated stream')
    if usage['prompt_tokens']!=len(body['prompt']):raise ValueError('Input token count changed')
    actual=usage['completion_tokens'];limit=body['max_tokens']
    if not 0<actual<=limit or (body.get('ignore_eos') and actual!=limit):raise ValueError('Wrong output token count')
    return {'status':200,'ttft_seconds':first,'completion_seconds':time.perf_counter()-started,
            'usage':usage,'text':text,'events':events}


def evidence(hosts):return {role:json.loads(get(f'http://{host}:8300/evidence')) for role,host in hosts.items()}


def stable(before,after):
    for role in before:
        if before[role]['devices']!=after[role]['devices'] or before[role]['identity']!=after[role]['identity'] or not after[role]['processes_alive']:
            raise ValueError('GPU worker identity changed')


def qualify_evidence(record,route):
    ids=[gpu for host in record.values() for group in host['devices'].values() for gpu in group]
    expected=12 if 'remote' in record else 8
    if len(ids)!=expected or len(set(ids))!=expected:raise ValueError('Physical GPUs overlap or are missing')
    local=record['local']
    topology=[line.split() for line in local['topology'].splitlines() if re.match(r'^GPU\d+\s',line)]
    if len(topology)<8 or any(len(row)<9 or any(not row[j+1].startswith('NV') for j in range(8) if i!=j) for i,row in enumerate(topology[:8])):
        raise ValueError('Expected full local eight-GPU NVLink connectivity')
    host=record[route];log=host['engine_logs']['decode']
    pids=e.worker_pids(log)
    logs={str(pid):host['ucx_logs'].get(f'ucx-decode.{pid}.log','') for pid in pids.values()}
    return e.validate_rank_transports(log,logs,route)


def run(hosts,suite_path,config_path,out,phase,deadline):
    config,arch=settings.load_config(config_path)
    raw=gzip.decompress(suite_path.read_bytes());suite=json.loads(raw)
    if suite['config_sha256']!=hashlib.sha256(config_path.read_bytes()).hexdigest():raise ValueError('Settings differ from frozen suite')
    if suite['model']!=config['model'] or suite['revision']!=config['revision']:raise ValueError('Wrong model')
    out.mkdir(parents=True,exist_ok=True)
    phase_dir=out/phase
    if phase_dir.exists():raise ValueError('Refuse to overwrite an earlier phase')
    phase_dir.mkdir()
    write(phase_dir/'plan.json',{'suite_sha256':hashlib.sha256(raw).hexdigest(),'phase':phase,'hosts':hosts})
    rows=[];base=evidence(hosts);write(phase_dir/'baseline.json',base)
    def measured(case,route,body):
        before=idle(hosts,deadline);start=time.time()
        row={'case_id':case['id'],'route':route,'input_tokens':case['input_tokens'],
             'started_unix':start,'request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
             'before':before}
        rows.append(row)
        try:
            row['response']=request(hosts,route,body,deadline)
            write(phase_dir/'requests.json',rows)
            for _ in range(40):
                after=snapshot(hosts)
                row['after']=after
                try:
                    a={r:s['values'] for r,s in before.items()};b={r:s['values'] for r,s in after.items()}
                    if route.startswith('pd-'):
                        payload=settings.cache_bytes_per_rank(arch,case['input_tokens'])*4
                        proof=e.transfer_delta(a,b,route[3:],payload)
                    else:
                        if any(b[r][name]!=a[r][name] for r in a for name in (e.COUNT,e.BYTES,*e.FAILURES)):
                            raise ValueError('Direct request caused KV transfer or errors')
                        proof={}
                    row.update(after=after,transfers=proof);break
                except ValueError as error:
                    row['counter_observation_error']=str(error)
                    time.sleep(.25)
            else:raise ValueError('Expected isolated request transfer counters did not settle')
            return row
        except Exception as error:
            row['error']=repr(error);raise
        finally:write(phase_dir/'requests.json',rows)
    try:
        if phase in ('local','remote'):
            reference={}
            if phase=='remote':
                prior=json.loads((out/'local/requests.json').read_text())
                reference={r['case_id']:r['response']['text'].strip() for r in prior if r['route']=='pd-local'}
            for case in suite['cases']:
                body=dict(case['request_body'],ignore_eos=False)
                direct=measured(case,'direct-'+phase,body)
                pd=measured(case,'pd-'+phase,body)
                if direct['response']['text'].strip()!=pd['response']['text'].strip():raise ValueError('Direct/P-D output parity failed')
                if phase=='remote' and reference[case['id']]!=pd['response']['text'].strip():raise ValueError('Local/remote output parity failed')
                print('Qualified '+phase+' '+case['id'],flush=True)
            final=evidence(hosts);write(phase_dir/'final-evidence.json',final)
            stable(base,final);proof=qualify_evidence(final,phase)
            capacities={}
            for host in final:
                for role,log in final[host]['engine_logs'].items():
                    matches=[int(x.replace(',','')) for x in re.findall(r'GPU KV cache size:\s*([\d,]+) tokens',log)]
                    if matches:
                        capacities[host+'-'+role]={'reported_kv_tokens':min(matches),
                            'estimated_max_concurrent_longest_requests':min(config['max_num_seqs'],min(matches)//122944)}
            write(phase_dir/'complete.json',{'requests':len(rows),'rank_transport':proof,'final_evidence':final,
                                           'startup_capacity':capacities,
                                           'gold_matches':sum(r['response']['text'].strip()==next(c['gold_path'] for c in suite['cases'] if c['id']==r['case_id']) for r in rows)})
        elif phase=='timings':
            for role in ['local','remote']:
                if not (out/role/'complete.json').exists():raise ValueError('Both transport qualifications required')
            byid={c['id']:c for c in suite['cases']}
            for length in config['input_tokens']:
                case=next(c for c in suite['cases'] if c['input_tokens']==length)
                for route in ['pd-local','pd-remote']:
                    for _ in range(2):
                        row=measured(case,route,case['request_body']);row['warmup']=True
            for pair in suite['pairs']:
                for role in pair['order']:
                    case=byid[pair['case_id']];row=measured(case,'pd-'+role,case['request_body'])
                    row.update(pair_id=pair['id'],block=pair['block'],warmup=False)
                    write(phase_dir/'requests.json',rows)
                print('Timed '+pair['id'],flush=True)
            final=evidence(hosts);write(phase_dir/'final-evidence.json',final);stable(base,final)
            write(phase_dir/'complete.json',{'timed_requests':sum(not r.get('warmup',False) for r in rows),
                                           'warmups':sum(r.get('warmup',False) for r in rows),'final_evidence':final})
        else:raise ValueError('Unknown phase')
    except Exception as error:
        write(phase_dir/'failure.json',{'error':repr(error),'time':time.time()});raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--local',required=True);p.add_argument('--remote')
    p.add_argument('--suite',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('/results'));p.add_argument('--phase',choices=['local','remote','timings'],required=True)
    p.add_argument('--deadline',type=float,required=True);a=p.parse_args()
    hosts={'local':a.local}
    if a.remote:hosts['remote']=a.remote
    run(hosts,a.suite,a.config,a.out,a.phase,a.deadline)
