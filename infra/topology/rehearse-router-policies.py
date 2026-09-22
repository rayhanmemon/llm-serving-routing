#!/usr/bin/env python3
"""Exercise the published router against synthetic HTTP workers on dedicated Kind."""
import argparse,concurrent.futures,json,socket,subprocess,sys,time,urllib.request
from pathlib import Path
import yaml

HERE=Path(__file__).resolve().parent

def run(kubeconfig,context,charts,rendered,out):
 if not context.startswith('kind-router-'):raise ValueError('Dedicated local rehearsal context required')
 k=['kubectl','--kubeconfig',str(kubeconfig),'--context',context,'--request-timeout=20s']
 state=json.loads(subprocess.check_output(k+['config','view','--minify','-o','json']))
 if not state['clusters'][0]['cluster']['server'].startswith('https://127.0.0.1:'):raise ValueError('Local API only')
 out.mkdir(parents=True,exist_ok=False);results=[]
 def query(n,policy):
  body={'model':'Qwen/Qwen3-32B','prompt':[101]*122880,'max_tokens':32,'temperature':0,'stream':True,'stream_options':{'include_usage':True}}
  req=urllib.request.Request('http://127.0.0.1:18881/v1/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','x-benchmark-run':'rehearsal-'+policy})
  with urllib.request.urlopen(req,timeout=45) as response:raw=response.read().decode();status=response.status
  if status!=200 or '[DONE]' not in raw:raise ValueError('Incomplete stream')
  route='local' if 'decode-local' in raw else 'remote' if 'decode-remote' in raw else None
  if not route or (policy=='hard' and route!='local'):raise ValueError('Wrong route')
  return {'request':n,'policy':policy,'status':status,'decoder':route,'stream_complete':True}
 for policy in ('hard','none','soft','absolute-cap','allowance'):
  rendered_yaml=subprocess.check_output(['helm','template','topology',str(charts),'-n','router-tp4','-f',str(rendered/f'router-{policy}.values.yaml')],text=True)
  modified=subprocess.run([sys.executable,str(HERE/'record-routes.py')],input=rendered_yaml,capture_output=True,text=True,check=True).stdout
  (out/f'{policy}-manifest.yaml').write_text(modified)
  subprocess.run(k+['apply','-f','-'],input=modified,text=True,check=True,stdout=subprocess.DEVNULL)
  subprocess.run(k+['-n','router-tp4','rollout','restart','deploy/topology-epp'],check=True,stdout=subprocess.DEVNULL)
  subprocess.run(k+['-n','router-tp4','rollout','status','deploy/topology-epp','--timeout=120s'],check=True,stdout=subprocess.DEVNULL)
  with (out/f'{policy}-portforward.log').open('w') as log:
   pf=subprocess.Popen(k+['-n','router-tp4','port-forward','svc/topology-epp','18881:80','--address','127.0.0.1'],stdout=log,stderr=subprocess.STDOUT)
   try:
    for _ in range(60):
     try:
      with socket.create_connection(('127.0.0.1',18881),timeout=.3):break
     except OSError:time.sleep(.3)
    else:raise TimeoutError('Local service forwarding unavailable')
    # Startup health does not imply endpoint discovery is published. One bounded probe precedes the recorded batch.
    for attempt in range(20):
     try:query(-1,policy);break
     except urllib.error.HTTPError:
      if attempt==19:raise
      time.sleep(.5)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:batch=list(pool.map(lambda n:query(n,policy),range(8)))
    results.extend(batch)
   finally:pf.terminate();pf.wait(timeout=10)
  logs=subprocess.check_output(k+['-n','router-tp4','logs','deploy/topology-epp','-c','epp'],text=True)
  parsed=[]
  for line in logs.splitlines():
   try:r=json.loads(line)
   except ValueError:continue
   if r.get('body') in ('Active request counts','parsed config','Sending request header response'):parsed.append(r)
  (out/f'{policy}-routing.json').write_text(json.dumps(parsed,indent=2)+'\n')
  if not any(x.get('body')=='Active request counts' and x.get('maxCount',0)>0 for x in parsed):raise ValueError('No observed in-flight load during concurrent rehearsal')
  print(json.dumps({'policy':policy,'requests':len(batch),'routes':[x['decoder'] for x in batch],'inflight_observed':True}),flush=True)
  (out/'requests.json').write_text(json.dumps(results,indent=2)+'\n')
 summary={'published_head':'0217d29924ba93b90f952e7a0281dd8dda146703','policies':5,'recorded_requests':len(results),'all_streams_complete':True,'real_epp':True,'real_sidecar':True,'synthetic_engines':True,'gpu_performance':False,'parameters_calibrated':False}
 (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');return summary

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--kubeconfig',type=Path,required=True);p.add_argument('--context',required=True);p.add_argument('--charts',type=Path,required=True);p.add_argument('--rendered',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
 a=p.parse_args();print(json.dumps(run(a.kubeconfig,a.context,a.charts,a.rendered,a.out)))
