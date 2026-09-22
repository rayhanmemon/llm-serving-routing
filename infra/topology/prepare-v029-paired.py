#!/usr/bin/env python3
"""Prepare a small matched remote screen only from a qualified local GPU epoch."""
import argparse,gzip,hashlib,json,random,shutil
from pathlib import Path

KEYS=('model','revision','tensor_parallel_size','dtype','kv_cache_dtype','max_model_len','rope_scaling','block_size','max_num_seqs','max_num_batched_tokens','gpu_memory_utilization','enable_prefix_caching','enforce_eager','vllm_version','vllm_image','model_runner','kv_cache_layout')

def prepare(run,epoch,out):
    session=json.loads((run/'session.json').read_text())
    if session['profile']!='tp4-h200-layout-local':raise ValueError('Expected qualified local-layout run')
    client=run/'paired/client'/epoch;engine=run/'paired/local'/epoch
    complete=json.loads((client/'complete.json').read_text())
    if complete['requests']!=26 or len(complete['parity'])!=4 or not all(x['matches'] for x in complete['parity']):raise ValueError('Local functional qualification incomplete')
    if set(complete['transport'])!={'0','1','2','3'}:raise ValueError('Missing rank transport proof')
    cfg=json.loads((engine/'effective-config.json').read_text())
    if cfg.get('vllm_version')!='0.29.0' or cfg.get('model_runner')!='V2':raise ValueError('Wrong serving version/runner')
    final=json.loads((client/'final-evidence.json').read_text())['local']
    probes=list(final['layout_probes'].values())
    if len(probes)!=8 or any(not x['use_v2'] or x['layout']!=cfg['kv_cache_layout'] for x in probes):raise ValueError('Live layout proof mismatch')
    base=Path(__file__).resolve().parents[2]/'workloads/tp4-v029'
    arch=(base/'model-config.json').read_bytes()
    if hashlib.sha256(arch).hexdigest()!=cfg['model_config_sha256']:raise ValueError('Model architecture mismatch')
    out.mkdir(parents=True,exist_ok=False)
    cfg['input_tokens']=[4096,122880];cfg['status']='paired smoke preparation; no remote result'
    config_bytes=(json.dumps(cfg,indent=2)+'\n').encode();(out/'config.json').write_bytes(config_bytes);(out/'model-config.json').write_bytes(arch)
    suite=json.loads(gzip.decompress((base/'suite.json.gz').read_bytes()));suite['cases']=[c for c in suite['cases'] if c['input_tokens'] in cfg['input_tokens']]
    pairs=[];rng=random.Random(22092026)
    for block in (0,1):
        rows=[]
        for length in cfg['input_tokens']:
            cases=[c for c in suite['cases'] if c['input_tokens']==length]
            for index,case in enumerate(cases):
                order=['local','remote'] if (index+block)%2==0 else ['remote','local']
                rows.append({'id':f'smoke-{block}-{length}-{index}','block':block,'case_id':case['id'],'order':order})
        rng.shuffle(rows);pairs.extend(rows)
    suite['pairs']=pairs;suite['config_sha256']=hashlib.sha256(config_bytes).hexdigest()
    (out/'suite.json.gz').write_bytes(gzip.compress(json.dumps(suite,separators=(',',':')).encode(),mtime=0))
    shutil.copy(base/'SOURCE-LICENSE',out/'SOURCE-LICENSE')
    proof={'local_run':session['session_id'],'local_epoch':epoch,'serving':{k:cfg[k] for k in KEYS},
           'qualification_sha256':hashlib.sha256((client/'complete.json').read_bytes()).hexdigest(),
           'local_gpu_qualified':True,'remote_gpu_qualified':False,'timing_pairs':8,'timed_requests':16,
           'warmups':8,'qualification_requests':16,'capacity_requirement':'Two eight-H200 VMs on us-central1-a',
           'budget':'A separately prepared and admitted remaining-budget profile is required; this command spends nothing.'}
    (out/'local-proof.json').write_text(json.dumps(proof,indent=2)+'\n');return proof

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--epoch',choices=['default-a','packed-doc','packed','default-b'],required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.run,a.epoch,a.out),indent=2))
