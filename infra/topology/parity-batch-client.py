import argparse,base64,hashlib,json,math,re,time,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--sha256',required=True);a=p.parse_args()
raw=Path(a.plan).read_bytes();assert hashlib.sha256(raw).hexdigest()==a.sha256;plan=json.loads(raw)
config=json.loads(Path('/results/endpoints.json').read_text());endpoints=config['endpoints'];pods=config['pods'];metrics_urls=config['metrics_urls']
failures=['vllm:nixl_num_failed_transfers_total','vllm:nixl_num_failed_notifications_total','vllm:nixl_num_kv_expired_reqs_total'];count='vllm:nixl_bytes_transferred_count';size='vllm:nixl_bytes_transferred_sum'
def snapshot():
 result={}
 for role,url in metrics_urls.items():
  with urllib.request.urlopen(url,timeout=10) as response:text=response.read().decode()
  values={}
  for line in text.splitlines():
   if line.startswith('#') or not line.strip():continue
   name=line.split('{',1)[0].split(' ',1)[0]
   if name in failures+[count,size,'vllm:num_requests_running','vllm:num_requests_waiting']:
    value=float(line.rsplit(' ',1)[1]);assert math.isfinite(value);values[name]=values.get(name,0)+value
  for name in failures+['vllm:num_requests_running','vllm:num_requests_waiting']:assert name in values,(role,name)
  if role!='prefill':
   for name in [count,size]:assert name in values,(role,name)
  result[role]=values
 return result
for case in plan['cases']:
 for route in plan['routes']:
  before=snapshot();assert all(v['vllm:num_requests_running']==0 and v['vllm:num_requests_waiting']==0 for v in before.values())
  run_id='confirm-'+case['case_id']+'-'+route+'-'+config['session_id'];headers={'Content-Type':'application/json','x-benchmark-run':run_id};requested=None
  if route in pods:
   requested=base64.b64encode(('topology-measurement/'+pods[route]+'-rank-0').encode()).decode();headers['x-benchmark-decoder']=requested
  body=case['request_body'];record={'case_id':case['case_id'],'route':route,'run_id':run_id,'request_body':body,'request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),'requested_decoder':requested,'selected_decoder':None,'status':None,'body':None,'error':None,'metrics_before':before}
  try:
   req=urllib.request.Request(endpoints[route]+'/v1/completions',data=json.dumps(body).encode(),headers=headers)
   with urllib.request.urlopen(req,timeout=90) as response:
    record['status']=response.status;record['selected_decoder']=response.headers.get('x-benchmark-decoder');record['body']=json.load(response)
   chosen='local' if route=='pd-local' else 'remote' if route=='pd-remote' else None
   for attempt in range(31):
    after=snapshot();deltas={role:after[role][count]-before[role][count] for role in ['local','remote']}
    assert all(delta<=int(role==chosen) for role,delta in deltas.items()),deltas
    complete=all(delta==int(role==chosen) for role,delta in deltas.items()) and all(v['vllm:num_requests_running']==0 and v['vllm:num_requests_waiting']==0 for v in after.values())
    if complete:break
    time.sleep(1)
   assert complete,('transfer or quiescence timeout',deltas)
   for role in metrics_urls:
    for name in failures:assert after[role][name]==before[role][name],(role,name)
   if chosen:assert after[chosen][size]>before[chosen][size]
   record['metrics_after']=after;record['transfer_checks_passed']=True
  except Exception as e:record['error']=str(e)
  print(json.dumps(record),flush=True)
  if record['error'] or record['status']!=200:raise SystemExit(1)
