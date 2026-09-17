#!/usr/bin/env python3
"""Bounded RDMA serving qualification and existing matched timing block."""
import importlib.util,json,sys,time,subprocess,hashlib,os
from pathlib import Path
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('serving',HERE/'run-serving.py');m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
args=m.parse_args();run=args.run_dir;s=m.read_json(run/'session.json')
assert s['profile'] in ('rdma-h200-serving','rdma-h200-serving-retry')
# Local runner deadline mirrors independent billing guard; no separate first-results cutoff.
s['first_measurement_deadline_unix']=s['cleanup_start_deadline_unix']
assert args.execute
m.stage_frozen_suite(run)
m.verify_archive(args.epp_archive,args.epp_sha256)
root=run/'serving';root.mkdir()
r=m.Runner(args,s,root)
r.environment['KUBECTL_REMOTE_COMMAND_WEBSOCKETS']='false'
try:
 r.wait_apply();cluster,groups=r.terraform_outputs();r.fetch_kubeconfig(cluster);nodes=r.wait_nodes(groups)
 rendered=r.render_for_resume(nodes)
 import yaml
 path=rendered/'modelservers.yaml';objects=list(yaml.safe_load_all(path.read_text()))
 for obj in objects:
  if obj.get('kind')=='Deployment':
   for c in obj['spec']['template']['spec']['containers']:
    if c['name']=='modelserver':
     c['securityContext']={'capabilities':{'add':['IPC_LOCK']}}
     c['env'].append({'name':'UCX_TLS','value':'rc,cuda_copy,self'})
 path.write_text(yaml.safe_dump_all(objects,sort_keys=False))
 r.apply_models_and_router(rendered,nodes['cpu']);inventory=r.wait_serving(nodes);r.deploy_correctness_client(nodes['cpu'])
 pods={p['metadata']['labels'].get('app.kubernetes.io/name'):p for p in r.inventory() if p['metadata']['labels'].get('app.kubernetes.io/name') in ['prefill','decode-local','decode-remote']}
 local=inventory['local_decoder'];remote=inventory['remote_decoder'];base='http://topology-epp.topology-measurement.svc.cluster.local'
 config={'session_id':s['session_id'],'endpoints':{'direct-local':'http://'+pods['decode-local']['status']['podIP']+':8200','direct-remote':'http://'+pods['decode-remote']['status']['podIP']+':8200','pd-local':base,'pd-remote':base},'pods':{'pd-local':local,'pd-remote':remote},'metrics_urls':{role:'http://'+pods[name]['status']['podIP']+':'+port+'/metrics' for role,name,port in [('prefill','prefill','8000'),('local','decode-local','8200'),('remote','decode-remote','8200')]}}
 out=run/'parity';out.mkdir();m.write_json(out/'endpoints.json',config)
 plan=run/'frozen-suite.json';sha=hashlib.sha256(plan.read_bytes()).hexdigest()
 collect=[sys.executable,str(HERE/'collect.py'),'--context',args.context,'--namespace',args.namespace]
 r.command(collect+['--out',str(out/'before')],timeout=120)
 for source,dest in [(plan,'suite.json'),(out/'endpoints.json','endpoints.json'),(HERE/'parity-batch-client.py','client.py')]:
  r.kubectl(['-n',args.namespace,'exec','-i','topology-correctness','-c','client','--','python','-c',f'import sys;open("/results/{dest}","w").write(sys.stdin.read())'],input_text=source.read_text(),timeout=60)
 result=r.kubectl(['-n',args.namespace,'exec','topology-correctness','-c','client','--','python','/results/client.py','--plan','/results/suite.json','--sha256',sha],timeout=600,log=out/'client',check=False)
 (out/'responses.jsonl').write_text(result.stdout)
 r.command(collect+['--out',str(out/'after')],timeout=120)
 assert result.returncode==0,'parity client failed'
 r.command([sys.executable,str(HERE/'known-answer-suite.py'),'validate','--plan',str(plan),'--responses',str(out/'responses.jsonl'),'--out',str(out/'validation.json')],timeout=30)
 v=m.read_json(out/'validation.json');assert v['validated'] and v['criterion']=='direct-parity' and v['direct_parity']['passed_cases']==8 and v['request_integrity']['passed']==32
 rows=[json.loads(x) for x in (out/'responses.jsonl').read_text().splitlines()];assert len(rows)==32
 assert all(x['transfer_checks_passed'] and x['requested_decoder']==x['selected_decoder'] for x in rows)
 ident=m.worker_identity(out/'before');assert ident==m.worker_identity(out/'after');assert m.gpu_identity(out/'before')==m.gpu_identity(out/'after')
 transfers={role:m.transfer_delta(out/'before',out/'after',pod) for role,pod in [('local',local),('remote',remote)]}
 assert all(x['operations']==8 and x['timed_operations']==8 and x['bytes']>0 and x['seconds']>0 for x in transfers.values())
 protocol={}
 for role,pod in [('local',local),('remote',remote)]:
  logs=r.kubectl(['-n',args.namespace,'logs',pod,'-c','modelserver'],timeout=30).stdout;(out/(role+'-protocol.log')).write_text(logs)
  lines=logs.splitlines();tables=[]
  for i,line in enumerate(lines):
   if 'remote memory read' in line and 'cuda' in line:tables.append('\n'.join(lines[i:i+8]))
  assert any('rc_mlx5' in t and 'zero-copy' in t for t in tables),role+' lacks RDMA CUDA READ protocol evidence'
  protocol[role]=tables
 m.write_json(out/'protocol.json',protocol)
 marker={'session_id':s['session_id'],'verified':True,'validated':True,'verified_unix':time.time(),'namespace':args.namespace,'cpu_node':nodes['cpu'],'local_decoder_pod':local,'remote_decoder_pod':remote,'transfers_per_request':1,'qualified_transfers_per_request':1,'worker_identities':ident,'checks':{k:True for k in ['direct_decode','local_pd','remote_pd','route_pins','transfer_counts','workers_stable']},'qualification':'prospective direct-parity and verified RDMA on both paths','suite_sha256':sha,'gold_accuracy':v['gold_accuracy'],'transfer_summary':transfers}
 m.write_json(run/'correctness-verified.json',marker)
 print('PASS RDMA and functional parity; starting timings',flush=True)
 r.run_measurements(rendered,nodes,inventory,run/'correctness-verified.json')
 m.write_json(root/'result.json',{'completed':True,'finished_unix':time.time()})
except Exception as e:
 m.write_json(root/'failure.json',{'error':str(e),'failed_unix':time.time()})
 try:r.failure_snapshot()
 except Exception:pass
 raise
finally:
 subprocess.run([sys.executable,str(HERE/'pilot-session.py'),'cleanup',str(run),'--execute'],check=False)
