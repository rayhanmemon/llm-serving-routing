#!/usr/bin/env python3
"""One retained packed deployment through calibration and actual policy comparison.

No execution without an explicit new profile admission and matching local proof.
"""
import argparse,base64,copy,gzip,hashlib,importlib.util,json,os,shlex,shutil,subprocess,sys,tarfile,tempfile,time
from pathlib import Path
import yaml
from datetime import datetime
HERE=Path(__file__).resolve().parent

def module(name):
    sp=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
s=module('run-tp4-staged');pilot=s.pilot;s.PROFILES=(*s.PROFILES,pilot.ROUTER_SESSION_PROFILE)
budget=module('router-session-budget');layout=module('run-layout-local');render=module('render-tp4-router');result=module('router-session-results');transfer=module('router-session-transfer');planning=module('router-session-plan')
snapshots=module('snapshot-results')
PERF_IMAGE='quay.io/inference-perf/inference-perf:v0.6.1@sha256:e29328cc223ebae58d9022d60ad651cc3c4cbd534885a78b28f54086aa4b9c9e'
CODE=tuple(dict.fromkeys((*layout.CODE,'router-session-budget.py','router-session-plan.py','router-session-client.py','router-perf-adapter.py','router-session-results.py','router-session-qualify.py','router-session-transfer.py','snapshot-results.py','run-router-session.py','render-tp4-router.py','render.py','record-routes.py','import-image.py')))

def chart_digest(charts):
    root=charts.parent
    values={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and not any(x.startswith('.') for x in p.relative_to(root).parts)}
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()

def fresh_capacity(minimum):
    record=layout.verify_capacity(minimum=minimum)
    status=record['selected']['status']['preemptible']
    effective=datetime.fromisoformat(status['effective_at'].replace('Z','+00:00')).timestamp()
    if not 0 <= time.time()-effective <= 30*60:
        raise ValueError('Capacity observation is older than30minutes or future-dated; no GPU request')
    return record

def code_manifest(config,nodes,seconds=7200):
    doc=s.tp4.render(config,nodes,seconds)
    doc['items'][1]['data'].update({name:(HERE/name).read_text() for name in CODE if name.endswith('.py')})
    return doc

def client_manifest(cpu,suite,qualification,plan):
    doc=s.client_manifest(cpu,suite)
    cm,pod=doc['items'];cm['binaryData']['qualification.json.gz']=base64.b64encode(qualification.read_bytes()).decode();cm['data']={'plan.json':plan.read_text()}
    pod['spec']['containers'][0]['image']=PERF_IMAGE
    pod['spec']['containers'][0]['env']=[{'name':'PYTHONPATH','value':'/workspace:/probe'}]
    pod['spec']['containers'][0]['resources']['limits']['memory']='8Gi'
    pod['spec']['activeDeadlineSeconds']=10800;pod['spec']['containers'][0]['command']=['sleep','10800']
    return doc

class Controller(s.Controller):
    def __init__(self,run,config,charts,image,plan):
        super().__init__(run,config)
        if self.session['profile']!=pilot.ROUTER_SESSION_PROFILE:raise ValueError('Wrong combined-session admission')
        self.env.update(TF_VAR_single_gpu_host='false',TF_VAR_existing_guard_service_account_id=pilot.LAYOUT_GUARD_ID)
        self.gpu_checks=True;self.architecture='amd64';self.charts=charts;self.image=image;self.plan_path=plan;self.plan=json.loads(plan.read_text())
        self.local_requested=None;self.remote_requested=None;self.tuning=None;self.policy=None
        self.session_cap=budget.spending_cap(self.session['budget_snapshot']['remaining_before_attempt_usd_pretax'])
        self.original_target=self.session['deletion_target_unix'];self.context='router-topology';self.url='http://topology-epp.router-tp4.svc.cluster.local'

    def reduce_deadline(self,record):
        self.session.update({k:record[k] for k in ('cleanup_start_deadline_unix','deletion_target_unix')})
        pilot.write_json(self.run/'session.json',self.session);pilot.write_json(self.run/'staged-budget.json',record)
        command='import json,pathlib; p=pathlib.Path("/results/deadline-request.json");t=p.with_suffix(".tmp");t.write_text('+repr(json.dumps({'cluster':self.cluster,'deadline':self.session['cleanup_start_deadline_unix']}))+');t.replace(p)'
        self.call(self.k+['-n',s.tp4.NS,'exec','deadline-guard','--','python','-c',command],'shorten-cloud-guard',25)
        until=time.time()+45
        while time.time()<until:
            ack=self.call(self.k+['-n',s.tp4.NS,'exec','deadline-guard','--','cat','/results/guard-ready.json'],'shortened-guard-ack',20)
            obj=json.loads(ack.stdout)
            if obj.get('cluster')==self.cluster and obj.get('deadline')==self.session['cleanup_start_deadline_unix']:
                if obj.get('gpu_groups_present_at_arm')!=['router-local']:raise ValueError('Unexpected GPU group before remote admission')
                pilot.write_json(self.run/'shortened-guard-ready.json',obj);return
            self.sleep(2)
        raise RuntimeError('Cloud guard did not acknowledge shorter deadline; remote remains unallocated')

    def allocate(self,role):
        if role=='local':
            # CPU/image preparation can outlive the initial availability snapshot.
            pilot.write_json(self.run/'capacity-before-local.json',fresh_capacity(minimum=2))
            self.local_requested=time.time()
        else:
            if not (self.run/'tp4-local-qualified.json').exists():raise ValueError('No verified local qualification')
            # Recheck availability before committing the second node; never wait on a billed host.
            pilot.write_json(self.run/'capacity-before-remote.json',fresh_capacity(minimum=1))
            now=time.time();record=budget.admit_remote(self.session['started_unix'],self.local_requested,now,self.original_target,cap=self.session_cap)
            self.reduce_deadline(record)
            self.remote_requested=now
        pilot.write_json(self.run/'resource-request-times.json',{'local':self.local_requested,'remote':self.remote_requested})
        super().allocate(role)

    def deploy(self,role):
        doc=code_manifest(self.config_path,self.nodes_by_role)
        if role=='remote':doc['items']=[x for x in doc['items'] if x['kind']=='Pod' and x['metadata']['name']=='remote']
        self.apply(doc,role+'-retained-engine-manifest')
        until=min(time.time()+25*60,self.session['cleanup_start_deadline_unix']-120)
        while time.time()<until:
            pod=json.loads(self.call(self.k+['-n',s.tp4.NS,'get','pod',role,'-o','json'],role+'-pod').stdout)
            if pod.get('metadata',{}).get('deletionTimestamp') or pod.get('status',{}).get('phase') in ('Failed','Succeeded'):raise RuntimeError('GPU Pod failed')
            for c in pod.get('status',{}).get('containerStatuses',[]):
                st=c.get('state',{})
                if st.get('terminated') or st.get('waiting',{}).get('reason') in ('ErrImagePull','ImagePullBackOff','CreateContainerConfigError'):raise RuntimeError('GPU container failed')
            ip=pod.get('status',{}).get('podIP')
            if ip:
                f=self.call(self.k+['-n',s.tp4.NS,'exec',role,'-c','engines','--','cat','/results/engine-failure.json'],role+'-failure',15,check=False)
                if f.returncode==0:raise RuntimeError(f.stdout)
                ports=(8100,8200,8300) if role=='local' else (8200,8300)
                code='import urllib.request;'+ ';'.join(f'urllib.request.urlopen("http://127.0.0.1:{p}/'+('evidence' if p==8300 else 'v1/models')+'",timeout=4).read()' for p in ports)
                r=self.call(self.k+['-n',s.tp4.NS,'exec',role,'-c','engines','--','python3','-c',code],role+'-ready',20,check=False)
                if r.returncode==0:self.ips[role]=ip;return
            self.sleep(3)
        raise TimeoutError(role+' model startup exceeded allowance')

    def live_identity(self):
        state=json.loads(self.call(self.k+['-n',s.tp4.NS,'get','pods','-o','json'],'live-pods').stdout)
        required=set(self.ips)|{'client'}
        if self.policy:required|={'prefill-http','picker'}
        found={}
        for pod in state['items']:
            name=pod['metadata']['name']
            if pod['metadata'].get('labels',{}).get('llm-d-router-standalone')=='topology-epp':name='picker'
            if name not in required:continue
            status=pod.get('status',{})
            if status.get('phase')!='Running' or pod['metadata'].get('deletionTimestamp'):raise RuntimeError('Pod lost during experiment')
            containers=status.get('containerStatuses',[])
            if not containers or any(x.get('restartCount',0) or 'running' not in x.get('state',{}) for x in containers):raise RuntimeError('Serving/client container restarted or stopped')
            if name in found:raise RuntimeError('Ambiguous serving endpoint identity')
            found[name]=(pod['metadata']['uid'],tuple(sorted((x['name'],x.get('containerID')) for x in containers)))
        if set(found)!=required:raise RuntimeError('Serving/client Pod missing')
        return found

    def job(self,name,args,limit):
        if time.time()+limit+120>=self.session['cleanup_start_deadline_unix']:raise TimeoutError('Insufficient work/collection reserve')
        identity=self.live_identity()
        script=shlex.join(args)+f'; rc=$?; echo "$rc" > /results/{name}.exit.tmp; mv /results/{name}.exit.tmp /results/{name}.exit; exit "$rc"'
        launch=f'nohup sh -c {shlex.quote(script)} > /results/{name}.log 2>&1 < /dev/null &'
        self.call(self.k+['-n',s.tp4.NS,'exec','client','--','sh','-c',launch],name+'-launch',20)
        until=time.time()+limit
        while time.time()<until:
            if self.live_identity()!=identity:raise RuntimeError('Serving/client identity changed during trial')
            r=self.call(self.k+['-n',s.tp4.NS,'exec','client','--','cat',f'/results/{name}.exit'],name+'-exit',15,check=False)
            if r.returncode==0:
                if r.stdout.strip()!='0':raise RuntimeError(name+' failed')
                return
            self.sleep(2)
        raise TimeoutError(name+' exceeded bounded duration')

    def qualify(self,role):
        args=['python3','/probe/router-session-qualify.py','--config','/probe/config.json','--suite','/data/qualification.json.gz','--local',self.ips['local'],'--role',role,'--deadline',str(self.session['cleanup_start_deadline_unix'])]
        if role=='remote':args+=['--remote',self.ips['remote']]
        self.job('qualify-'+role,args,240);self.collect(required='qualification/'+role+'/complete.json')
        marker=pilot.read_json(self.out/'client/qualification'/role/'complete.json')
        if not marker.get('validated') or marker.get('layout')!='BHLNC' or marker.get('requests')!=6:raise ValueError('Invalid qualification handoff')
        pilot.write_json(self.run/('tp4-'+role+'-qualified.json'),marker)

    def collect(self,required=None):
        errors=[];verified={}
        for pod in (*self.ips,'client'):
            container='client' if pod=='client' else 'engines'
            try:
                # Completed trial directories are immutable. Retain earlier verified
                # copies locally rather than retransmitting all prior reports.
                relative=Path(required).parent if pod=='client' and required else Path('.')
                source_root=str(Path('/results')/relative)
                r=self.call(self.k+['-n',s.tp4.NS,'exec',pod,'-c',container,'--','python3','/probe/snapshot-results.py','--root',source_root],pod+'-evidence',60,binary=True,check=False)
                if r.returncode:
                    raise RuntimeError(f'{pod} snapshot command exited {r.returncode}: '+r.stderr.decode(errors='replace')[-1200:])
                with tempfile.TemporaryDirectory(dir=self.out) as tmp:
                    with tarfile.open(self.out/(pod+'-evidence.tar.gz')) as t:t.extractall(tmp,filter='data')
                    verified[pod]=snapshots.validate(tmp)
                    verified[pod]['source_root']=source_root
                    if required and pod=='client' and not (Path(tmp)/Path(required).name).is_file():raise ValueError('Fresh required artifact missing')
                    shutil.copytree(tmp,self.out/pod/relative,dirs_exist_ok=True)
            except Exception as e:errors.append({'pod':pod,'error':str(e)[:1600]})
        for container in ('epp','envoy-proxy'):
            try:self.call(self.k+['-n',s.tp4.NS,'logs','deploy/topology-epp','-c',container],('envoy' if container=='envoy-proxy' else container)+'-log',20,check=False)
            except Exception:pass
        pilot.write_json(self.run/'last-collection.json',{'complete':not errors,'errors':errors,'snapshots':verified})
        if errors:raise RuntimeError('Incomplete collection; no next phase: '+str(errors))

    def picker(self,policy,params=None):
        self.drain()
        params=params or {}
        cfg=render.b.epp_config(policy,params.get('allowance',0),params.get('weight',.5),params.get('cap',2),True)
        for plug in cfg['plugins']:
            if plug['type']=='active-request-scorer':plug['parameters']={'idleThreshold':params.get('idle_threshold',0),'maxBusyScore':params.get('max_busy_score',1)}
        values=render.b.router_values(cfg,self.cpu_node);values['router']['epp']['image']['tag']='topology-0217d299-'+self.architecture
        values['router']['epp']['flags'].update({'v':2,'emit-endpoint-scores':True})
        path=self.out/('picker-'+policy+'.yaml');path.write_text(yaml.safe_dump(values))
        native=self.call(['helm','template','topology',str(self.charts),'-n',s.tp4.NS,'-f',str(path)],policy+'-helm').stdout
        logged=self.call([sys.executable,str(HERE/'record-routes.py')],policy+'-logs',30,native).stdout
        docs=list(yaml.safe_load_all(logged))
        for obj in docs:
            if obj and obj.get('kind')=='ConfigMap' and 'envoy.yaml' in obj.get('data',{}):
                e=yaml.safe_load(obj['data']['envoy.yaml'])
                for listener in e['static_resources']['listeners']:
                    for chain in listener.get('filter_chains',[]):
                        for item in chain['filters']:
                            if 'http_connection_manager' in item['name']:item['typed_config']['preserve_external_request_id']=True
                obj['data']['envoy.yaml']=yaml.safe_dump(e)
        self.call(self.k+['apply','-f','-'],policy+'-apply',60,yaml.safe_dump_all(docs))
        self.call(self.k+['-n',s.tp4.NS,'rollout','restart','deployment/topology-epp'],policy+'-restart',30)
        self.call(self.k+['-n',s.tp4.NS,'rollout','status','deployment/topology-epp','--timeout=90s'],policy+'-ready',100)
        self.policy=policy
        # Metric service exists independently of chart port naming.
        self.metrics_url='http://topology-epp.router-tp4.svc.cluster.local:9090/metrics'
        self.wait_discovery()
        self.warmup()
        self.drain()

    def wait_discovery(self):
        code=('import urllib.request; raw=urllib.request.urlopen('+repr(self.metrics_url)+',timeout=3).read().decode(); '
              'assert sum(float(x.rsplit(" ",1)[1]) for x in raw.splitlines() if x.startswith("llm_d_epp_ready_endpoints{"))==3')
        until=time.time()+45
        while time.time()<until:
            status=self.call(self.k+['-n',s.tp4.NS,'exec','client','--','python3','-c',code],'complete-discovery',10,check=False)
            if status.returncode==0:return
            self.sleep(1)
        raise RuntimeError('Not all three serving endpoints are ready')

    def warmup(self):
        # A healthy EPP process can precede usable endpoint discovery.
        # Identical short warmup for every policy; never retry a measured trial.
        code=('import gzip,json,urllib.request; '
              's=json.loads(gzip.decompress(open("/data/suite.json.gz","rb").read())); '
              'b=dict(next(c["request_body"] for c in s["cases"] if c["input_tokens"]==4096),max_tokens=1); '
              'r=urllib.request.Request('+repr(self.url+'/v1/completions')+',data=json.dumps(b).encode(),headers={"Content-Type":"application/json","x-benchmark-run":"declared-warmup"}); '
              'raw=urllib.request.urlopen(r,timeout=15).read();assert b"[DONE]" in raw')
        if self.gpu_checks:
            code=('import gzip,json,importlib.util; s=json.loads(gzip.decompress(open("/data/suite.json.gz","rb").read())); '
                  'b=dict(next(c["request_body"] for c in s["cases"] if c["input_tokens"]==4096),max_tokens=1); '
                  'sp=importlib.util.spec_from_file_location("transfer","/probe/router-session-transfer.py");m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m); '
                  'm.warmup('+repr(self.ips)+','+repr(self.url)+',b)')
        until=time.time()+45
        while time.time()<until:
            r=self.call(self.k+['-n',s.tp4.NS,'exec','client','--','python3','-c',code],'picker-warmup',20,check=False)
            if r.returncode==0:return
            if not any(x in r.stderr for x in ('503','Connection refused','Temporary failure in name resolution','timed out')):
                raise RuntimeError('Router warmup failed: '+r.stderr[-400:])
            self.sleep(2)
        raise TimeoutError('EPP discovery did not become usable')

    def drain(self):
        if not self.policy:return
        until=time.time()+45
        code='import urllib.request; raw=urllib.request.urlopen('+repr(self.metrics_url)+',timeout=3).read().decode(); assert all(float(x.rsplit(" ",1)[1])==0 for x in raw.splitlines() if "_inflight_requests{" in x)'
        while time.time()<until:
            r=self.call(self.k+['-n',s.tp4.NS,'exec','client','--','python3','-c',code],'picker-drain',10,check=False)
            if r.returncode==0:return
            self.sleep(1)
        raise RuntimeError('In-flight requests remain; refusing policy restart')

    def import_picker(self):
        self.call([sys.executable,str(HERE/'import-image.py'),'--archive',str(self.image),'--sha256',hashlib.sha256(self.image.read_bytes()).hexdigest(),'--execute','--node',self.cpu_node,'--context',self.context,'--namespace',s.tp4.NS],'epp-image-import',180)

    def setup_router(self):
        for role in ('local','remote'):
            patch={'metadata':{'labels':{'llm-d.ai/guide':'topology-measurement','llm-d.ai/role':'decode','kubernetes.io/hostname':self.nodes_by_role[role]}}}
            self.call(self.k+['-n',s.tp4.NS,'patch','pod',role,'--type=merge','-p',json.dumps(patch)],role+'-labels')
        self.call(self.k+['apply','-f','-'],'prefill-proxy',30,yaml.safe_dump_all(render.prefill_proxy(self.nodes_by_role['local'],self.ips['local'])))
        self.call(self.k+['-n',s.tp4.NS,'wait','--for=condition=Ready','pod/prefill-http','--timeout=60s'],'prefill-proxy-ready',70)
        self.pins={role:base64.b64encode(f'{s.tp4.NS}/{role}-rank-0'.encode()).decode() for role in ('local','remote')}
        self.picker('diagnostic')

    def trial(self,trial):
        args=['python3','/probe/router-session-client.py','--plan','/data/plan.json','--suite','/data/suite.json.gz',
              '--trial',json.dumps(trial,separators=(',',':')),'--url',self.url,'--metrics',self.metrics_url,
              '--pins',json.dumps(self.pins),'--deadline',str(self.session['cleanup_start_deadline_unix'])]
        if self.gpu_checks:args+=['--hosts',json.dumps(self.ips)]
        self.job(trial['id'],args,trial['max_seconds']+15);self.drain();self.collect(required='trials/'+trial['id']+'/complete.json')
        folder=self.out/'client/trials'/trial['id'];doc,rows=result.records(folder)
        # Join every outcome to real Envoy routing and retain per-request EPP count logs.
        until=time.time()+8
        while True:
            access=[json.loads(x) for x in (self.out/'envoy-log.stdout').read_text().splitlines() if x.startswith('{')]
            matched={x.get('request_id'):x for x in access if x.get('run_id')==trial['id']}
            if all(row['request_key'] in matched for row in rows) or time.time()>=until:break
            self.sleep(.5)
            self.call(self.k+['-n',s.tp4.NS,'logs','deploy/topology-epp','-c','envoy-proxy'],'envoy-log',20)
        for row in rows:
            if row['request_key'] not in matched:raise ValueError('Missing route for measured request')
            route=matched[row['request_key']];upstream=route.get('upstream_host','')
            if not any(upstream.startswith(ip+':') for ip in self.ips.values()):raise ValueError('Unknown routed decoder')
            if row['pin'] and not upstream.startswith(self.ips[row['pin']]+':'):raise ValueError('Diagnostic route mismatch')
        pilot.write_json(folder/'routes.json',matched)
        pilot.write_json(folder/'sampled-inflight-counts.json',{'scope':'Router gauges sampled just before client request timing; not exact internal scheduling snapshots','requests':{row['request_key']:row['counts'] for row in rows}})
        if self.gpu_checks:
            samples=pilot.read_json(folder/'engine-load.json')
            pilot.write_json(folder/'probe-load-timelines.json',result.probe_load_timelines(rows,samples))
            _,architecture=s.tp4.load_config(self.config_path)
            checked=transfer.verify(pilot.read_json(folder/'metrics-before.json'),pilot.read_json(folder/'metrics-after.json'),rows,matched,self.ips,architecture)
            pilot.write_json(folder/'transfer-verified.json',checked)
        return doc,rows

    def execute(self,suite,qualification):
        s.paired.Controller.bootstrap(self,allocate_gpus=False)
        # Prepare CPU-only artifacts before the first expensive GPU request.
        code=code_manifest(self.config_path,{'local':'not-yet-allocated'})
        self.apply({'apiVersion':'v1','kind':'List','items':code['items'][:2]},'session-code')
        self.apply(client_manifest(self.cpu_node,suite,qualification,self.plan_path),'session-client')
        self.call(self.k+['-n',s.tp4.NS,'wait','--for=condition=Ready','pod/client','--timeout=180s'],'client-ready',190)
        self.import_picker()
        self.allocate('local');self.deploy('local');self.qualify('local')
        self.allocate('remote');self.deploy('remote');self.qualify('remote');self.setup_router()
        self.evaluate()

    def evaluate(self):
        training=[]
        for t in planning.calibration_trials(self.plan):
            training.append(self.trial(t))
        self.tuning=result.tune(training,self.plan)
        if self.gpu_checks:
            loads=[sample for path in (self.out/'client/trials').glob('train-*/engine-load.json') for sample in json.loads(path.read_text())]
            queued=result.decoder_queue_observed(loads,self.plan['decoder_max_num_seqs'])
            self.tuning['real_decoder_queue_observed']=queued
            self.tuning['tradeoff_observed'] &= queued
        pilot.write_json(self.run/'frozen-tuning.json',self.tuning)
        if not self.tuning['tradeoff_observed']:
            pilot.write_json(self.run/'inconclusive.json',{'reason':'Training did not establish a useful locality/load tradeoff'});return
        evaluated=[]
        for t in self.plan['trials']:
            if time.time()+t['max_seconds']+210>=self.session['cleanup_start_deadline_unix']:
                pilot.write_json(self.run/'incomplete.json',{'reason':'Protected collection/cleanup reserve','next_trial':t['id']});break
            policy=self.tuning['reference'] if t['policy']=='reference' else t['policy']
            self.picker(policy,self.tuning['parameters'][policy]);evaluated.append(self.trial(t))
            pilot.write_json(self.run/'comparison-summary.json',result.summarize(evaluated,self.tuning,self.plan))
        self.collect()


def execute_admitted(run,session,config,suite,qualification,charts,image,plan):
    controller=None
    try:
        guard=pilot.spawn_guard(run);pilot.wait_guard_ready(run,guard)
        session['guard_pid']=guard.pid;pilot.write_json(run/'session.json',session)
        controller=Controller(run,config,charts,image,plan);controller.execute(suite,qualification)
    except BaseException as e:
        pilot.write_json(run/'failure.json',{'error':repr(e)})
        if controller:
            try:controller.collect()
            except Exception as other:pilot.write_json(run/'failure-collection.json',{'error':repr(other)})
        raise
    finally:pilot.cleanup_until_target(run)


def main():
    p=pilot.parser()
    for key in ('config','suite','qualification','charts','image','comparison-plan','preflight-record'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    if not a.execute:raise ValueError('Cloud execution disabled; explicit new admission is required')
    if a.profile!=pilot.ROUTER_SESSION_PROFILE:raise ValueError('New combined-session profile required; old approvals cannot admit it')
    proof=pilot.read_json(a.preflight_record)
    cfg,_=s.tp4.load_config(a.config)
    if cfg.get('kv_cache_layout')!='BHLNC' or cfg.get('vllm_version')!='0.29.0':raise ValueError('Wrong packed serving baseline')
    if proof.get('chart_sha256')!=chart_digest(a.charts):raise ValueError('Changed prepared Helm chart')
    if json.loads(a.comparison_plan.read_text())!=module('router-session-plan').frozen_plan():raise ValueError('Comparison plan differs from reviewed protocol')
    if cfg['max_num_seqs']!=json.loads(a.comparison_plan.read_text())['decoder_max_num_seqs']:raise ValueError('Decoder concurrency limit differs from the pressure protocol')
    for k in ('combined_controller_rehearsed','real_router_rehearsed','native_perf_rehearsed','native_vllm_checked','manifests_validated','budget_bound_verified'):
        if proof.get(k) is not True:raise ValueError('Missing preparation proof: '+k)
    for path in [a.config,a.suite,a.qualification,a.comparison_plan,a.image,*[HERE/n for n in CODE],*sorted((HERE/'terraform-rdma').glob('*.tf'))]:
        if proof['sha256'].get(path.name)!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Changed prepared input: '+path.name)
    fresh_capacity(minimum=2);layout.verify_cleanup_identity()
    run,session=pilot.prepare_session(a);pilot.write_json(run/'combined-preflight.json',proof)
    execute_admitted(run,session,a.config,a.suite,a.qualification,a.charts,a.image,a.comparison_plan)

if __name__=='__main__':main()
