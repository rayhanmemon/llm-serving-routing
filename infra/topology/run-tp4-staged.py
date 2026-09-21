#!/usr/bin/env python3
"""Qualify the local TP4 host before admitting the remote host; always clean up."""
import base64
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import shlex
import tarfile
import time

HERE=Path(__file__).resolve().parent
def module(name):
    sp=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
paired=module('run-paired-hosts');tp4=module('tp4-config');pilot=paired.pilot
paired.NS=tp4.NS
PROFILE='tp4-h200-staged'
PROFILES=(PROFILE,'tp4-h200-staged-retry','tp4-h200-health-retry')
CODE=('tp4-config.py','tp4-engines.py','tp4-client.py','tp4-evidence.py','validate-tp4-args.py','run-tp4-staged.py','run-paired-hosts.py','pilot-session.py','cloud-deadline-guard.py')


def validate_stage(original,new,expected):
    if original['variables']!=new['variables'] or original['configuration']!=new['configuration']:
        raise ValueError('Terraform configuration changed after admission')
    changes={r['address']:r['change']['actions'] for r in new['resource_changes'] if r['change']['actions']!=['no-op']}
    if changes!={address:['create'] for address in expected}:
        raise ValueError('Unexpected resource mutation in staged allocation')


def client_manifest(node,suite):
    image='python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b'
    return {'apiVersion':'v1','kind':'List','items':[
        {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'tp4-requests','namespace':tp4.NS},
         'binaryData':{'suite.json.gz':base64.b64encode(suite.read_bytes()).decode()}},
        {'apiVersion':'v1','kind':'Pod','metadata':{'name':'client','namespace':tp4.NS},'spec':{
            'nodeSelector':{'kubernetes.io/hostname':node},'restartPolicy':'Never','automountServiceAccountToken':False,
            'activeDeadlineSeconds':8100,'containers':[{'name':'client','image':image,'command':['sleep','8000'],
            'resources':{'requests':{'cpu':'2','memory':'1Gi'},'limits':{'cpu':'4','memory':'4Gi'}},
            'volumeMounts':[{'name':'code','mountPath':'/probe','readOnly':True},
                            {'name':'data','mountPath':'/data','readOnly':True},{'name':'results','mountPath':'/results'}]}],
            'volumes':[{'name':'code','configMap':{'name':'tp4-code'}},
                       {'name':'data','configMap':{'name':'tp4-requests'}},{'name':'results','emptyDir':{'sizeLimit':'8Gi'}}]}}]}


class Controller(paired.Controller):
    def __init__(self,run,config_path):
        super().__init__(run)
        if self.session['profile'] not in PROFILES:raise ValueError('Requires TP4 staged admission')
        self.config_path=config_path;self.nodes_by_role={};self.ips={}
        self.env.update({'TF_VAR_project_id':self.session['project_id'],
                         'TF_VAR_subnet_id':self.session['subnet_id'],
                         'TF_VAR_gpu_platform':'H200','TF_VAR_infiniband_fabric':self.session['infiniband_fabric'],
                         'TF_VAR_gpu_preemptible':'true','TF_VAR_ipc_diagnostic_only':'false','TF_VAR_cloud_guard':'true'})
        self.original=json.loads(self.call(self.tf+['show','-json',self.session['terraform_plan_path']],'original-full-plan').stdout)

    def allocate(self,role):
        if role=='remote' and not (self.run/'tp4-local-qualified.json').exists():
            raise ValueError('Remote allocation requires saved local qualification')
        if time.time()+1200>self.session['cleanup_start_deadline_unix']:
            raise TimeoutError('Insufficient time for another GPU allocation')
        target='nebius_mk8s_v1_node_group.local' if role=='local' else 'nebius_mk8s_v1_node_group.remote[0]'
        plan=self.run/(role+'.tfplan')
        self.call(self.tf+['plan','-input=false','-target='+target,'-out='+str(plan)],role+'-plan',90)
        data=json.loads(self.call(self.tf+['show','-json',str(plan)],role+'-plan-json').stdout)
        expected={target}
        if role=='local':expected.add('nebius_compute_v1_gpu_cluster.local')
        validate_stage(self.original,data,expected)
        self.session['terraform_plan_path']=str(plan);self.session['terraform_plan_sha256']=pilot.file_sha256(plan)
        pilot.write_json(self.run/'session.json',self.session)
        if pilot.apply_plan(self.run,self.session):raise RuntimeError(role+' GPU placement failed')
        state=json.loads(self.call(self.tf+['show','-json'],role+'-state').stdout)
        group=next(r['values']['id'] for r in state['values']['root_module']['resources'] if r['address']==target)
        self.nodes_by_role.update(self.nodes({role:group}))
        print('Healthy '+role+' GPU host allocated.',flush=True)

    def deploy(self,role):
        doc=tp4.render(self.config_path,self.nodes_by_role,7200)
        # Never reapply the already-running local Pod during remote deployment.
        if role=='remote':doc['items']=[x for x in doc['items'] if x['kind']=='Pod' and x['metadata']['name']=='remote']
        self.apply(doc,role+'-engine-manifest')
        until=min(time.time()+1800,self.session['cleanup_start_deadline_unix']-600)
        while time.time()<until:
            pod=json.loads(self.call(self.k+['-n',tp4.NS,'get','pod',role,'-o','json'],role+'-pod').stdout)
            if pod.get('metadata',{}).get('deletionTimestamp') or pod.get('status',{}).get('phase') in ('Failed','Succeeded'):
                raise RuntimeError('Engine Pod exited/evicted')
            for container in pod.get('status',{}).get('containerStatuses',[]):
                state=container.get('state',{})
                if state.get('terminated') or state.get('waiting',{}).get('reason') in ('ErrImagePull','ImagePullBackOff','CreateContainerConfigError'):
                    raise RuntimeError('Engine container failed: '+str(state))
            ip=pod.get('status',{}).get('podIP')
            if ip:
                ipaddress.ip_address(ip)
                failed=self.call(self.k+['-n',tp4.NS,'exec',role,'-c','engines','--','cat','/results/engine-failure.json'],role+'-failure-check',25,check=False)
                if failed.returncode==0:raise RuntimeError('Engine startup failed: '+failed.stdout)
                ports=[8100,8200,8300] if role=='local' else [8200,8300]
                code='import urllib.request;'+ ';'.join(f'urllib.request.urlopen("http://127.0.0.1:{p}/'+('evidence' if p==8300 else 'v1/models')+'",timeout=5).read()' for p in ports)
                result=self.call(self.k+['-n',tp4.NS,'exec',role,'-c','engines','--','python3','-c',code],role+'-ready',25,check=False)
                if result.returncode==0:
                    self.ips[role]=ip;print(role+' TP4 engines ready.',flush=True);return
            self.sleep(5)
        raise TimeoutError('Large-model readiness deadline reached')

    def phase(self,name):
        args=['python3','-u','/probe/tp4-client.py','--config','/probe/config.json','--suite','/data/suite.json.gz',
              '--local',self.ips['local'],'--phase',name,'--deadline',str(self.session['cleanup_start_deadline_unix'])]
        if 'remote' in self.ips:args+=['--remote',self.ips['remote']]
        script=shlex.join(args)+f'; result=$?; echo "$result" > /results/{name}.exit; exit "$result"'
        launch=f'nohup sh -c {shlex.quote(script)} > /results/{name}.log 2>&1 < /dev/null &'
        self.call(self.k+['-n',tp4.NS,'exec','client','--','sh','-c',launch],name+'-start',25)
        print('Started '+name+' client phase.',flush=True)
        while time.time()+150<self.session['cleanup_start_deadline_unix']:
            result=self.call(self.k+['-n',tp4.NS,'exec','client','--','cat',f'/results/{name}.exit'],name+'-exit',25,check=False)
            if result.returncode==0:
                if result.stdout.strip()!='0':raise RuntimeError(name+' client phase failed; raw evidence retained')
                marker=json.loads(self.call(self.k+['-n',tp4.NS,'exec','client','--','cat',f'/results/{name}/complete.json'],name+'-complete',30).stdout)
                pilot.write_json(self.run/('tp4-'+name+'-qualified.json'),marker)
                print('Completed '+name+' client phase.',flush=True);return
            self.sleep(5)
        raise TimeoutError('Client phase reached collection reserve')

    def execute(self,suite):
        super().bootstrap(allocate_gpus=False)
        cpu=self.cpu_node
        self.allocate('local');self.deploy('local')
        self.apply(client_manifest(cpu,suite),'tp4-client-manifest')
        self.call(self.k+['-n',tp4.NS,'wait','--for=condition=Ready','pod/client','--timeout=180s'],'client-ready',190)
        self.phase('local')
        self.collect()
        self.allocate('remote');self.deploy('remote')
        self.phase('remote');self.collect()
        self.phase('timings');self.collect()

    def collect(self):
        for pod,container in [('local','engines'),('remote','engines'),('client','client')]:
            result=self.call(self.k+['-n',tp4.NS,'exec',pod,'-c',container,'--','tar','czf','-','-C','/results','.'],pod+'-evidence',120,check=False,binary=True)
            if result.returncode==0:
                with tarfile.open(self.out/(pod+'-evidence.tar.gz')) as archive:
                    archive.extractall(self.out/pod,filter='data')
            self.call(self.k+['-n',tp4.NS,'logs',pod,'-c',container],pod+'-log',30,check=False)
        self.call(self.k+['-n',tp4.NS,'get','pods','-o','json'],'final-pods',30,check=False)


def main():
    parser=pilot.parser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--suite',type=Path,required=True)
    parser.add_argument('--preflight-record',type=Path,required=True);args=parser.parse_args()
    if args.profile not in PROFILES:raise ValueError('Wrong profile')
    proof=json.loads(args.preflight_record.read_text())
    if not all(proof.get(k) is True for k in ('native_vllm_parser_passed','full_http_rehearsal_passed','manifests_server_validated','request_configmap_roundtrip','staged_controller_rehearsed')):
        raise ValueError('Full TP4 preflight required before admission')
    for path in [args.config,args.suite,*[HERE/name for name in CODE]]:
        if proof['sha256'].get(path.name)!=hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Preflight source changed: '+path.name)
    run,session=pilot.prepare_session(args);pilot.write_json(run/'tp4-preflight.json',proof)
    print('RUN_DIR='+str(run),flush=True)
    guard=pilot.spawn_guard(run);pilot.wait_guard_ready(run,guard)
    session['guard_pid']=guard.pid;pilot.write_json(run/'session.json',session)
    controller=None
    try:
        controller=Controller(run,args.config);controller.execute(args.suite)
    except Exception as error:
        pilot.write_json(run/'failure.json',{'error':repr(error)})
        if controller is not None:
            try:controller.collect()
            except Exception:pass
        raise
    finally:pilot.cleanup_until_target(run)


if __name__=='__main__':main()
