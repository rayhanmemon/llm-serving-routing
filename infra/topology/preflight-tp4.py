#!/usr/bin/env python3
"""Rehearse the exact TP4 sources and manifests against a loopback Kind API."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse

HERE=Path(__file__).resolve().parent
def module(name):
    sp=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--suite',type=Path,required=True)
    p.add_argument('--kubeconfig',type=Path,required=True);p.add_argument('--context',required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not a.context.startswith('kind-router-tp4-'):raise ValueError('Dedicated TP4 Kind context required')
    k=['kubectl','--kubeconfig',str(a.kubeconfig),'--context',a.context]
    raw=subprocess.run(k+['config','view','--minify','-o','json'],capture_output=True,text=True,check=True)
    server=json.loads(raw.stdout)['clusters'][0]['cluster']['server']
    if urllib.parse.urlsplit(server).hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('Preflight refuses a non-loopback Kubernetes API')
    a.out.mkdir(parents=True,exist_ok=False)
    s=module('run-tp4-staged');r=module('rehearse-tp4-client')
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(HERE),'-p','test_*.py'],capture_output=True,text=True)
    (a.out/'tests.log').write_text(result.stdout+result.stderr);result.check_returncode()
    cpu_image='vllm/vllm-openai-cpu@sha256:da36fc7267676e061270b3e8718cd723ddc47f7002fa02ede735aa779b2b94fc'
    native=subprocess.run(['docker','run','--rm','--network','none','--entrypoint','python3',
                          '-v',str(HERE)+':/probe:ro','-v',str(a.config.parent.resolve())+':/settings:ro',
                          cpu_image,'/probe/validate-tp4-args.py','--config','/settings/'+a.config.name],
                         capture_output=True,text=True,timeout=180)
    (a.out/'native-parser.log').write_text(native.stdout+native.stderr);native.check_returncode()
    rehearsal=r.rehearsal(a.suite,a.config,a.out/'http')
    engines=s.tp4.render(a.config,{'local':'tp4-local-placeholder','remote':'tp4-remote-placeholder'},7200)
    client=s.client_manifest('tp4-cpu-placeholder',a.suite)
    guard=s.paired.guard_manifest('tp4-cpu-placeholder',{'project_id':'project-u00k8gmbpr0067akfrxdah',
        'cleanup_start_deadline_unix':9999999999},'placeholder-cluster')
    init={'apiVersion':'v1','kind':'List','items':engines['items'][:2]+client['items'][:1]}
    subprocess.run(k+['apply','--server-side','-f','-'],input=json.dumps(init),text=True,check=True)
    for name,doc in [('engines',engines),('client',client),('guard',guard)]:
        (a.out/(name+'.json')).write_text(json.dumps(doc,indent=2)+'\n')
        subprocess.run(k+['apply','--server-side','--dry-run=server','-f','-'],input=json.dumps(doc),text=True,check=True)
    saved=json.loads(subprocess.run(k+['-n',s.tp4.NS,'get','configmap','tp4-requests','-o','json'],capture_output=True,text=True,check=True).stdout)
    if base64.b64decode(saved['binaryData']['suite.json.gz'])!=a.suite.read_bytes():raise ValueError('Frozen requests changed in API roundtrip')
    code=json.loads(subprocess.run(k+['-n',s.tp4.NS,'get','configmap','tp4-code','-o','json'],capture_output=True,text=True,check=True).stdout)
    if code['data']!=engines['items'][1]['data']:raise ValueError('Source changed in API roundtrip')
    files=[a.config,a.suite,*[HERE/name for name in s.CODE]]
    proof={'native_vllm_parser_passed':True,'full_http_rehearsal_passed':rehearsal['full_http_rehearsal_passed'],
           'manifests_server_validated':True,'request_configmap_roundtrip':True,'staged_controller_rehearsed':True,
           'cloud_actions':False,'sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
    (a.out/'preflight.json').write_text(json.dumps(proof,indent=2)+'\n')
    print('PASS complete local TP4 preflight: '+str(a.out/'preflight.json'),flush=True)


if __name__=='__main__':main()
