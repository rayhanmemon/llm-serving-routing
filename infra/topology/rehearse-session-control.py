#!/usr/bin/env python3
"""Real local Kubernetes job and deadline-file RPCs; provider SDK is substituted."""
import argparse,importlib.util,json,os,subprocess,time
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(name):
 s=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def main():
 p=argparse.ArgumentParser();p.add_argument('--kubeconfig',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 r=load('run-router-session');fake=load('rehearse-session-guard');k=['kubectl','--kubeconfig',str(a.kubeconfig),'--context','kind-router-session','--request-timeout=20s']
 state=json.loads(subprocess.check_output(k+['config','view','--minify','-o','json']))
 if not state['clusters'][0]['cluster']['server'].startswith('https://127.0.0.1:'):raise ValueError('Local API only')
 a.out.mkdir(parents=True,exist_ok=False);obj=object.__new__(r.Controller);obj.run=a.out;obj.out=a.out/'rpc';obj.out.mkdir();obj.k=k;obj.rpc=subprocess.run;obj.sleep=time.sleep
 obj.env={**os.environ,'KUBECONFIG':str(a.kubeconfig)};obj.policy='allowance';obj.cluster='cluster'
 obj.session={'cleanup_start_deadline_unix':time.time()+1800,'deletion_target_unix':time.time()+3000}
 pods=json.loads(subprocess.check_output(k+['-n','router-tp4','get','pods','-o','json']))['items']
 obj.ips={x['metadata']['name']:x['status']['podIP'] for x in pods if x['metadata']['name'] in ('local','remote')}
 obj.job('native-job-control',['python3','-c','print("bounded job executed")'],15)
 # Same guard code, actual Pod/emptyDir/kubectl exec, substituted SDK.
 bootstrap='from pathlib import Path;import sys,time;Path("/results/groups.json").write_text(\'["router-cpu","router-local"]\');sys.argv=["fake",str(time.time()+1800)];exec('+repr(fake.FAKE)+')'
 doc={'apiVersion':'v1','kind':'Pod','metadata':{'name':'deadline-guard','namespace':'router-tp4'},'spec':{'restartPolicy':'Never','automountServiceAccountToken':False,'activeDeadlineSeconds':180,'containers':[{'name':'guard','image':'assigned-from-reviewed-renderer-below','command':['python3','-u','-c',bootstrap],'volumeMounts':[{'name':'results','mountPath':'/results'},{'name':'code','mountPath':'/probe','readOnly':True}]}],'volumes':[{'name':'results','emptyDir':{}},{'name':'code','configMap':{'name':'tp4-code'}}]}}
 # Use the already verified image spelling from the production CPU guard renderer.
 doc['spec']['containers'][0]['image']=r.s.paired.guard_manifest('unused',{'project_id':'p','cleanup_start_deadline_unix':1},'c')['items'][-1]['spec']['containers'][0]['image']
 obj.apply(doc,'fake-cloud-guard')
 try:
  obj.call(k+['-n','router-tp4','wait','--for=condition=Ready','pod/deadline-guard','--timeout=45s'],'guard-ready',55)
  until=time.time()+30
  while True:
   first=obj.call(k+['-n','router-tp4','exec','deadline-guard','--','cat','/results/guard-ready.json'],'initial-ack',10,check=False)
   if first.returncode==0:break
   if time.time()>until:
    logs=obj.call(k+['-n','router-tp4','logs','deadline-guard'],'guard-startup-log',10,check=False)
    raise RuntimeError('Guard initial acknowledgement missing: '+logs.stdout)
   time.sleep(.5)
  shortened={'cleanup_start_deadline_unix':time.time()+600,'deletion_target_unix':time.time()+1800}
  obj.reduce_deadline(shortened)
  summary={'local_kubernetes_control_rehearsed':True,'real_job_launch_poll_and_identity':True,'real_deadline_write_and_ack_rpc':True,'provider_sdk_substituted':True,'cloud_actions':False}
  (a.out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
 finally:subprocess.run(k+['-n','router-tp4','delete','pod','deadline-guard','--wait=false'],capture_output=True,timeout=20)

if __name__=='__main__':main()
