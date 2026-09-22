#!/usr/bin/env python3
"""Bounded one-host default/packed/default experiment with reusable cleanup identity."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('staged',HERE/'run-tp4-staged.py')
s=importlib.util.module_from_spec(sp);sp.loader.exec_module(s)
pilot=s.pilot
s.PROFILES=(*s.PROFILES,pilot.LAYOUT_PROFILE)
CODE=(*s.CODE,'layout-client.py','layout-engines.py','run-layout-local.py','v029_worker_probe.py',
      'run-single-host.py','prepare-teardown.py','teardown.sh','verify-empty.sh')


def validate_result(marker,mode):
    requests,pairs,timings=(4,2,0) if mode=='qualification' else (26,4,12)
    if (marker.get('mode')!=mode or marker.get('requests')!=requests
            or marker.get('timed_requests')!=timings or len(marker.get('parity',[]))!=pairs
            or not all(x.get('matches') is True for x in marker['parity']) or not marker.get('transport')):
        raise ValueError('Incomplete or wrong-mode epoch result')


def validate_qualification(run,config,suite):
    """Require collected GPU qualification, not a local rehearsal marker."""
    proof=pilot.read_json(run/'layout-preflight.json')
    if pilot.read_json(run/'session.json')['profile']!=pilot.LAYOUT_PROFILE:
        raise ValueError('Wrong qualification session')
    marker=pilot.read_json(run/'qualification-complete.json')
    if marker.get('epochs')!=['default-a','packed-doc','packed','default-b']:
        raise ValueError('Incomplete restart qualification')
    for path in [config,suite,*[HERE/n for n in CODE]]:
        if proof['sha256'].get(path.name)!=hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Qualification source changed: '+path.name)
    for epoch in marker['epochs']:
        result=pilot.read_json(run/'paired/client'/epoch/'complete.json')
        validate_result(result,'qualification')
        rows=json.loads((run/'paired/client'/epoch/'requests.json').read_text())
        if len(rows)!=4 or not all('response' in row and 'after' in row for row in rows):
            raise ValueError('Missing qualification request evidence')
        if not pilot.read_json(run/(epoch+'-shutdown.json')).get('drained'):
            raise ValueError('Unverified worker shutdown')


def select_capacity(data, newer_than=None, minimum=1):
    """Require positive advice for an eight-GPU VM, not eight single-GPU VMs."""
    for item in data.get('items',[]):
        spec=item.get('spec',{});machine=spec.get('compute_instance',{})
        if (spec.get('region')!='us-central1' or spec.get('fabric')!='us-central1-a'
                or machine.get('platform')!='gpu-h200-sxm'
                or machine.get('preset',{}).get('name')!='8gpu-128vcpu-1600gb'):
            continue
        status=item.get('status',{}).get('preemptible',{})
        if status.get('data_state')!='DATA_STATE_FRESH' or int(status.get('available',0))<minimum:
            continue
        effective=datetime.fromisoformat(status['effective_at'].replace('Z','+00:00'))
        if newer_than and effective<=datetime.fromisoformat(newer_than.replace('Z','+00:00')):
            continue
        return item
    raise ValueError('No fresh positive eight-H200 capacity advice; no infrastructure created')


def verify_capacity(runner=subprocess.run,minimum=1):
    state_path=pilot.STATE_ROOT/'overnight-capacity-state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    not_before=state.get('next_identical_attempt_not_before')
    if not_before and datetime.now(timezone.utc)<datetime.fromisoformat(not_before.replace('Z','+00:00')):
        raise ValueError('Capacity backoff remains active; no infrastructure created')
    result=runner([str(Path.home()/'.nebius/bin/nebius'),'capacity','resource-advice','list',
        '--parent-id','tenant-e00evgkv9j4px9vymy','--all','--format','json','--no-check-update','--timeout','20s'],
        capture_output=True,text=True,timeout=30,check=True)
    selected=select_capacity(json.loads(result.stdout),state.get('last_failed_gpu_create_finished_at'),minimum)
    return {'checked_unix':time.time(),'selected':selected,'reservation':False}


def verify_cleanup_identity(runner=subprocess.run):
    """Verify token issuance and project access before creating any infrastructure."""
    policy=pilot.PROFILE_POLICIES[pilot.LAYOUT_PROFILE]
    cmd=[str(Path.home()/'.nebius/bin/nebius'),'compute','instance','list',
         '--parent-id',policy['project_id'],'--impersonate-service-account-id',pilot.LAYOUT_GUARD_ID,
         '--format','json','--no-check-update','--no-browser','--timeout','25s']
    result=runner(cmd,capture_output=True,text=True,timeout=35)
    if result.returncode!=0:
        raise ValueError('Cleanup identity token issuance/project access is not authorized; no infrastructure created')
    data=json.loads(result.stdout)
    if data!={} and not isinstance(data.get('items'),list):raise ValueError('Invalid cleanup identity access probe')
    return {'cleanup_identity_access_verified':True,'service_account_id':pilot.LAYOUT_GUARD_ID,
            'project_id':policy['project_id'],'checked_unix':time.time()}


def manifest(config,nodes):
    doc=s.tp4.render(config,nodes,6600)
    doc['items'][1]['data'].update({name:(HERE/name).read_text() for name in ('layout-engines.py','layout-client.py')})
    pod=next(x for x in doc['items'] if x['kind']=='Pod')
    pod['spec']['containers'][0]['command']=['python3','-u','/probe/layout-engines.py','--config','/probe/config.json']
    return doc


class Controller(s.Controller):
    def __init__(self,run,config):
        super().__init__(run,config)
        if self.session['profile']!=pilot.LAYOUT_PROFILE:raise ValueError('Requires single-host layout admission')
        self.env.update(TF_VAR_single_gpu_host='true',TF_VAR_existing_guard_service_account_id=pilot.LAYOUT_GUARD_ID)
        self.mode=self.session.get('layout_mode','qualification')

    def ready(self,epoch):
        until=min(time.time()+1800,self.session['cleanup_start_deadline_unix']-600)
        while time.time()<until:
            pod=json.loads(self.call(self.k+['-n',s.tp4.NS,'get','pod','local','-o','json'],'local-pod').stdout)
            if pod.get('metadata',{}).get('deletionTimestamp') or pod.get('status',{}).get('phase') in ('Failed','Succeeded'):
                raise RuntimeError('Local engine Pod failed')
            for container in pod.get('status',{}).get('containerStatuses',[]):
                state=container.get('state',{})
                if state.get('terminated') or state.get('waiting',{}).get('reason') in ('ErrImagePull','ImagePullBackOff','CreateContainerConfigError','CreateContainerError','RunContainerError'):
                    raise RuntimeError('Local container failed: '+str(state))
            ip=pod.get('status',{}).get('podIP')
            if ip:
                code=('import json,pathlib,urllib.request; p=pathlib.Path("/results");'
                      'assert not (p/"layout-failure.json").exists();'
                      f'assert not (p/"{epoch}"/"engine-failure.json").exists();'
                      f'assert json.loads((p/"current-epoch.json").read_text())["epoch"]=={epoch!r};'+
                      ';'.join(f'urllib.request.urlopen("http://127.0.0.1:{port}/{path}",timeout=5).read()'
                               for port,path in [(8100,'v1/models'),(8200,'v1/models'),(8300,'evidence')]))
                result=self.call(self.k+['-n',s.tp4.NS,'exec','local','-c','engines','--','python3','-c',code],epoch+'-ready',25,check=False)
                if result.returncode==0:self.ips['local']=ip;print(epoch+' engines ready.',flush=True);return
                fail=self.call(self.k+['-n',s.tp4.NS,'exec','local','-c','engines','--','sh','-c',
                    f'cat /results/layout-failure.json /results/{epoch}/engine-failure.json 2>/dev/null'],epoch+'-failure-check',25,check=False)
                if fail.stdout.strip():raise RuntimeError('Engine failure: '+fail.stdout)
            self.sleep(5)
        raise TimeoutError('Layout engine readiness deadline')

    def epoch(self,name):
        args=['python3','-u','/probe/layout-client.py','--config','/probe/config.json','--suite','/data/suite.json.gz',
              '--local',self.ips['local'],'--epoch',name,'--mode',self.mode,
              '--deadline',str(self.session['cleanup_start_deadline_unix'])]
        script=shlex.join(args)+f'; result=$?; echo "$result" > /results/{name}.exit; exit "$result"'
        launch=f'nohup sh -c {shlex.quote(script)} > /results/{name}.log 2>&1 < /dev/null &'
        self.call(self.k+['-n',s.tp4.NS,'exec','client','--','sh','-c',launch],name+'-start',25)
        while time.time()+180<self.session['cleanup_start_deadline_unix']:
            result=self.call(self.k+['-n',s.tp4.NS,'exec','client','--','cat',f'/results/{name}.exit'],name+'-exit',25,check=False)
            if result.returncode==0:
                if result.stdout.strip()!='0':raise RuntimeError(name+' client failed')
                marker=json.loads(self.call(self.k+['-n',s.tp4.NS,'exec','client','--','cat',f'/results/{name}/complete.json'],name+'-complete').stdout)
                validate_result(marker,self.mode)
                pilot.write_json(self.run/(name+'-complete.json'),marker)
                return marker
            self.sleep(5)
        raise TimeoutError('Client reached collection reserve')

    def collect(self,required_epoch=None):
        errors=[]
        for pod,container in [('local','engines'),('client','client')]:
            try:
                result=self.call(self.k+['-n',s.tp4.NS,'exec',pod,'-c',container,'--','tar','czf','-','-C','/results','.'],pod+'-evidence',60,check=False,binary=True)
                result.check_returncode()
                # Check the fresh archive in isolation: old extracted files cannot mask a failed download.
                with tempfile.TemporaryDirectory(dir=self.out) as tmp:
                    with tarfile.open(self.out/(pod+'-evidence.tar.gz')) as archive:
                        archive.extractall(tmp,filter='data')
                    root=Path(tmp)
                    if required_epoch:
                        required=('requests.json','complete.json','final-evidence.json','plan.json') if pod=='client' else ('effective-config.json','supervisor.log')
                        for name in required:
                            if not (root/required_epoch/name).is_file():raise ValueError('Missing fresh '+pod+'/'+required_epoch+'/'+name)
                        if pod=='client':
                            saved=pilot.read_json(root/required_epoch/'complete.json');validate_result(saved,self.mode)
                            rows=json.loads((root/required_epoch/'requests.json').read_text())
                            if len(rows)!=saved['requests'] or not all('response' in row and 'after' in row for row in rows):
                                raise ValueError('Incomplete saved requests')
                    shutil.copytree(root,self.out/pod,dirs_exist_ok=True)
            except Exception as error:errors.append({'pod':pod,'error':repr(error)})
            try:self.call(self.k+['-n',s.tp4.NS,'logs',pod,'-c',container],pod+'-log',30,check=False)
            except Exception as error:errors.append({'pod':pod,'log_error':repr(error)})
        pilot.write_json(self.run/'last-collection.json',{'complete':not errors,'epoch':required_epoch,'errors':errors})
        if errors:raise RuntimeError('Evidence collection incomplete; refusing another phase: '+str(errors))

    def advance(self,epoch):
        self.call(self.k+['-n',s.tp4.NS,'exec','local','-c','engines','--','touch','/results/'+epoch+'.advance'],epoch+'-advance',25)
        until=min(time.time()+120,self.session['cleanup_start_deadline_unix']-180)
        while time.time()<until:
            failure=self.call(self.k+['-n',s.tp4.NS,'exec','local','-c','engines','--','cat','/results/layout-failure.json'],epoch+'-shutdown-failure',20,check=False)
            if failure.returncode==0:raise RuntimeError('Restart failed: '+failure.stdout)
            result=self.call(self.k+['-n',s.tp4.NS,'exec','local','-c','engines','--','cat','/results/'+epoch+'/shutdown.json'],epoch+'-shutdown',20,check=False)
            if result.returncode==0:
                marker=json.loads(result.stdout)
                if marker.get('drained') is not True:raise RuntimeError('Worker shutdown did not drain')
                pilot.write_json(self.run/(epoch+'-shutdown.json'),marker);return
            self.sleep(2)
        raise TimeoutError('Worker shutdown acknowledgement missing')

    def execute(self,suite):
        s.paired.Controller.bootstrap(self,allocate_gpus=False)
        self.allocate('local')
        self.apply(manifest(self.config_path,self.nodes_by_role),'layout-engine-manifest')
        self.apply(s.client_manifest(self.cpu_node,suite),'layout-client-manifest')
        self.call(self.k+['-n',s.tp4.NS,'wait','--for=condition=Ready','pod/client','--timeout=180s'],'client-ready',190)
        config,_=s.tp4.load_config(self.config_path)
        epochs=('default-a','packed-doc','packed','default-b') if config.get('vllm_version')=='0.29.0' else ('default-a','packed','default-b')
        for epoch in epochs:
            self.ready(epoch); result=self.epoch(epoch);self.collect(required_epoch=epoch)
            if self.mode=='comparison' and epoch=='default-a' and not result['default_slow_reproduced']:
                pilot.write_json(self.run/'inconclusive.json',{'reason':'Default slow-transfer state not reproduced; no packed restart'})
                return
            if epoch!='default-b' and time.time()+(600 if self.mode=='qualification' else 1200)>self.session['cleanup_start_deadline_unix']:
                raise TimeoutError('Insufficient reserved time for another restart and collection')
            self.advance(epoch)
        self.collect(required_epoch='default-b')
        if self.mode=='qualification':
            pilot.write_json(self.run/'qualification-complete.json',{'epochs':list(epochs),'mode':self.mode,
                'meaning':'Correctness, local transport, layout and restart qualification only; no performance comparison'})


def run_admitted(run,session,config,suite,mode):
    controller=None
    try:
        session['layout_mode']=mode;pilot.write_json(run/'session.json',session)
        guard=pilot.spawn_guard(run);pilot.wait_guard_ready(run,guard)
        session['guard_pid']=guard.pid;pilot.write_json(run/'session.json',session)
        controller=Controller(run,config);controller.execute(suite)
    except BaseException as error:
        pilot.write_json(run/'failure.json',{'error':repr(error)})
        if controller:
            try:controller.collect()
            except Exception as collect_error:
                pilot.write_json(run/'failure-collection.json',{'error':repr(collect_error)})
        raise
    finally:pilot.cleanup_until_target(run)


def main():
    p=pilot.parser();p.add_argument('--config',type=Path,required=True);p.add_argument('--suite',type=Path,required=True)
    p.add_argument('--mode',choices=['qualification','comparison'],default='qualification')
    p.add_argument('--qualification-run',type=Path)
    p.add_argument('--preflight-record',type=Path,required=True);a=p.parse_args()
    if a.profile!=pilot.LAYOUT_PROFILE:raise ValueError('Wrong profile')
    config,_=s.tp4.load_config(a.config)
    if config.get('vllm_version')!='0.29.0':raise ValueError('This launcher requires the reviewed v0.29 V2 layouts; the v0.26 setup did not activate packing')
    if a.mode=='comparison':
        if not a.qualification_run:raise ValueError('Full comparison requires a completed GPU restart qualification')
        validate_qualification(a.qualification_run,a.config,a.suite)
    proof=json.loads(a.preflight_record.read_text())
    for field in ('native_vllm_parser_passed','native_v2_layout_resolution_passed','native_cpu_allocation_passed','full_http_rehearsal_passed','qualification_http_rehearsal_passed','manifests_server_validated','layout_controller_rehearsed','restart_lifecycle_passed','controller_failure_replay_passed'):
        if proof.get(field) is not True:raise ValueError('Missing preflight '+field)
    for path in [a.config,a.suite,*[HERE/name for name in CODE],*sorted((HERE/'terraform-rdma').glob('*.tf'))]:
        if proof['sha256'].get(path.name)!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Changed preflight source '+path.name)
    capacity=verify_capacity()
    identity=verify_cleanup_identity()
    run,session=pilot.prepare_session(a);pilot.write_json(run/'layout-preflight.json',proof)
    pilot.write_json(run/'cleanup-identity-access.json',identity)
    pilot.write_json(run/'capacity-advice.json',capacity)
    print('RUN_DIR='+str(run),flush=True)
    run_admitted(run,session,a.config,a.suite,a.mode)


if __name__=='__main__':main()
