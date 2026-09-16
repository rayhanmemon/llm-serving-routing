#!/usr/bin/env python3
"""Run an isolated-pod NIXL READ qualification on one guarded H200 host."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
IMAGE = 'docker.io/vllm/vllm-openai:v0.26.0@sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b'
NS = 'router-rdma-check'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args(); run = a.run_dir.resolve()
    session = json.loads((run / 'session.json').read_text())
    if session['profile'] != 'rdma-h200-local': raise SystemExit('Wrong session profile')
    if not a.execute:
        print('Preview: RDMA-only GPU READ; one producer and one consumer in separate one-GPU Pods.')
        return
    out = run / 'rdma-check'; out.mkdir(exist_ok=False)
    env = {**os.environ, 'KUBECONFIG': str(run / 'kubeconfig'), 'KUBECTL_REMOTE_COMMAND_WEBSOCKETS': 'false'}
    k = ['kubectl', '--context', 'router-topology', '--request-timeout=20s']
    def remaining():
        if (run / 'manual-stop-requested.json').exists(): raise RuntimeError('Manual stop requested')
        left = session['cleanup_start_deadline_unix'] - time.time() - 120
        if left <= 0: raise RuntimeError('Collection reserve reached')
        return left
    def call(cmd, name, timeout=30, data=None, check=True):
        x = subprocess.run(cmd, input=data, capture_output=True, text=True, env=env,
                           timeout=min(timeout, remaining()))
        (out / (name + '.stdout')).write_text(x.stdout); (out / (name + '.stderr')).write_text(x.stderr)
        if check: x.check_returncode()
        return x
    try:
        while not (run / 'apply-result.json').exists(): remaining(); time.sleep(3)
        if json.loads((run / 'apply-result.json').read_text())['exit_code'] != 0:
            raise RuntimeError('Infrastructure did not become ready')
        outputs = json.loads(call(['terraform', '-chdir='+session['terraform_dir'], 'output', '-json'], 'outputs').stdout)
        cluster = outputs['cluster_id']['value']; group = outputs['node_group_ids']['value']['local']
        call([str(Path.home()/'.nebius/bin/nebius'), 'mk8s','cluster','get-credentials','--id',cluster,
              '--external','--kubeconfig',str(run/'kubeconfig'),'--context-name','router-topology','--force'], 'credentials', 60)
        nodes = json.loads(call(k+['get','nodes','-o','json'], 'nodes').stdout)['items']
        node = next(n for n in nodes if n['metadata']['labels'].get('nebius.com/node-group-id') == group)
        assert any(c['type']=='Ready' and c['status']=='True' for c in node['status']['conditions'])
        assert int(node['status']['allocatable'].get('nvidia.com/gpu',0)) == 8
        rdma_resources = {n:v for n,v in node['status']['allocatable'].items() if n.startswith('rdma/')}
        if len(rdma_resources)>1: raise RuntimeError('Multiple RDMA pools require explicit selection: '+str(rdma_resources))
        rdma = next(iter(rdma_resources), None)
        if rdma and int(rdma_resources[rdma])<2: raise RuntimeError('Not enough advertised RDMA grants for two Pods')
        (out/'device-resources.json').write_text(json.dumps({'rdma':rdma_resources,'node':node['metadata']['name']},indent=2))
        source = (HERE/'rdma-read-probe.py').read_text()
        documents = [{'apiVersion':'v1','kind':'Namespace','metadata':{'name':NS}},
                     {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'probe-code','namespace':NS},'data':{'probe.py':source}}]
        for role in ['producer','consumer']:
            resources = {'cpu':'2','memory':'8Gi','nvidia.com/gpu':'1'}
            if rdma: resources[rdma]='1'
            command = ['python3','-u','/probe/probe.py','producer','--lifetime','1800'] if role=='producer' else ['sleep','2400']
            documents.append({'apiVersion':'v1','kind':'Pod','metadata':{'name':'rdma-'+role,'namespace':NS},'spec':{
                'nodeName':node['metadata']['name'],'restartPolicy':'Never','activeDeadlineSeconds':2400,
                'automountServiceAccountToken':False,
                'containers':[{'name':'probe','image':IMAGE,'command':command,
                    'env':[{'name':'UCX_TLS','value':'rc,cuda_copy,self'}, {'name':'UCX_PROTO_INFO','value':'y'},
                           {'name':'UCX_LOG_LEVEL','value':'info'}],
                    'securityContext':{'capabilities':{'add':['IPC_LOCK']}},
                    'resources':{'requests':resources,'limits':resources},
                    'volumeMounts':[{'name':'code','mountPath':'/probe','readOnly':True},
                                    {'name':'results','mountPath':'/results'}, {'name':'shm','mountPath':'/dev/shm'}]}],
                'volumes':[{'name':'code','configMap':{'name':'probe-code'}},{'name':'results','emptyDir':{}},
                           {'name':'shm','emptyDir':{'medium':'Memory','sizeLimit':'2Gi'}}]}})
        manifest={'apiVersion':'v1','kind':'List','items':documents}
        (out/'pods-manifest.json').write_text(json.dumps(manifest,indent=2))
        call(k+['apply','-f','-'], 'apply-pods', 30, json.dumps(manifest))
        deadline = min(time.time()+900, time.time()+remaining())
        while time.time()<deadline:
            items=json.loads(call(k+['-n',NS,'get','pods','-o','json'],'pods').stdout)['items']
            for pod in items:
                for st in pod.get('status',{}).get('containerStatuses',[]):
                    if st.get('state',{}).get('terminated',{}).get('exitCode',0)!=0:
                        raise RuntimeError('Probe container failed: '+pod['metadata']['name'])
            ready=call(k+['-n',NS,'exec','rdma-producer','--','cat','/results/ready'],'ready',15,check=False)
            if ready.returncode==0 and any(pod['metadata']['name']=='rdma-consumer' and
                 any(x.get('ready') for x in pod.get('status',{}).get('containerStatuses',[])) for pod in items):break
            time.sleep(10)
        else:raise RuntimeError('Probe readiness timeout')
        for role in ['producer','consumer']:
            call(k+['-n',NS,'exec','rdma-'+role,'--','bash','-lc',
                    'ulimit -l; grep CapEff /proc/self/status; ls -l /dev/infiniband; ibv_devinfo; nvidia-smi -L'],
                 role+'-hardware',40)
        peer=next(p['status']['podIP'] for p in items if p['metadata']['name']=='rdma-producer')
        call(k+['-n',NS,'exec','rdma-consumer','--','timeout','360','python3','-u','/probe/probe.py',
                'consumer','--peer','http://'+peer+':8123'], 'consumer-read',390)
        for role in ['producer','consumer']:
            call(k+['-n',NS,'logs','rdma-'+role,'--timestamps'], role+'-logs',30)
            x=call(k+['-n',NS,'exec','rdma-'+role,'--','cat','/results/environment.json'],role+'-environment')
        rows=json.loads(call(k+['-n',NS,'exec','rdma-consumer','--','cat','/results/transfers.json'],'transfers').stdout)
        assert len(rows)==9 and all(x['all_bytes_equal_73'] and x['checksum']==x['bytes']*73 for x in rows)
        call(k+['-n',NS,'get','pods','-o','json'],'pods-after')
        (out/'result.json').write_text(json.dumps({'session_id':session['session_id'],'byte_checks_passed':True,
            'transfer_count':9,'ucx_tls':'rc,cuda_copy,self','tcp_payload_transport_enabled':False,
            'requires_protocol_review':True,'finished_unix':time.time()},indent=2))
        print('PASS: nine GPU READs verified; inspect RC protocol selection before attributing transport.',flush=True)
    except Exception as error:
        (out/'failure.json').write_text(json.dumps({'error':str(error),'failed_unix':time.time()},indent=2))
        for role in ['producer','consumer']:
            try:call(k+['-n',NS,'logs','rdma-'+role,'--timestamps'],role+'-failure-logs',20,check=False)
            except Exception:pass
        raise

if __name__=='__main__':main()
