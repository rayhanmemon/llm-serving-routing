#!/usr/bin/env python3
"""Six real direct/P-D checks per host in the one selected packed layout."""
import argparse,gzip,importlib.util,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('client',HERE/'tp4-client.py');c=importlib.util.module_from_spec(s);s.loader.exec_module(c)

def geometry(record):
    for host,proof in record.items():
        expected={(role,rank) for role in (('prefill','decode') if host=='local' else ('decode',)) for rank in range(4)}
        probes=list(proof.get('layout_probes',{}).values())
        if {(p['role'],p['rank']) for p in probes}!=expected:raise ValueError('Missing live rank allocation')
        for p in probes:
            if (not p['use_v2'] or p['layout']!='BHLNC' or p['stride']!=[2097152,1048576,256,1]
                or p['layer_count']!=64 or p['shared_storage_count']!=1 or p['kernel_block_sizes']!=[64]
                or p['attention_backends']!=['FLASH_ATTN'] or p['shape']!=[p['num_blocks'],2,64,256]
                or p['element_bytes']!=2 or p['storage_bytes']!=p['num_blocks']*4194304):
                raise ValueError('Unexpected live packed cache geometry')

def qualify(config,suite,hosts,out,role,deadline):
    cfg,_=c.settings.load_config(config)
    if cfg.get('kv_cache_layout')!='BHLNC' or cfg.get('vllm_version')!='0.29.0':raise ValueError('Wrong serving configuration')
    geometry(c.evidence(hosts))
    c.run(hosts,suite,config,out,role,deadline)
    marker=json.loads((out/role/'complete.json').read_text())
    if marker['requests']!=6:raise ValueError('Wrong qualification count')
    geometry(marker['final_evidence'])
    marker['rank_transport']=c.qualify_evidence(marker['final_evidence'],role,minimum_descriptor_bytes=4194304)
    rows=json.loads((out/role/'requests.json').read_text())
    for row in rows:
        if not row['route'].startswith('pd-'):continue
        a=row['before'][role]['values'];b=row['after'][role]['values']
        n='vllm:nixl_num_descriptors';count=b[n+'_count']-a[n+'_count']
        if count!=4 or (b[n+'_sum']-a[n+'_sum'])/count > row['input_tokens']//64:
            raise ValueError('Packed transfer still has excessive or unobserved descriptors')
    marker.update(schema_version=1,layout='BHLNC',validated=True)
    c.write(out/role/'complete.json',marker)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--suite',type=Path,required=True)
    p.add_argument('--local',required=True);p.add_argument('--remote');p.add_argument('--out',type=Path,default=Path('/results/qualification'))
    p.add_argument('--role',choices=['local','remote'],required=True);p.add_argument('--deadline',type=float,required=True);a=p.parse_args()
    hosts={'local':a.local}
    if a.remote:hosts['remote']=a.remote
    qualify(a.config,a.suite,hosts,a.out,a.role,a.deadline)
