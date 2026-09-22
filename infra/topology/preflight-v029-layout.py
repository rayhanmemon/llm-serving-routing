#!/usr/bin/env python3
"""Validate the v0.29 layout fixture locally before any paid admission."""
import argparse,base64,hashlib,importlib.util,json,re,subprocess,sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
CPU_IMAGE='vllm/vllm-openai-cpu@sha256:527ec4e8188f2ad480aca5863ab3b7e7c39cfda84f6c0bbb06525363a3eb5a0f'

def module(name):
 s=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--suite',type=Path,required=True)
 p.add_argument('--kubeconfig',type=Path,required=True);p.add_argument('--context',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--lifecycle-record',type=Path,required=True);a=p.parse_args()
 if not a.context.startswith('kind-router-tp4-'):raise ValueError('Dedicated Kind context required')
 k=['kubectl','--kubeconfig',str(a.kubeconfig),'--context',a.context]
 state=json.loads(subprocess.check_output(k+['config','view','--minify','-o','json']))
 if not state['clusters'][0]['cluster']['server'].startswith('https://127.0.0.1:'):raise ValueError('Loopback API required')
 a.out.mkdir(parents=True,exist_ok=False);r=module('run-layout-local');rehearsal=module('rehearse-layout-client')
 tests=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(HERE),'-p','test_*.py'],capture_output=True,text=True)
 (a.out/'tests.log').write_text(tests.stdout+tests.stderr);tests.check_returncode()
 replay=subprocess.run([sys.executable,str(HERE/'test_layout_workflow.py')],capture_output=True,text=True,timeout=90)
 (a.out/'controller-failures.log').write_text(replay.stdout+replay.stderr);replay.check_returncode()
 native=['docker','run','--rm','--network','none','--entrypoint','python3','-e','PYTHONPATH=/probe','-v',str(HERE)+':/probe:ro','-v',str(a.config.parent.resolve())+':/settings:ro',CPU_IMAGE]
 commands=[('native-parser',['/probe/validate-tp4-args.py','--config','/settings/'+a.config.name]),
           ('native-layout',['/probe/native-v029-layout-check.py']),('native-worker',['/probe/native-v029-worker-check.py']),
           ('native-shutdown',['/probe/test_epoch_shutdown.py'])]
 for name,args in commands:
  result=subprocess.run(native+args,capture_output=True,text=True,timeout=180)
  (a.out/(name+'.log')).write_text(result.stdout+result.stderr);result.check_returncode()
 result=rehearsal.rehearsal(a.suite,a.config,a.out/'http')
 if result['requests']!=104:raise ValueError('Four-epoch HTTP rehearsal incomplete')
 qualification=rehearsal.rehearsal(a.suite,a.config,a.out/'qualification-http','qualification')
 if qualification['requests']!=16:raise ValueError('Short qualification HTTP rehearsal incomplete')
 life=json.loads(a.lifecycle_record.read_text())
 if not life.get('restart_lifecycle_passed') or life['epochs']!=4 or not life.get('both_supervisors_real'):raise ValueError('Both real supervisors must pass four-epoch lifecycle proof')
 for name in ('layout-engines.py','tp4-engines.py','rehearse-layout-lifecycle.py'):
  if life['sha256'].get(name)!=hashlib.sha256((HERE/name).read_bytes()).hexdigest():raise ValueError('Stale lifecycle proof: '+name)
 (a.out/'lifecycle.json').write_text(json.dumps(life,indent=2)+'\n')
 engine=r.manifest(a.config,{'local':'v029-local-placeholder'});client=r.s.client_manifest('v029-cpu-placeholder',a.suite)
 guard=r.s.paired.guard_manifest('v029-cpu-placeholder',{'project_id':'project-u00k8gmbpr0067akfrxdah','cleanup_start_deadline_unix':9999999999},'placeholder-cluster')
 init={'apiVersion':'v1','kind':'List','items':engine['items'][:2]+client['items'][:1]}
 subprocess.run(k+['apply','--server-side','-f','-'],input=json.dumps(init),text=True,check=True)
 for name,doc in [('engine',engine),('client',client),('guard',guard)]:
  (a.out/(name+'.json')).write_text(json.dumps(doc,indent=2)+'\n');subprocess.run(k+['apply','--server-side','--dry-run=server','-f','-'],input=json.dumps(doc),text=True,check=True)
 code=json.loads(subprocess.check_output(k+['-n',r.s.tp4.NS,'get','configmap','tp4-code','-o','json']))
 if code['data']!=engine['items'][1]['data']:raise ValueError('Mounted source differs')
 data=json.loads(subprocess.check_output(k+['-n',r.s.tp4.NS,'get','configmap','tp4-requests','-o','json']))
 if base64.b64decode(data['binaryData']['suite.json.gz'])!=a.suite.read_bytes():raise ValueError('Requests differ')
 files=[a.config,a.suite,*[HERE/n for n in r.CODE],*sorted((HERE/'terraform-rdma').glob('*.tf'))]
 proof={key:True for key in ('native_vllm_parser_passed','native_v2_layout_resolution_passed','native_cpu_allocation_passed','full_http_rehearsal_passed','qualification_http_rehearsal_passed','manifests_server_validated','layout_controller_rehearsed','restart_lifecycle_passed','controller_failure_replay_passed')}
 proof.update(gpu_execution=False,cloud_actions=False,tests_passed=int(re.search(r'Ran (\d+) tests',tests.stderr).group(1)),sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in files})
 (a.out/'preflight.json').write_text(json.dumps(proof,indent=2)+'\n');print('PASS v0.29 local preparation; no GPU result')

if __name__=='__main__':main()
