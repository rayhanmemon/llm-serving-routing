#!/usr/bin/env python3
"""Assemble source-bound OFFLINE proof for the combined runner; never allocate."""
import ast
import argparse,hashlib,importlib.util,json,re,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent

def module(n):
 s=importlib.util.spec_from_file_location(n,HERE/(n+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def main():
 p=argparse.ArgumentParser()
 for n in ('out','workload','kubeconfig','charts','image','router-rehearsal'):p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False);r=module('run-router-session')
 tests=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(HERE),'-p','test_*.py'],capture_output=True,text=True,timeout=150)
 (a.out/'tests.log').write_text(tests.stdout+tests.stderr);tests.check_returncode()
 native=['docker','run','--rm','--network','none','--entrypoint','python3','-e','PYTHONPATH=/probe','-v',str(HERE)+':/probe:ro','-v',str(a.workload.resolve())+':/settings:ro','vllm/vllm-openai-cpu@sha256:527ec4e8188f2ad480aca5863ab3b7e7c39cfda84f6c0bbb06525363a3eb5a0f']
 for name,args in [('parser',['/probe/validate-tp4-args.py','--config','/settings/config.json']),('layout',['/probe/native-v029-layout-check.py']),('worker',['/probe/native-v029-worker-check.py']),('processes',['/probe/test_epoch_shutdown.py'])]:
  result=subprocess.run(native+args,capture_output=True,text=True,timeout=180)
  (a.out/(name+'.log')).write_text(result.stdout+result.stderr);result.check_returncode()
 k=['kubectl','--kubeconfig',str(a.kubeconfig),'--context','kind-router-session']
 state=json.loads(subprocess.check_output(k+['config','view','--minify','-o','json']))
 if not state['clusters'][0]['cluster']['server'].startswith('https://127.0.0.1:'):raise ValueError('Local API required')
 engine=r.code_manifest(a.workload/'config.json',{'local':'gpu-a-placeholder','remote':'gpu-b-placeholder'})
 client=r.client_manifest('cpu-placeholder',a.workload/'suite.json.gz',a.workload/'qualification.json.gz',a.workload/'plan.json')
 guard=r.s.paired.guard_manifest('cpu-placeholder',{'project_id':'project-u00k8gmbpr0067akfrxdah','cleanup_start_deadline_unix':9999999999},'cluster-placeholder')
 # Existing live synthetic Pods have different specs; validate real manifests under distinct names.
 for name,doc in [('engines',engine),('client',client),('guard',guard)]:
  (a.out/(name+'.json')).write_text(json.dumps(doc,indent=2))
  check=json.loads(json.dumps(doc))
  for x in check['items']:
   if x['kind']=='Pod':x['metadata']['name']='preflight-'+x['metadata']['name']
  result=subprocess.run(k+['apply','--server-side','--dry-run=server','-f','-'],input=json.dumps(check),capture_output=True,text=True,timeout=45)
  (a.out/(name+'-api.log')).write_text(result.stdout+result.stderr);result.check_returncode()
 # Validate exact image contents without entering its execute branch.
 archive_hash=hashlib.sha256(a.image.read_bytes()).hexdigest()
 validated=subprocess.run([sys.executable,str(HERE/'import-image.py'),'--context','kind-router-session','--node','unused','--archive',str(a.image),'--sha256',archive_hash],capture_output=True,text=True,timeout=30)
 validated.check_returncode();(a.out/'image-validation.json').write_text(json.dumps({'sha256':archive_hash,'archive_validated':True,'node_import_performed':False}))
 until=time.time()+900
 while not (a.router_rehearsal/'summary.json').exists():
  if time.time()>until:raise TimeoutError('Real-router rehearsal not complete; no proof issued')
  time.sleep(2)
 proof=json.loads((a.router_rehearsal/'summary.json').read_text())
 if not proof.get('real_router_rehearsed') or proof['recorded_requests']!=46 or proof.get('gpu_execution') is not False:raise ValueError('Wrong real-router rehearsal proof')
 if not proof.get('growing_log_snapshots_validated') or not proof.get('live_identity_checked_each_trial'):
  raise ValueError('Replay must validate growing-log snapshots and live identities on every trial')
 host_guard_revalidated=False
 for n in r.CODE:
  if proof['source_sha256'].get(n)==hashlib.sha256((HERE/n).read_bytes()).hexdigest():continue
  if n!='run-router-session.py':raise ValueError('Code changed after real-router rehearsal: '+n)
  # The local adapter substitutes job() to run AMD64 Docker outside ARM Kind.
  # A new Pod-identity check in that unexecuted method needs focused tests,
  # not a claim that the preceding Kind run executed it.
  original=json.loads((a.router_rehearsal/'paired/synthetic-workers.json').read_text())['items'][1]['data'][n]
  if hashlib.sha256(original.encode()).hexdigest()!=proof['source_sha256'][n]:raise ValueError('Unbound original source')
  def without_host_job(text):
   tree=ast.parse(text)
   for node in tree.body:
    if isinstance(node,ast.ClassDef) and node.name=='Controller':
     node.body=[x for x in node.body if not isinstance(x,ast.FunctionDef) or x.name not in ('job','live_identity')]
   return ast.dump(tree,include_attributes=False)
  if without_host_job(original)!=without_host_job((HERE/n).read_text()):raise ValueError('Executed router methods changed')
  host_guard_revalidated=True
 guard_test=subprocess.run([sys.executable,str(HERE/'rehearse-session-guard.py')],capture_output=True,text=True,timeout=90)
 (a.out/'guard-protocol.log').write_text(guard_test.stdout+guard_test.stderr);guard_test.check_returncode()
 files=[a.workload/n for n in ('config.json','suite.json.gz','qualification.json.gz','plan.json')]+[a.image]+[HERE/n for n in r.CODE]+sorted((HERE/'terraform-rdma').glob('*.tf'))
 record={k:True for k in ('combined_controller_rehearsed','real_router_rehearsed','native_perf_rehearsed','native_vllm_checked','manifests_validated','budget_bound_verified')}
 record.update(host_job_guard_revalidated_by_tests=host_guard_revalidated,cloud_actions=False,gpu_execution=False,tests_run=int(re.search(r'Ran (\d+) tests',tests.stderr).group(1)),router_rehearsal=proof,
  chart_sha256=r.chart_digest(a.charts),sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
  limits=['Cloud provisioning/cleanup are command/phase substitutions in tests, not a fresh provider qualification.',
          'Real router is ARM64 on Kind; native AMD64 benchmark runs in Docker via port forwards; GPU workers are synthetic.',
          'Input/output streams and actual routes/count gauges are verified; CUDA execution, RDMA, real load crossover and policy advantage remain unmeasured.'])
 (a.out/'preflight.json').write_text(json.dumps(record,indent=2)+'\n');print('PASS local combined-session preparation; no cloud or GPU run')

if __name__=='__main__':main()
