from pathlib import Path
import argparse,importlib.util,json,shutil,tempfile,time
parser=argparse.ArgumentParser(description='Replay saved native trials through the real GPU verification branch without cloud access')
parser.add_argument('--synthetic-root',type=Path,required=True,help='Private saved 40-trial rehearsal root')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
src=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('runner',src/'run-router-session.py');r=importlib.util.module_from_spec(sp);sp.loader.exec_module(r)
repo=src.parent.parent
synthetic=args.synthetic_root/'paired'
plan=json.loads((repo/'workloads/router-session/plan.json').read_text())
_,architecture=r.s.tp4.load_config(repo/'workloads/router-session/config.json')
with tempfile.TemporaryDirectory(prefix='router-gpu-replay-') as temp:
 run=Path(temp);out=run/'paired';(out/'client/trials').mkdir(parents=True);shutil.copyfile(synthetic/'envoy-log.stdout',out/'envoy-log.stdout')
 c=object.__new__(r.Controller);c.run=run;c.out=out;c.ips={'local':'10.244.0.11','remote':'10.244.0.12'};c.gpu_checks=True;c.config_path=repo/'workloads/router-session/config.json';c.plan=plan;c.pins={'local':'local','remote':'remote'};c.url='http://router';c.metrics_url='http://router-metrics';c.session={'cleanup_start_deadline_unix':time.time()+36000};c.k=['kubectl'];c.policy='diagnostic'
 calls=[]
 c.drain=lambda:None;c.collect=lambda *a,**k:None;c.sleep=lambda n:None
 c.call=lambda *a,**k: (_ for _ in ()).throw(AssertionError('Unexpected external call during replay'))
 def picker(policy,params=None):c.policy=policy;calls.append({'policy':policy,'params':params})
 c.picker=picker
 def job(name,args,limit):
  origin=synthetic/'client/trials'/name
  if not (origin/'complete.json').exists():raise AssertionError('Missing completed native trial '+name)
  dst=out/'client/trials'/name;shutil.copytree(origin,dst)
  _,rows=r.result.records(dst);routes=json.loads((origin/'routes.json').read_text())
  (out/'envoy-log.stdout').write_text('\n'.join(json.dumps(entry) for entry in routes.values())+'\n')
  metric_names=[r.transfer.c.e.COUNT,r.transfer.c.e.TIME_COUNT,r.transfer.c.e.BYTES,*r.transfer.c.e.FAILURES]
  before={role:{'values':{name:0 for name in metric_names}} for role in c.ips}
  after=json.loads(json.dumps(before));counts={role:0 for role in c.ips}
  for row in rows:
   upstream=routes[row['request_key']]['upstream_host'];roles=[role for role,ip in c.ips.items() if upstream.startswith(ip+':')]
   if len(roles)!=1:raise AssertionError('Route address outside replay hosts')
   role=roles[0];counts[role]+=1;after[role]['values'][r.transfer.c.e.BYTES]+=r.transfer.c.settings.cache_bytes_per_rank(architecture,row['input_tokens'])*4
  for role,n in counts.items():
   after[role]['values'][r.transfer.c.e.COUNT]=after[role]['values'][r.transfer.c.e.TIME_COUNT]=4*n
  (dst/'metrics-before.json').write_text(json.dumps(before));(dst/'metrics-after.json').write_text(json.dumps(after))
  state=json.loads((dst/'requests.json').read_text())['trial'].get('state') or [0,0]
  pressured=state[0]>=r.s.tp4.load_config(repo/'workloads/router-session/config.json')[0]['max_num_seqs']
  samples=[]
  for row in rows:
   for when in ('start_perf_counter','first_token_perf_counter'):
    samples.append({'perf_counter':row[when],'unix':0,'workers':{'prefill':{'running':0,'waiting':0},'local':{'running':8 if pressured else 1,'waiting':1 if pressured else 0},'remote':{'running':0,'waiting':0}}})
  (dst/'engine-load.json').write_text(json.dumps(samples))
  calls.append({'trial':name,'requests':len(rows)})
 c.job=job
 c.evaluate()
 output={'gpu_execution':False,'scope':'Replay saved native synthetic client results through production gpu_checks=True validation with constructed transfer counters and load observations. No cloud or live GPU data; no performance claim.','source_trials':len(list((synthetic/'client/trials').glob('*/complete.json'))),'completed_trials':len(list((out/'client/trials').glob('*/transfer-verified.json'))),'queue_pressure_signal_observed':c.tuning.get('real_decoder_queue_observed'),'tradeoff_observed':c.tuning.get('tradeoff_observed'),'policy_restarts':len([x for x in calls if 'policy' in x]),'comparison':json.loads((run/'comparison-summary.json').read_text()) if (run/'comparison-summary.json').exists() else None,'call_counts':{'jobs':len([x for x in calls if 'trial' in x]),'picker':len([x for x in calls if 'policy' in x])}}
 print(json.dumps({k:v for k,v in output.items() if k!='comparison'},indent=2))
 if output['comparison']:print('comparison_complete',output['comparison']['complete'],'cells',len(output['comparison']['cells']))
 assert output['source_trials']==output['completed_trials']==40
 assert output['queue_pressure_signal_observed'] and output['tradeoff_observed']
 assert output['comparison'] and output['comparison']['complete'] and len(output['comparison']['cells'])==24
 assert all(json.loads(p.read_text())['validated'] for p in (out/'client/trials').glob('*/transfer-verified.json'))
 args.output.parent.mkdir(parents=True,exist_ok=True)
 args.output.write_text(json.dumps(output,indent=2)+'\n')
