import importlib.util
import json
from pathlib import Path
import signal
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('stop_active', Path(__file__).with_name('stop-active.py'))
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)

class StopActiveTest(unittest.TestCase):
    def test_empty_and_verified_sessions_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(stop.active_session(root))
            run = root / 'runs' / 'run1'
            run.mkdir(parents=True)
            (run / 'session.json').write_text(json.dumps({'session_id': 'run1'}))
            (root / 'budget.json').write_text(json.dumps({'attempts': [
                {'session_id': 'run1', 'status': 'active', 'run_dir': str(run)}]}))
            self.assertEqual(stop.active_session(root)[0], run.resolve())
            (run / 'cleanup-verified.json').write_text(json.dumps({'session_id': 'run1'}))
            self.assertIsNone(stop.active_session(root))

    def test_outside_session_store_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'budget.json').write_text(json.dumps({'attempts': [
                {'session_id': 'x', 'status': 'active', 'run_dir': '/unrelated/project'}]}))
            with self.assertRaisesRegex(ValueError, 'outside'):
                stop.active_session(root)

    def test_interrupts_launcher_once_and_preserves_guard_other_projects(self):
        run = Path('/owned/runs/run1')
        s = {'terraform_plan_path': '/owned/prepared.tfplan'}
        processes = '\n'.join([
            '100 python /code/pilot-session.py --execute --plan /owned/prepared.tfplan',
            '101 terraform -chdir=/owned apply /owned/prepared.tfplan',
            '102 python /code/pilot-session.py guard /owned/runs/run1',
            '103 python /code/run-serving.py --run-dir /owned/runs/run1',
            '104 terraform -chdir=/other apply /other/plan',
            '105 python /code/pilot-session.py cleanup /owned/runs/run1 --execute',
        ])
        self.assertCountEqual(stop.processes_to_stop(processes, run, s),
                              [(100, signal.SIGINT), (103, signal.SIGTERM)])

if __name__ == '__main__':
    unittest.main()
