import gzip,importlib.util,json,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('prepare',HERE/'prepare-v029-paired.py');p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
BASE=HERE.parent.parent/'workloads/tp4-v029'

class PairedPreparationTests(unittest.TestCase):
 def fixture(self,root):
  c=root/'paired/client/packed';e=root/'paired/local/packed';c.mkdir(parents=True);e.mkdir(parents=True)
  (root/'session.json').write_text(json.dumps({'profile':'tp4-h200-layout-local','session_id':'synthetic-unit-test'}))
  (c/'complete.json').write_text(json.dumps({'requests':26,'parity':[{'matches':True}]*4,'transport':{str(i):{} for i in range(4)}}))
  (c/'final-evidence.json').write_text(json.dumps({'local':{'layout_probes':{str(i):{'use_v2':True,'layout':'BHLNC'} for i in range(8)}}}))
  cfg=json.loads((BASE/'config.json').read_text());cfg['kv_cache_layout']='BHLNC';(e/'effective-config.json').write_text(json.dumps(cfg));return c,e
 def test_balances_each_case_and_route_order_without_claiming_remote_qualification(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'run';self.fixture(root);out=Path(d)/'prepared';proof=p.prepare(root,'packed',out)
   self.assertFalse(proof['remote_gpu_qualified']);suite=json.loads(gzip.decompress((out/'suite.json.gz').read_bytes()))
   self.assertEqual(len(suite['cases']),4);self.assertEqual(len(suite['pairs']),8)
   for case in suite['cases']:
    rows=[x for x in suite['pairs'] if x['case_id']==case['id']]
    self.assertEqual({tuple(x['order']) for x in rows},{('local','remote'),('remote','local')})
   for block in (0,1):
    rows=[x for x in suite['pairs'] if x['block']==block]
    self.assertEqual(sum(x['order'][0]=='local' for x in rows),2)
 def test_rejects_missing_qualification_or_layout_disagreement(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'run';c,e=self.fixture(root)
   cfg=json.loads((e/'effective-config.json').read_text());cfg['kv_cache_layout']='LBHNC';(e/'effective-config.json').write_text(json.dumps(cfg))
   with self.assertRaisesRegex(ValueError,'layout proof'):p.prepare(root,'packed',Path(d)/'out')
   (c/'complete.json').unlink()
   with self.assertRaises(FileNotFoundError):p.prepare(root,'packed',Path(d)/'out')
