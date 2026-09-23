#!/usr/bin/env python3
"""Local real-router/native-benchmark integration. No cloud API or GPU claims."""
import argparse,importlib.util,json,os,subprocess,time,shlex,tarfile,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('combined',HERE/'run-router-session.py');r=importlib.util.module_from_spec(sp);sp.loader.exec_module(r)

def rehearse(kubeconfig,charts,out):
    k=['kubectl','--kubeconfig',str(kubeconfig),'--context','kind-router-session','--request-timeout=20s']
    state=json.loads(subprocess.check_output(k+['config','view','--minify','-o','json']))
    if not state['clusters'][0]['cluster']['server'].startswith('https://127.0.0.1:'):raise ValueError('Local loopback API required')
    node=json.loads(subprocess.check_output(k+['get','nodes','-o','json']))['items'][0]['metadata']['name']
    source_hashes={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in r.CODE}
    out.mkdir(parents=True,exist_ok=False);obj=object.__new__(r.Controller);obj.run=out;obj.out=out/'paired';obj.out.mkdir()
    obj.k=k;obj.context='kind-router-session';obj.cpu_node=node;obj.nodes_by_role={'local':node,'remote':node+'-synthetic-remote'}
    obj.env={**os.environ,'KUBECONFIG':str(kubeconfig),'KUBECTL_REMOTE_COMMAND_WEBSOCKETS':'false'};obj.sleep=time.sleep
    obj.session={'cleanup_start_deadline_unix':time.time()+2400};obj.gpu_checks=False;obj.architecture='arm64';obj.policy=None;obj.ips={}
    obj.config_path=HERE.parent.parent/'workloads/router-session/config.json';obj.plan_path=obj.config_path.with_name('plan.json');obj.plan=json.loads(obj.plan_path.read_text())
    obj.charts=charts;obj.image=Path('/Users/rayhanmemon/.codex/run-state/router-h100-pilot/prepared/v029-overnight-final/epp-amd64.tar')
    obj.url='http://topology-epp.router-tp4.svc.cluster.local'
    def rpc(cmd,**kw):
        if any(str(x).endswith('import-image.py') for x in cmd):
            return subprocess.CompletedProcess(cmd,0,'Local ARM image preloaded; production AMD archive import is separately checked.','')
        return subprocess.run(cmd,**kw)
    obj.rpc=rpc
    doc=r.code_manifest(obj.config_path,{'local':node,'remote':node+'-synthetic-remote'})
    doc['items'][1]['data']['router-http-fixture.py']=(HERE/'router-http-fixture.py').read_text()
    doc['items'][1]['data']['live-log-writer.py']='from pathlib import Path\nimport time\np=Path("/results/prefill.log")\nwith p.open("ab") as f:\n while True:\n  f.write(b"live inference log line\\n"*16);f.flush();time.sleep(.005)\n'
    for pod in [x for x in doc['items'] if x['kind']=='Pod']:
        pod['spec']['nodeSelector']={'kubernetes.io/hostname':node}
        engine=pod['spec']['containers'][0];engine['image']='python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b'
        engine['command']=['sh','-c','python3 /probe/live-log-writer.py & exec python3 -u /probe/router-http-fixture.py'];engine['resources']={'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'memory':'512Mi'}}
        engine.pop('securityContext',None)
    obj.apply(doc,'synthetic-workers')
    client=r.client_manifest(node,obj.config_path.with_name('suite.json.gz'),obj.config_path.with_name('qualification.json.gz'),obj.plan_path)
    client['items'][1]['spec']['containers'][0]['image']='python:3.12-slim@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b'
    client['items'][1]['spec']['containers'][0]['imagePullPolicy']='IfNotPresent'
    obj.apply(client,'real-client')
    obj.call(k+['-n','router-tp4','wait','--for=condition=Ready','pod/local','pod/remote','pod/client','--timeout=300s'],'fixtures-ready',310)
    pods=json.loads(obj.call(k+['-n','router-tp4','get','pods','-o','json'],'fixtures').stdout)['items']
    obj.ips={x['metadata']['name']:x['status']['podIP'] for x in pods if x['metadata']['name'] in ('local','remote')}
    obj.setup_router()
    trials=[{'id':'smoke-diagnostic','mode':'calibration','state':[1,1],'repeat':0,'seed':22092026,'max_seconds':75}]
    trials += [{'id':'smoke-'+p,'mode':'heldout','policy':p,'trace':'low','repeat':0,'seed':22092026,'max_seconds':90} for p in r.result.POLICIES]
    # Kind's ARM CRI cannot reliably start the AMD64 benchmark image. Run that
    # exact pinned image in Docker's supported AMD64 emulation through local forwards.
    # The CPU administrative Pod and all router/sidecar paths remain real Kubernetes.
    def native_job(name,args,limit):
        identity=obj.live_identity()
        trial=json.loads(args[args.index('--trial')+1]);base=out/name;base.mkdir()
        (base/'plan.json').write_text(obj.plan_path.read_text())
        suite=base/'suite.json.gz';suite.write_bytes(obj.config_path.with_name('suite.json.gz').read_bytes())
        command=['docker','run','--rm','--name','router-native-'+name,'--platform','linux/amd64',
            '-v',str(HERE)+':/probe:ro','-v',str(base)+':/work','-e','PYTHONPATH=/workspace:/probe',
            '--entrypoint','python',r.PERF_IMAGE,'/probe/router-session-client.py',
            '--plan','/work/plan.json','--suite','/work/suite.json.gz','--trial',json.dumps(trial),
            '--out','/work/trials','--url','http://host.docker.internal:18882',
            '--metrics','http://host.docker.internal:19091/metrics','--pins',json.dumps(obj.pins),'--deadline',str(obj.session['cleanup_start_deadline_unix'])]
        port_log=(base/'forward.log').open('w')
        forward=subprocess.Popen(k+['-n','router-tp4','port-forward','svc/topology-epp','18882:80','19091:9090','--address','0.0.0.0'],stdout=port_log,stderr=subprocess.STDOUT)
        try:
            import socket
            for _ in range(40):
                try:
                    with socket.create_connection(('127.0.0.1',18882),timeout=.2):break
                except OSError:time.sleep(.2)
            else:raise TimeoutError('Local benchmark forward failed')
            with (base/'docker.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=limit+15)
        finally:
            forward.terminate();forward.wait(timeout=10);port_log.close()
            subprocess.run(['docker','rm','-f','router-native-'+name],capture_output=True,timeout=15)
        archive=base/'trial.tar.gz'
        with tarfile.open(archive,'w:gz') as t:t.add(base/'trials'/name,arcname='trials/'+name)
        with archive.open('rb') as src:
            subprocess.run(k+['-n','router-tp4','exec','-i','client','--','tar','xzf','-','-C','/results'],stdin=src,check=True,timeout=30)
        if obj.live_identity()!=identity:raise ValueError('Live identity changed during native replay')
    obj.job=native_job
    count=0;foreground=0
    for t in trials:
        if t['mode']!='calibration':obj.picker(t['policy'],{'allowance':1,'weight':.5,'cap':2})
        doc,rows=obj.trial(t);count+=len(rows);foreground+=sum(x['kind']=='foreground' for x in rows)
        if not any(x['counts']['local']+x['counts']['remote']>0 for x in rows):raise ValueError('No genuine router in-flight load observed')
        print(json.dumps({'trial':t['id'],'requests':len(rows),'joined_routes':len(rows),'nonzero_inflight':True}),flush=True)
    summary={'real_router_rehearsed':True,'native_perf_rehearsed':True,'recorded_requests':count,'foreground':foreground,
             'real_epp_source':'0217d29924ba93b90f952e7a0281dd8dda146703','real_sidecar':True,'synthetic_workers':True,
             'cloud_calls':False,'gpu_execution':False,'production_image_import_substituted':True,'epp_architecture':'arm64',
             'scope':'Real Controller setup/picker/job/collection/route joins; six native benchmark trials; cloud/GPU qualification validated separately.'}
    summary['source_sha256']=source_hashes
    summary['benchmark_execution']='Exact pinned AMD64 image in Docker through local Kubernetes port forwards; no GPU timing claims'
    summary['continuous_log_writers']=True
    summary['live_identity_checked_each_trial']=True
    snapshot_record=json.loads((out/'last-collection.json').read_text())
    if any('prefill.log' not in snapshot_record['snapshots'][role]['mutable_log_prefixes'] for role in ('local','remote')):
        raise ValueError('Growing engine logs were not snapshot-validated')
    summary['growing_log_snapshots_validated']=True
    (out/'summary.json').write_text(json.dumps(summary,indent=2));return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--kubeconfig',type=Path,required=True);p.add_argument('--charts',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    print(json.dumps(rehearse(a.kubeconfig,a.charts,a.out)))
