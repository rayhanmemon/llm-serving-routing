#!/usr/bin/env python3
"""Render real EPP policies for the qualified shared-Pod TP4 serving fixture."""
import argparse
import importlib.util
import ipaddress
import json
from pathlib import Path
import yaml

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('base',HERE/'render.py');b=importlib.util.module_from_spec(sp);sp.loader.exec_module(b)


def prefill_proxy(node,engine_ip,namespace='router-tp4'):
    ipaddress.ip_address(engine_ip)
    labels={'llm-d.ai/guide':'topology-measurement','llm-d.ai/role':'prefill','kubernetes.io/hostname':node}
    cfg={'static_resources':{'listeners':[{'name':'prefill','address':{'socket_address':{'address':'0.0.0.0','port_value':8000}},'filter_chains':[{'filters':[{'name':'envoy.filters.network.http_connection_manager','typed_config':{'@type':'type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager','stat_prefix':'prefill','stream_idle_timeout':'300s','route_config':{'name':'prefill','virtual_hosts':[{'name':'engine','domains':['*'],'routes':[{'match':{'prefix':'/'},'route':{'cluster':'prefill','timeout':'0s'}}]}]},'http_filters':[{'name':'envoy.filters.http.router','typed_config':{'@type':'type.googleapis.com/envoy.extensions.filters.http.router.v3.Router'}}]}}]}]}],'clusters':[{'name':'prefill','connect_timeout':'5s','type':'STATIC','load_assignment':{'cluster_name':'prefill','endpoints':[{'lb_endpoints':[{'endpoint':{'address':{'socket_address':{'address':engine_ip,'port_value':8100}}}}]}]}}]}}
    return [{'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'prefill-http','namespace':namespace},'data':{'envoy.yaml':yaml.safe_dump(cfg,sort_keys=False)}},
            {'apiVersion':'v1','kind':'Pod','metadata':{'name':'prefill-http','namespace':namespace,'labels':labels},'spec':{'nodeSelector':{'kubernetes.io/hostname':node},'automountServiceAccountToken':False,'tolerations':[{'key':'nvidia.com/gpu','operator':'Exists','effect':'NoSchedule'}],'containers':[{'name':'proxy','image':b.PROXY_IMAGE,'args':['-c','/etc/envoy/envoy.yaml','--concurrency','2'],'ports':[{'containerPort':8000}], 'readinessProbe':{'httpGet':{'path':'/v1/models','port':8000},'periodSeconds':3},'resources':{'requests':{'cpu':'100m','memory':'64Mi'},'limits':{'cpu':'1','memory':'256Mi'}},'volumeMounts':[{'name':'config','mountPath':'/etc/envoy','readOnly':True}]}],'volumes':[{'name':'config','configMap':{'name':'prefill-http'}}]}}]


def render(local_node,remote_node,cpu_node,local_ip,out,architecture='amd64',verbosity=2):
    out.mkdir(parents=True,exist_ok=True)
    for name,node in [('local',local_node),('remote',remote_node)]:
        labels={'llm-d.ai/guide':'topology-measurement','llm-d.ai/role':'decode','kubernetes.io/hostname':node}
        (out/f'{name}-labels.json').write_text(json.dumps({'metadata':{'labels':labels}},indent=2)+'\n')
    (out/'prefill-proxy.yaml').write_text(yaml.safe_dump_all(prefill_proxy(local_node,local_ip),sort_keys=False))
    (out/'prefill-envoy.yaml').write_text(prefill_proxy(local_node,local_ip)[0]['data']['envoy.yaml'])
    variants=[('none',0,1,4),('hard',0,1,4),('soft',0,.5,4),('absolute-cap',0,1,4),('allowance',1,1,4)]
    for name,allowance,weight,cap in variants:
        cfg=b.epp_config(name,allowance,weight,cap,True)
        values=b.router_values(cfg,cpu_node)
        values['router']['epp']['flags'].update({'v':verbosity,'emit-endpoint-scores':True})
        values['router']['epp']['image']['tag']='topology-0217d299-'+architecture
        (out/f'router-{name}.values.yaml').write_text(yaml.safe_dump(values,sort_keys=False))
    (out/'manifest.json').write_text(json.dumps({'router_source':'0217d29924ba93b90f952e7a0281dd8dda146703','policies':[v[0] for v in variants],'parameters':'Illustrative values; soft weight/cap/allowance require calibration before held-out evaluation.','namespace':'router-tp4','local_node':local_node,'remote_node':remote_node,'cpu_node':cpu_node,'prefill_upstream':local_ip+':8100','boundary':'One extra prefill HTTP hop, identical across policies; KV path bypasses this proxy. No performance result from rendering.'},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--local-node',required=True);p.add_argument('--remote-node',required=True);p.add_argument('--cpu-node',required=True);p.add_argument('--local-ip',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--architecture',choices=['arm64','amd64'],default='amd64');p.add_argument('--verbosity',type=int,default=2);a=p.parse_args();render(a.local_node,a.remote_node,a.cpu_node,a.local_ip,a.out,a.architecture,a.verbosity)
