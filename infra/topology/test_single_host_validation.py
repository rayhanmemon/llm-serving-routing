"""Replay recorded hardware evidence and reject corrupted controls offline."""
import importlib.util,json,tarfile,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location('validator',HERE/'validate-single-host.py')
v=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(v)
class ValidationTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  with tarfile.open(HERE.parent.parent/'results/2026-09-20-nvlink/evidence.tar.gz') as t:t.extractall(self.p,filter='data')
 def test_recorded_run(self):
  r=v.validate(self.p);self.assertTrue(r['nvlink_verified']);self.assertEqual(r['pd_transfers'],8);self.assertEqual(r['gold_matches'],8)
 def test_idle_traffic_rejected(self):
  p=self.p/'nvlink-idle-after.stdout';s=p.read_text();s=s.replace('Data Tx: 185763 KiB','Data Tx: 185764 KiB',1);p.write_text(s)
  with self.assertRaisesRegex(ValueError,'Idle NVLink'):v.validate(self.p)
 def test_missing_link_rejected(self):
  p=self.p/'nvlink-idle-before.stdout';p.write_text('\n'.join(p.read_text().splitlines()[2:]))
  with self.assertRaises(ValueError):v.validate(self.p)
 def test_changed_prompt_rejected(self):
  p=self.p/'responses.json';r=json.loads(p.read_text());r[0]['request']['prompt'][0]+=1;p.write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'Request mismatch'):v.validate(self.p)
 def test_bad_payload_match_rejected(self):
  p=self.p/'responses.json';r=json.loads(p.read_text());r[1]['after'][v.worker.SIZE]+=1024*1024;p.write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'NVLink bytes'):v.validate(self.p)
if __name__=='__main__':unittest.main()
