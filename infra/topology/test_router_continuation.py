"""Validate route-isolated calibration, actual result decisions, and completed telemetry."""
import importlib.util,json,tempfile,threading,time,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent

def load(n):
 s=importlib.util.spec_from_file_location(n,HERE/(n+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
r=load('run-router-session');p=load('router-session-plan');c=load('router-session-client')

class ContinuationTests(unittest.TestCase):
 def test_absolute_cap_matches_filter_then_topology_fallback(self):
  # Predicate semantics are from the actual utilization/topology Go filters.
  for local in range(26):
   for remote in range(26):
    for cap in p.GRID['cap']:
     kept=[role for role,count in [('local',local),('remote',remote)] if count<=cap]
     if not kept:kept=['local','remote']
     expected=['local'] if 'local' in kept else kept
     self.assertEqual(r.result.choose('absolute-cap',{'cap':cap},local,remote),expected)
 def test_route_episodes_are_balanced_and_have_no_competing_long_probe(self):
  import gzip
  plan=p.frozen_plan();trials=p.calibration_trials(plan);cases=json.loads(gzip.decompress((HERE.parent.parent/'workloads/router-session/suite.json.gz').read_bytes()))['cases']
  counts={'foreground':0,'background':0}
  for t in trials:
   rows=p.requests_for(plan,t,cases);fg=[x for x in rows if x['kind']=='foreground']
   self.assertEqual([x['at'] for x in fg],[3,6]);self.assertEqual({x['pin'] for x in fg},{t['route']})
   self.assertEqual(sum(len(x['body']['prompt'])==122880 for x in fg),1)
   for row in rows:counts[row['kind']]+=1
  self.assertEqual(counts,{'foreground':32,'background':240})
  self.assertEqual([t['route'] for t in trials[:2]],['local','remote'])
  self.assertEqual([t['route'] for t in trials[8:10]],['remote','local'])
 def test_actual_tuning_and_summary_success_inconclusive_and_incomplete(self):
  for outcome in ('complete','inconclusive','incomplete'):
   with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as tmp:
    obj=object.__new__(r.Controller);obj.run=Path(tmp);obj.plan=p.frozen_plan();obj.gpu_checks=False;obj.session={'cleanup_start_deadline_unix':time.time()+10000};seen=[]
    obj.collect=lambda:None;obj.picker=lambda *a:None
    def trial(t):
     seen.append(t);rows=[]
     if t['mode']=='calibration':
      for size in obj.plan['input_tokens']:
       route=t['route'];state=t['state'];ttft=(.1+.2*state[0]) if route=='local' else (.6+.2*state[1])
       rows.append({'kind':'foreground','input_tokens':size,'pin':route,'ttft':ttft if outcome!='inconclusive' else 1,'counts':dict(zip(('local','remote'),state))})
     else:
      rows=[{'kind':'foreground','ttft':.5 if t['policy']=='allowance' else 1} for _ in range(4)]+[{'kind':'background','duration':2,'maximum_chunk_gap':.01}]
      if outcome=='incomplete':obj.session['cleanup_start_deadline_unix']=time.time()+100
     return {'trial':t},rows
    obj.trial=trial;obj.evaluate()
    tuning=json.loads((obj.run/'frozen-tuning.json').read_text())
    if outcome=='inconclusive':
     self.assertFalse(tuning['tradeoff_observed']);self.assertEqual(len(seen),16);self.assertTrue((obj.run/'inconclusive.json').exists())
    else:
     summary=json.loads((obj.run/'comparison-summary.json').read_text());self.assertEqual(summary['complete'],outcome=='complete');self.assertEqual(summary['engineering_criterion_met'],outcome=='complete')
     if outcome=='complete':self.assertEqual(len(seen),40)
     else:self.assertTrue((obj.run/'incomplete.json').exists())
 def test_telemetry_export_waits_for_delayed_observer(self):
  with tempfile.TemporaryDirectory() as temp:
   stop=threading.Event();samples=[]
   def observe():time.sleep(.03);samples.append({'unix':1,'workers':{}})
   worker=threading.Thread(target=observe);worker.start();path=Path(temp)/'load.json'
   c.finish_observation(worker,stop,samples,path,timeout=1)
   self.assertFalse(worker.is_alive());self.assertEqual(json.loads(path.read_text()),samples)
 def test_stuck_observer_does_not_publish_complete_telemetry(self):
  with tempfile.TemporaryDirectory() as temp:
   stop=threading.Event();release=threading.Event();worker=threading.Thread(target=lambda:release.wait(1));worker.start();path=Path(temp)/'load.json'
   try:
    with self.assertRaises(TimeoutError):c.finish_observation(worker,stop,[],path,timeout=.01)
    self.assertFalse(path.exists())
   finally:release.set();worker.join()

if __name__=='__main__':unittest.main()
