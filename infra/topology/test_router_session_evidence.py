"""Evidence falsification controls for the combined runner."""
import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(n):
 s=importlib.util.spec_from_file_location(n,HERE/(n+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
r=load('router-session-results');t=load('router-session-transfer');p=load('router-session-plan')

class EvidenceTests(unittest.TestCase):
 def test_transfer_rejects_recomputation_missing_rank_and_errors(self):
  cfg,arch=t.c.settings.load_config(HERE.parent.parent/'workloads/router-session/config.json')
  metrics={n:0 for n in (t.c.e.COUNT,t.c.e.TIME_COUNT,t.c.e.BYTES,*t.c.e.FAILURES)}
  before={x:{'values':dict(metrics)} for x in ('prefill','local','remote')};after=copy.deepcopy(before)
  rows=[{'request_key':'a','input_tokens':4096},{'request_key':'b','input_tokens':122880}]
  routes={'a':{'upstream_host':'10.0.0.1:8000'},'b':{'upstream_host':'10.0.0.2:8000'}};ips={'local':'10.0.0.1','remote':'10.0.0.2'}
  for role,n in [('local',4096),('remote',122880)]:
   after[role]['values'].update({t.c.e.COUNT:4,t.c.e.TIME_COUNT:4,t.c.e.BYTES:t.c.settings.cache_bytes_per_rank(arch,n)*4})
  self.assertTrue(t.verify(before,after,rows,routes,ips,arch)['validated'])
  for metric,value in [(t.c.e.COUNT,3),(t.c.e.BYTES,1),(t.c.e.FAILURES[0],1)]:
   bad=copy.deepcopy(after);bad['local']['values'][metric]=value
   with self.assertRaises(ValueError):t.verify(before,bad,rows,routes,ips,arch)
 def test_training_complete_and_same_data_for_every_policy(self):
  plan=p.frozen_plan();train=[]
  for repeat in range(2):
   for state in plan['training_states']:
    rows=[]
    for size in plan['input_tokens']:
     for route in ('local','remote'):
      rows.append({'kind':'foreground','input_tokens':size,'pin':route,'ttft':(.1+.2*state[0]) if route=='local' else (.6+.2*state[1]),'counts':dict(zip(('local','remote'),state))})
    train.append(({'trial':{'state':state,'repeat':repeat}},rows))
  tuned=r.tune(train,plan);self.assertTrue(tuned['tradeoff_observed']);self.assertEqual(set(tuned['parameters']),set(p.POLICIES))
  with self.assertRaises(ValueError):r.tune(train[:-1],plan)
 def test_prefill_queue_or_proxy_counts_are_not_decoder_saturation(self):
  self.assertFalse(r.decoder_queue_observed([{'workers':{'prefill':{'running':8,'waiting':20},'local':{'running':3,'waiting':0}}}],8))
  self.assertTrue(r.decoder_queue_observed([{'workers':{'local':{'running':8,'waiting':1}}}],8))
 def test_route_order_alone_does_not_pass_crossover(self):
  plan=p.frozen_plan();training=[]
  for repeat in range(2):
   for state in plan['training_states']:
    rows=[]
    for size in plan['input_tokens']:
     for route in ('local','remote'):
      rows.append({'kind':'foreground','input_tokens':size,'pin':route,'ttft':1 if (route=='local')==(repeat==0) else 2,'counts':{'local':17,'remote':8}})
    training.append(({'trial':{'state':state,'repeat':repeat}},rows))
  tuned=r.tune(training,plan)
  self.assertFalse(tuned['tradeoff_observed']);self.assertTrue(all(x==0 for x in tuned['route_cost_contrasts_seconds']))
 def test_incomplete_evaluation_cannot_claim_success(self):
  self.assertFalse(r.summarize([] ,{'reference':'soft'},p.frozen_plan())['engineering_criterion_met'])
 def test_other_users_regression_invalidates_win(self):
  plan=p.frozen_plan();tuning={'reference':'soft'};trials=[]
  for x in plan['trials']:
   policy=x['policy'];rows=[]
   for i in range(4):rows.append({'kind':'foreground','ttft':.5 if policy=='allowance' else 1})
   rows.append({'kind':'background','duration':2 if policy=='allowance' else 1,'maximum_chunk_gap':.01})
   trials.append(({'trial':x},rows))
  self.assertFalse(r.summarize(trials,tuning,plan)['engineering_criterion_met'])

if __name__=='__main__':unittest.main()
