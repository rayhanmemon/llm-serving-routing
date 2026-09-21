#!/usr/bin/env python3
"""Deploy one shared-container local qualification; never creates cloud resources.

A separately admitted pilot-session owns the deadline and teardown. This runner
saves evidence and returns for review; it does not destroy a usable VM merely
because a local report check failed. The caller must clean up after review.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
IMAGE = 'docker.io/vllm/vllm-openai:v0.26.0@sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b'
SIDECAR = 'ghcr.io/llm-d/llm-d-router-disagg-sidecar:v0.10.0@sha256:1344adff65f96f76fc00c6003a1c63c1e9bb70f3628307f61f748a89a265b260'
NS = 'router-local-check'


def manifest(node):
    data = {name:(HERE/name).read_text() for name in
            ('single-host-worker.py','raw-ipc.py','rdma-read-probe.py','known-answer-suite.py')}
    return {'apiVersion':'v1','kind':'List','items':[
        {'apiVersion':'v1','kind':'Namespace','metadata':{'name':NS}},
        {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'local-code','namespace':NS},'data':data},
        {'apiVersion':'v1','kind':'Pod','metadata':{'name':'local-check','namespace':NS},'spec':{
            'nodeSelector':{'kubernetes.io/hostname':node},'restartPolicy':'Never',
            'activeDeadlineSeconds':4200,'automountServiceAccountToken':False,
            'containers':[
                {'name':'engines','image':IMAGE,
                 'command':['python3','-u','/probe/single-host-worker.py','--execute'],
                 'securityContext':{'capabilities':{'add':['IPC_LOCK']}},
                 'resources':{'requests':{'nvidia.com/gpu':'2','cpu':'12','memory':'64Gi'},
                              'limits':{'nvidia.com/gpu':'2','cpu':'24','memory':'96Gi'}},
                 'volumeMounts':[{'name':'code','mountPath':'/probe','readOnly':True},
                                {'name':'results','mountPath':'/results'},
                                {'name':'cache','mountPath':'/cache'},
                                {'name':'shm','mountPath':'/dev/shm'}]},
                {'name':'routing-proxy','image':SIDECAR,
                 'args':['--port=8000','--model-server-port=8200','--kv-connector=nixlv2',
                         '--secure-proxy=false','--zap-log-level=4'],
                 'securityContext':{'allowPrivilegeEscalation':False,'runAsNonRoot':True},
                 'resources':{'requests':{'cpu':'250m','memory':'256Mi'},'limits':{'memory':'1Gi'}}}],
            'volumes':[{'name':'code','configMap':{'name':'local-code'}},
                       {'name':'results','emptyDir':{'sizeLimit':'8Gi'}},
                       {'name':'cache','emptyDir':{}},
                       {'name':'shm','emptyDir':{'medium':'Memory','sizeLimit':'16Gi'}}]}}]}


class Controller:
    def __init__(self, run, rpc=subprocess.run, sleeper=time.sleep, resume=False):
        self.run = run
        self.session = json.loads((run/'session.json').read_text())
        if self.session['profile'] != 'nvlink-h200-local':
            raise ValueError('Requires the admitted single-host profile')
        self.out = run/'single-host'
        self.out.mkdir(exist_ok=resume)
        self.resume = resume
        self.rpc, self.sleep = rpc, sleeper
        self.env = {**os.environ,'KUBECONFIG':str(run/'kubeconfig'),
                    'KUBECTL_REMOTE_COMMAND_WEBSOCKETS':'false'}
        self.k = ['kubectl','--context','router-topology','--request-timeout=20s']

    def call(self, cmd, name, timeout=30, data=None, check=True, binary=False):
        left = self.session['cleanup_start_deadline_unix']-time.time()-120
        if left <= 0 or (self.run/'manual-stop-requested.json').exists():
            raise TimeoutError('Collection reserve or manual stop reached')
        for attempt in range(3):
            result = self.rpc(cmd,input=data,capture_output=True,text=not binary,env=self.env,
                              timeout=min(timeout,left))
            transient = any(message in str(result.stderr).lower() for message in
                            ('unexpected eof', 'connection reset', 'context deadline exceeded'))
            if not (check and result.returncode and not binary and
                    ('get' in cmd or 'logs' in cmd) and transient and attempt < 2):
                break
            (self.out/(name+f'.retry{attempt+1}.stderr')).write_text(result.stderr)
            self.sleep(attempt+1)
        if binary:
            (self.out/(name+'.tar.gz')).write_bytes(result.stdout)
            (self.out/(name+'.stderr')).write_bytes(result.stderr)
        else:
            (self.out/(name+'.stdout')).write_text(result.stdout)
            (self.out/(name+'.stderr')).write_text(result.stderr)
        if check:
            result.check_returncode()
        return result

    def execute(self):
        if self.resume:
            saved = json.loads((self.out/'manifest.json').read_text())
            node = saved['items'][-1]['spec']['nodeSelector']['kubernetes.io/hostname']
            if saved != manifest(node):
                raise RuntimeError('Resume rejected: deployed worker manifest differs from current source')
            return self.monitor()
        while not (self.run/'apply-result.json').exists():
            if time.time()+180 >= self.session['cleanup_start_deadline_unix']:
                raise TimeoutError('Provisioning exhausted work window')
            self.sleep(3)
        if json.loads((self.run/'apply-result.json').read_text())['exit_code']:
            raise RuntimeError('Infrastructure failed')
        values=json.loads(self.call(['terraform','-chdir='+self.session['terraform_dir'],'output','-json'],'outputs').stdout)
        cluster=values['cluster_id']['value'];groups=values['node_group_ids']['value']
        if groups.get('remote') or groups.get('cpu'):
            raise RuntimeError('Unexpected additional node group')
        self.call([str(Path.home()/'.nebius/bin/nebius'),'mk8s','cluster','get-credentials',
                   '--id',cluster,'--external','--kubeconfig',str(self.run/'kubeconfig'),
                   '--context-name','router-topology','--force'],'credentials',60)
        while True:
            items=json.loads(self.call(self.k+['get','nodes','-o','json'],'nodes').stdout)['items']
            matches=[n for n in items if n['metadata']['labels'].get('nebius.com/node-group-id')==groups['local']]
            if len(matches)==1:
                node=matches[0]
                conditions = {c['type']: c for c in node['status']['conditions']}
                health = conditions.get('NebiusGPUError', {})
                bad = [c for c in conditions.values() if c['type'] in ('NebiusGPUError', 'NebiusContainerRuntimeError') and c.get('status') == 'True']
                if bad:
                    raise RuntimeError('Provider node health failed: ' + json.dumps(bad))
                if (int(node['status'].get('allocatable',{}).get('nvidia.com/gpu',0)) == 8
                        and conditions.get('Ready', {}).get('status') == 'True'
                        and health.get('status') == 'False'
                        and not node.get('spec', {}).get('unschedulable')):
                    break
            self.sleep(5)
        rendered=manifest(node['metadata']['labels']['kubernetes.io/hostname'])
        (self.out/'manifest.json').write_text(json.dumps(rendered,indent=2))
        self.call(self.k+['apply','-f','-'],'apply-pod',45,json.dumps(rendered))
        return self.monitor()

    def monitor(self):
        previous=None
        while True:
            result=self.call(self.k+['-n',NS,'exec','local-check','-c','engines','--',
                                    'cat','/results/status.json'],'status',20,check=False)
            if result.returncode==0:
                status=json.loads(result.stdout)
                if status['phase']!=previous:
                    print(status['phase'],flush=True)
                    previous=status['phase']
            done=self.call(self.k+['-n',NS,'exec','local-check','-c','engines','--',
                                  'test','-f','/results/DONE'],'done',20,check=False)
            if done.returncode==0:
                break
            pods=json.loads(self.call(self.k+['-n',NS,'get','pod','local-check','-o','json'],'pod').stdout)
            if pods.get('metadata', {}).get('deletionTimestamp'):
                raise RuntimeError('Test Pod was evicted or deleted before evidence collection')
            for item in pods.get('status',{}).get('containerStatuses',[]):
                if (item.get('state',{}).get('terminated') or item.get('state',{}).get('waiting',{}).get('reason') in ('ImagePullBackOff','ErrImagePull','CreateContainerConfigError')):
                    raise RuntimeError('Container exited before collection')
            self.sleep(5)
        self.call(self.k+['-n',NS,'exec','local-check','-c','engines','--',
                         'tar','czf','-','-C','/results','.'],'evidence',120,binary=True)
        extracted=self.out/'evidence'
        with tarfile.open(self.out/'evidence.tar.gz') as archive:
            archive.extractall(extracted,filter='data')
        self.call(self.k+['-n',NS,'get','pod','local-check','-o','json'],'pod-final')
        self.call(self.k+['-n',NS,'logs','local-check','-c','routing-proxy'],'sidecar-log')
        result={'saved_off_box':True,'archive_sha256':hashlib.sha256((self.out/'evidence.tar.gz').read_bytes()).hexdigest(),
                'worker_succeeded':(extracted/'worker-result.json').exists(),
                'requires_nvlink_counter_review':True,'cloud_cleanup_required':True}
        (self.out/'collection.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
        return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--resume',action='store_true',help='Reconnect to the unchanged existing Pod; no apply')
    a=p.parse_args()
    if not a.execute:
        print('Preview: one GPU host, two processes sharing two GPUs; no cloud create.')
        return
    controller=Controller(a.run_dir,resume=a.resume)
    try:
        controller.execute()
    except Exception as e:
        (controller.out/'controller-failure.json').write_text(json.dumps({'error':repr(e)})+'\n')
        raise


if __name__=='__main__':
    main()
