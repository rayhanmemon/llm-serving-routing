#!/usr/bin/env python3
"""Independently validate saved single-host real P/D and NVLink evidence."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

SPEC=importlib.util.spec_from_file_location('worker',Path(__file__).with_name('single-host-worker.py'))
worker=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def require(ok,message):
    if not ok:raise ValueError(message)


def parse_counters(text,ids):
    result={};gpu=None
    for line in text.splitlines():
        match=re.search(r'UUID: (GPU-[^)]+)',line)
        if match:
            gpu=match[1];result[gpu]={'Tx':{},'Rx':{}}
        match=re.search(r'Link (\d+): Data (Tx|Rx): (\d+) KiB',line)
        if match:
            require(gpu is not None,'Counter lacks GPU identity')
            link,direction,value=match.groups()
            require(int(link) not in result[gpu][direction],'Duplicate link counter')
            result[gpu][direction][int(link)]=int(value)
    require(set(result)==set(ids),'Unexpected GPU counter identities')
    require(all(set(d[k])==set(range(18)) for d in result.values() for k in ['Tx','Rx']),
            'Expected all 18 NVLink counters on each selected H200')
    return {g:{k:sum(v.values()) for k,v in d.items()} for g,d in result.items()}


def validate(folder):
    read=lambda name:json.loads((folder/name).read_text())
    ids=read('selected-gpus.json');require(len(ids)==2 and len(set(ids))==2,'Two distinct GPUs required')
    topology=(folder/'gpu-topology.stdout').read_text()
    require(re.search(r'^GPU0\s+X\s+NV18',topology,re.M) and
            re.search(r'^GPU1\s+NV18\s+X',topology,re.M),'NV18 physical pairing missing')
    for index,role in enumerate(['producer','consumer']):
        raw=read('raw-'+role+'.json')
        require(raw['ok'] and raw['selected_gpu_uuid']==ids[index],'Raw CUDA control or identity failed')
    transfers=read('nixl-consumer/transfers.json')
    require(len(transfers)==9 and all(x['all_bytes_equal_73'] and x['checksum']==73*x['bytes'] for x in transfers),
            'NIXL byte checks failed')
    require(worker.ipc_read_tables((folder/'nixl-consumer.log').read_text()),'Raw NIXL data lane was not CUDA IPC')
    tables=worker.ipc_read_tables((folder/'decode.log').read_text())
    require(tables,'Real KV READ data lane was not CUDA IPC')
    before=(folder/'compute-processes-before.stdout').read_text().splitlines()
    after=(folder/'compute-processes-after.stdout').read_text().splitlines()
    require(sorted(before)==sorted(after) and all(any(g in line for line in after) for g in ids),
            'GPU processes changed or are missing')
    scope=read('scope.json');require(scope['routes']==['direct','pd'],'Wrong local execution scope')
    raw_plan=(folder/'suite/suite.json').read_bytes()
    require(hashlib.sha256(raw_plan).hexdigest()==(folder/'suite/suite.sha256').read_text().split()[0],
            'Frozen suite hash mismatch')
    plan=json.loads(raw_plan);cases={c['case_id']:c for c in plan['cases']}
    require(plan['model']==scope['model']==worker.MODEL,'Wrong model')
    require(plan['revision']==scope['revision']==worker.REVISION,'Wrong model revision')
    rows=read('responses.json')
    expected={(case,route) for case in cases for route in scope['routes']}
    require(len(cases)==8 and len(rows)==16 and {(x['case_id'],x['route']) for x in rows}==expected,
            'Expected eight cases on each of two routes')
    def delta(name):
        a=parse_counters((folder/(name+'-before.stdout')).read_text(),ids)
        b=parse_counters((folder/(name+'-after.stdout')).read_text(),ids)
        return {g:{k:b[g][k]-a[g][k] for k in ['Tx','Rx']} for g in ids}
    idle=delta('nvlink-idle');require(all(v==0 for d in idle.values() for v in d.values()),'Idle NVLink traffic')
    results=[];texts={};gold=0
    for row in rows:
        case=cases[row['case_id']];pd=row['route']=='pd';a,b=row['before'],row['after'];body=row['response']
        require(row['request']==case['request_body'] and row['status']==200,'Request mismatch or failure')
        require(body['usage']['prompt_tokens']==case['input_tokens'] and
                body['usage']['completion_tokens']==len(case['gold_token_ids']),'Wrong token counts')
        require(b[worker.COUNT]-a[worker.COUNT]==int(pd),'Wrong transfer count')
        require(all(b[x]==a[x] for x in worker.FAILURES),'Transfer error counter changed')
        text=body['choices'][0]['text'].strip();texts.setdefault(row['case_id'],{})[row['route']]=text
        gold+=text==case['gold_text']
        payload=b[worker.SIZE]-a[worker.SIZE]
        change=delta('nvlink-'+row['case_id']+'-'+row['route'])
        if pd:
            # Each of 18 integer-KiB counters can contribute <1 KiB rounding
            # error when differencing two samples. This is not a fitted tolerance.
            require(payload>0 and abs(change[ids[0]]['Tx']-payload/1024)<=18 and
                    abs(change[ids[1]]['Rx']-payload/1024)<=18,'NVLink bytes do not match the KV payload')
            require(change[ids[0]]['Rx']==0 and change[ids[1]]['Tx']==0,'Unexpected reverse NVLink data')
        else:
            require(payload==0 and all(v==0 for d in change.values() for v in d.values()),'Direct control moved peer data')
        results.append({'case':row['case_id'],'route':row['route'],'prompt_tokens':case['input_tokens'],
                        'kv_bytes':payload,'nvlink_prefill_tx_kib':change[ids[0]]['Tx'],
                        'nvlink_decode_rx_kib':change[ids[1]]['Rx']})
    require(all(d['direct']==d['pd'] for d in texts.values()),'Direct/P/D output mismatch')
    pref=read('prefill-metrics.json')
    require(all(pref['before'][x]==pref['after'][x] for x in worker.FAILURES),'Prefill errors changed')
    return {'nvlink_verified':True,'requests':16,'matched_cases':8,'pd_transfers':8,
            'gold_matches':gold,'physical_gpu_uuids':ids,'protocol_tables':tables,
            'idle_delta':idle,'request_evidence':results,'rounding_tolerance_kib':18,
            'scope':'Qwen3-0.6B eager, shared two-GPU container, pinned llm-d sidecar. No remote TTFT or router-policy gain.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();result=validate(args.evidence);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: 16 requests, 8 matching cases, real KV payload verified on NVLink; gold '+str(result['gold_matches'])+'/16')
