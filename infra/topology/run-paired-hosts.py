#!/usr/bin/env python3
"""Stage cloud cleanup before GPUs, then run a bounded forced-route experiment."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

HERE=Path(__file__).resolve().parent
NS='router-paired'
PROFILE='nvlink-rdma-h200-paired'

def load(name,file):
    s=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
pilot=load('pilot','pilot-session.py');single=load('single','run-single-host.py')
CODE_FILES=('paired-engines.py','paired-client.py','single-host-worker.py','raw-ipc.py','validate-single-host.py')


def configmap(name,data):return {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':name,'namespace':NS},'data':data}
def namespace():return {'apiVersion':'v1','kind':'Namespace','metadata':{'name':NS}}
def listdoc(items):return {'apiVersion':'v1','kind':'List','items':items}


def guard_manifest(node,session,cluster):
    return listdoc([namespace(),configmap('guard-code',{'guard.py':(HERE/'cloud-deadline-guard.py').read_text()}),
        {'apiVersion':'v1','kind':'Pod','metadata':{'name':'deadline-guard','namespace':NS},'spec':{
         'nodeSelector':{'kubernetes.io/hostname':node},'hostNetwork':True,'dnsPolicy':'Default',
         'automountServiceAccountToken':False,'restartPolicy':'Always',
         'containers':[{'name':'guard','image':'python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b',
          'command':['sh','-c','pip install --disable-pip-version-check nebius==0.6.11 && exec python -u /code/guard.py "$@"','guard',
                     '--cluster',cluster,'--project',session['project_id'],'--deadline',str(session['cleanup_start_deadline_unix'])],
          'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'memory':'512Mi'}},
          'volumeMounts':[{'name':'code','mountPath':'/code','readOnly':True},{'name':'results','mountPath':'/results'}]}],
         'volumes':[{'name':'code','configMap':{'name':'guard-code'}},{'name':'results','emptyDir':{}}]}}])


def guard_acknowledgement(log):
    for line in reversed(log.splitlines()):
        try:
            value=json.loads(line)
        except (ValueError,TypeError):
            continue
        if isinstance(value,dict) and value.get('armed') is True:
            return value
    return None


def engine_manifest(nodes):
    data={n:(HERE/n).read_text() for n in CODE_FILES}
    items=[namespace(),configmap('experiment-code',data)]
    for role in ('local','remote'):
        pod=single.manifest(nodes[role])['items'][-1]
        pod['metadata']={'name':role,'namespace':NS}
        spec=pod['spec'];spec['activeDeadlineSeconds']=3600
        engine=spec['containers'][0];engine['command']=['python3','-u','/probe/paired-engines.py']
        engine['env']=[{'name':'ENGINE_ROLE','value':role},{'name':'POD_IP','valueFrom':{'fieldRef':{'fieldPath':'status.podIP'}}}]
        n='2' if role=='local' else '1'
        for key in ('requests','limits'):engine['resources'][key]['nvidia.com/gpu']=n
        spec['volumes'][0]['configMap']['name']='experiment-code'
        items.append(pod)
    return listdoc(items)


def client_manifest(node,config,suite,deadline):
    return listdoc([configmap('request-data',{'config.json':json.dumps(config),'suite.json':suite.read_text()}),
        {'apiVersion':'v1','kind':'Pod','metadata':{'name':'client','namespace':NS},'spec':{
         'nodeSelector':{'kubernetes.io/hostname':node},'restartPolicy':'Never','automountServiceAccountToken':False,
         'containers':[{'name':'client','image':'python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b',
          'command':['sh','-c','python -u /probe/paired-client.py --config /data/config.json --suite /data/suite.json --deadline "$1"; touch /results/DONE; sleep 7200','client',str(deadline)],
          'resources':{'requests':{'cpu':'2','memory':'1Gi'},'limits':{'cpu':'4','memory':'4Gi'}},
          'volumeMounts':[{'name':'code','mountPath':'/probe','readOnly':True},{'name':'data','mountPath':'/data','readOnly':True},{'name':'results','mountPath':'/results'}]}],
         'volumes':[{'name':'code','configMap':{'name':'experiment-code'}},{'name':'data','configMap':{'name':'request-data'}},{'name':'results','emptyDir':{}}]}}])


def validate_remaining(original,new):
    original_by={x['address']:x for x in original['resource_changes']}
    creates=[]
    for item in new['resource_changes']:
        action=item['change']['actions']
        if action==['no-op']:continue
        if action!=['create'] or item['address'] not in original_by:raise ValueError('Unexpected remaining infrastructure mutation')
        creates.append(item['address'])
    expected={'nebius_compute_v1_gpu_cluster.local','nebius_mk8s_v1_node_group.local','nebius_mk8s_v1_node_group.remote[0]'}
    if set(creates)!=expected:raise ValueError('Remaining plan is not exactly two GPU groups and fabric')
    if new['variables']!=original['variables']:raise ValueError('Plan variables changed after guard bootstrap')
    if new['configuration']!=original['configuration']:raise ValueError('Terraform configuration changed after admission')


class Controller(single.Controller):
    def __init__(self,run):
        self.run=run;self.session=pilot.read_json(run/'session.json')
        if self.session['profile'] not in pilot.PAIRED_PROFILES:raise ValueError('Wrong admitted profile')
        self.out=run/'paired';self.out.mkdir();self.rpc=subprocess.run;self.sleep=time.sleep
        self.env={**os.environ,'KUBECONFIG':str(run/'kubeconfig'),'KUBECTL_REMOTE_COMMAND_WEBSOCKETS':'false'}
        self.k=['kubectl','--context','router-topology','--request-timeout=20s']
        self.tf=['terraform','-chdir='+self.session['terraform_dir']]
    def call(self,cmd,name,*args,**kwargs):
        # Bootstrap/readiness RPC failures must not discard a healthy paid run.
        # Only these known read-only commands may be replayed; never an apply.
        retryable = cmd[0]=='kubectl' and (
            'get' in cmd or 'logs' in cmd or name in
            ('local-ready','remote-ready','client-done','local-evidence','remote-evidence','client-evidence'))
        for attempt in range(3):
            try:
                return super().call(cmd,name,*args,**kwargs)
            except subprocess.TimeoutExpired:
                if not retryable or attempt==2:raise
                self.sleep(2)
    def apply(self,doc,name):
        pilot.write_json(self.out/(name+'.json'),doc)
        return self.call(self.k+['apply','--server-side','-f','-'],name,60,json.dumps(doc))
    def nodes(self,groups):
        deadline=min(time.time()+600,self.session['cleanup_start_deadline_unix']-600)
        while time.time()<deadline:
            items=json.loads(self.call(self.k+['get','nodes','-o','json'],'nodes').stdout)['items'];found={}
            for role,group in groups.items():
                matches=[x for x in items if x['metadata']['labels'].get('nebius.com/node-group-id')==group]
                if len(matches)!=1:continue
                n=matches[0];conditions={x['type']:x for x in n['status']['conditions']}
                if any(conditions.get(t,{}).get('status')=='True' for t in ('NebiusGPUError','NebiusContainerRuntimeError')):raise ValueError('Provider node health failure')
                if conditions.get('Ready',{}).get('status')!='True' or n.get('spec',{}).get('unschedulable'):continue
                if role!='cpu' and (int(n['status'].get('allocatable',{}).get('nvidia.com/gpu',0))!=8 or conditions.get('NebiusGPUError',{}).get('status')!='False'):continue
                found[role]=n['metadata']['labels']['kubernetes.io/hostname']
            if len(found)==len(groups):return found
            self.sleep(4)
        raise TimeoutError('Healthy nodes did not become ready')
    def bootstrap(self,allocate_gpus=True):
        # Only CPU, control plane and temporary IAM resources. No GPU allocation.
        self.call(self.tf+['apply','-auto-approve','-input=false','-target=nebius_mk8s_v1_node_group.cpu'], 'cpu-bootstrap',600)
        state=json.loads(self.call(self.tf+['show','-json'],'bootstrap-state').stdout)
        resources={x['address']:x['values'] for x in state['values']['root_module']['resources']}
        cluster=resources['nebius_mk8s_v1_cluster.topology']['id'];cpu=resources['nebius_mk8s_v1_node_group.cpu[0]']['id']
        self.cluster=cluster
        self.call([str(Path.home()/'.nebius/bin/nebius'),'mk8s','cluster','get-credentials','--id',cluster,'--external','--kubeconfig',str(self.run/'kubeconfig'),'--context-name','router-topology','--force'],'credentials',60)
        node=self.nodes({'cpu':cpu})['cpu']
        self.cpu_node=node
        # Remove drain blockers from this isolated experiment before guard arming.
        self.call([sys.executable,str(HERE/'prepare-teardown.py'),'--terraform-dir',self.session['terraform_dir']],'drain-preparation',90)
        self.apply(guard_manifest(node,self.session,cluster),'guard-manifest')
        until=time.time()+180
        while time.time()<until:
            try:
                r=self.call(self.k+['-n',NS,'logs','deadline-guard','--tail=10'],'cloud-guard-readiness',25,check=False)
            except subprocess.TimeoutExpired:
                self.sleep(3)
                continue
            ready=guard_acknowledgement(r.stdout) if r.returncode==0 else None
            if ready is not None:
                if ready.get('armed') is not True or ready['cluster']!=cluster or ready['deadline']!=self.session['cleanup_start_deadline_unix'] or ready['gpu_groups_present_at_arm']:raise ValueError('Invalid cloud guard acknowledgement')
                pilot.write_json(self.run/'cloud-guard-ready.json',ready);print('Cloud guard armed before GPU allocation.',flush=True);break
            self.sleep(3)
        else:raise TimeoutError('Cloud guard did not arm; no GPUs will be allocated')
        if not allocate_gpus:
            return
        if time.time()+1200>self.session['cleanup_start_deadline_unix']:raise TimeoutError('Too little work time remains for GPU admission')
        path=self.run/'gpu.tfplan'
        self.call(self.tf+['plan','-input=false','-out='+str(path)],'gpu-plan',90)
        original=json.loads(self.call(self.tf+['show','-json',self.session['terraform_plan_path']],'original-plan').stdout)
        new=json.loads(self.call(self.tf+['show','-json',str(path)],'remaining-plan').stdout)
        validate_remaining(original,new)
        self.session['terraform_plan_path']=str(path);self.session['terraform_plan_sha256']=pilot.file_sha256(path)
        pilot.write_json(self.run/'session.json',self.session)
        if pilot.apply_plan(self.run,self.session):raise RuntimeError('GPU placement failed')
    def collect(self):
        for pod,container in [('local','engines'),('remote','engines'),('client','client')]:
            r=self.call(self.k+['-n',NS,'exec',pod,'-c',container,'--','tar','czf','-','-C','/results','.'],pod+'-evidence',100,check=False,binary=True)
            if r.returncode==0:
                with tarfile.open(self.out/(pod+'-evidence.tar.gz')) as t:t.extractall(self.out/pod,filter='data')
            self.call(self.k+['-n',NS,'logs',pod,'-c',container],pod+'-log',30,check=False)
        self.call(self.k+['-n',NS,'logs','deadline-guard'],'cloud-guard-log',30,check=False)
        self.call(self.k+['-n',NS,'get','pods','-o','json'],'final-pods',30,check=False)
    def execute(self,suite):
        self.bootstrap()
        values=json.loads(self.call(self.tf+['output','-json'],'outputs').stdout)
        groups=values['node_group_ids']['value'];nodes=self.nodes(groups)
        self.apply(engine_manifest(nodes),'engine-manifest')
        until=min(time.time()+900,self.session['cleanup_start_deadline_unix']-600)
        while time.time()<until:
            inventory=json.loads(self.call(self.k+['-n',NS,'get','pods','-o','json'],'pods').stdout)['items']
            pods={x['metadata']['name']:x for x in inventory};ips={}
            for role in ('local','remote'):
                pod=pods.get(role,{})
                if pod.get('metadata',{}).get('deletionTimestamp'):raise RuntimeError('Engine Pod evicted')
                if any(x.get('state',{}).get('terminated') or x.get('state',{}).get('waiting',{}).get('reason') in ('ImagePullBackOff','ErrImagePull') for x in pod.get('status',{}).get('containerStatuses',[])):raise RuntimeError('Engine failed')
                ip=pod.get('status',{}).get('podIP')
                if not ip:continue
                ports=(8100,8200) if role=='local' else (8200,)
                check='import urllib.request;'+ ';'.join(f'urllib.request.urlopen("http://127.0.0.1:{port}/v1/models",timeout=2).read()' for port in ports)
                r=self.call(self.k+['-n',NS,'exec',role,'-c','engines','--','python3','-c',check],role+'-ready',10,check=False)
                if not r.returncode:ips[role]=ip
            if len(ips)==2:break
            self.sleep(5)
        else:raise TimeoutError('Engines did not become ready')
        config={'prefill':ips['local']+':8100','endpoints':{route:'http://'+ips[route.split('-')[1]]+(':'+('8000' if route.startswith('pd-') else '8200')) for route in ('direct-local','direct-remote','pd-local','pd-remote')},'metrics':{'prefill':'http://'+ips['local']+':8100/metrics',**{r:'http://'+ip+':8200/metrics' for r,ip in ips.items()}},'evidence':{r:'http://'+ip+':8300/evidence' for r,ip in ips.items()}}
        self.apply(client_manifest(nodes['cpu'],config,suite,self.session['cleanup_start_deadline_unix']),'client-manifest')
        print('Engines ready; qualification client started.',flush=True)
        while True:
            r=self.call(self.k+['-n',NS,'exec','client','--','test','-f','/results/DONE'],'client-done',15,check=False)
            if r.returncode==0:break
            if time.time()+200>=self.session['cleanup_start_deadline_unix']:break
            self.sleep(10)
        self.collect()


def main():
    p=pilot.parser();p.add_argument('--suite',type=Path,required=True);p.add_argument('--preflight-record',type=Path,required=True);a=p.parse_args()
    if a.profile not in pilot.PAIRED_PROFILES:raise ValueError('This runner only accepts the paired profile')
    preflight=json.loads(a.preflight_record.read_text())
    if not all(preflight.get(k) is True for k in ('server_side_full_configmap_stored','data_unchanged','all_three_actual_manifests_pass_server_validation','full_http_workflow_passed')):
        raise ValueError('Real API and full HTTP preflight required before rental')
    if preflight.get('suite_sha256')!=hashlib.sha256(a.suite.read_bytes()).hexdigest():
        raise ValueError('Preflight did not validate this frozen suite')
    for name in CODE_FILES:
        if preflight['code_sha256'].get(name)!=hashlib.sha256((HERE/name).read_bytes()).hexdigest():
            raise ValueError('Preflight source changed: '+name)
    run,session=pilot.prepare_session(a)
    pilot.write_json(run/'preflight.json',preflight)
    print('RUN_DIR='+str(run),flush=True)
    guard=pilot.spawn_guard(run);pilot.wait_guard_ready(run,guard)
    session['guard_pid']=guard.pid;pilot.write_json(run/'session.json',session)
    controller=Controller(run)
    try:controller.execute(a.suite)
    except Exception as e:
        pilot.write_json(run/'failure.json',{'error':repr(e)})
        try:controller.collect()
        except Exception:pass
        raise
    finally:pilot.cleanup_until_target(run)


if __name__=='__main__':main()
