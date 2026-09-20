"""Replay the production orchestrator against saved evidence; no cloud RPCs.

Local rendering and validation execute for real. GPU execution, cloud lifecycle
and the timing workload are explicitly substituted; this proves no NVLink path.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent.parent / 'results/2026-09-16-h200-rdma-serving'
SPEC = importlib.util.spec_from_file_location('rdma_driver', HERE / 'run-rdma-serving.py')
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)
serving = driver.m


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.saved = self.root / 'saved'
        with tarfile.open(EVIDENCE / 'evidence.tar.gz') as archive:
            archive.extractall(self.saved, filter='data')
        shutil.copy2(EVIDENCE / 'suite.json', self.run / 'frozen-suite.json')
        shutil.copy2(EVIDENCE / 'suite.sha256', self.run / 'frozen-suite.sha256')
        (self.run / 'session.json').write_text(json.dumps({
            'session_id': 'offline-replay', 'profile': 'rdma-h200-serving-retry',
            'cleanup_start_deadline_unix': time.time() + 600,
        }))
        self.events = []
        self.mode = 'pass'
        self.inventory = json.loads((self.saved / 'before/pods.json').read_text())['items']
        models = {p['metadata']['labels'].get('app.kubernetes.io/name'): p
                  for p in self.inventory}
        self.local = models['decode-local']['metadata']['name']
        self.remote = models['decode-remote']['metadata']['name']
        self.nodes = {
            'local': models['prefill']['spec']['nodeName'],
            'remote': models['decode-remote']['spec']['nodeName'],
            'cpu': next(p['spec']['nodeName'] for p in self.inventory
                        if 'topology-epp-' in p['metadata']['name']),
        }
        self.responses = (self.saved / 'responses.jsonl').read_text()

    def execute(self):
        case = self

        class ReplayRunner(serving.Runner):
            def wait_apply(self):
                case.events.append('provisioning-substitute')
            def terraform_outputs(self):
                return 'offline-cluster', {}
            def fetch_kubeconfig(self, cluster):
                self.context_ready = True
            def wait_nodes(self, groups):
                return case.nodes
            def apply_models_and_router(self, rendered, cpu):
                case.events.append('deployment-substitute')
                if case.mode == 'startup-error':
                    raise serving.ServingError('injected startup failure')
            def wait_serving(self, nodes):
                return {'local_decoder': case.local, 'remote_decoder': case.remote}
            def deploy_correctness_client(self, cpu):
                pass
            def inventory(self):
                return case.inventory
            def command(self, command, **kwargs):
                name = Path(command[1]).name
                if name == 'collect.py':
                    destination = Path(command[command.index('--out') + 1])
                    shutil.copytree(case.saved / destination.name, destination)
                    case.events.append('saved-' + destination.name)
                    return subprocess.CompletedProcess(command, 0, '', '')
                if name not in ('render.py', 'known-answer-suite.py'):
                    raise AssertionError('Unexpected process: ' + repr(command))
                # These exact CLI boundaries execute without mocks.
                case.events.append('real-' + name)
                return super().command(command, **kwargs)
            def kubectl(self, arguments, **kwargs):
                if 'logs' in arguments:
                    pod = arguments[arguments.index('logs') + 1]
                    logs = (case.saved / 'after' / (pod + '-modelserver.log')).read_text()
                    if case.mode == 'wrong-transport':
                        logs = logs.replace('rc_mlx5', 'tcp')
                    return subprocess.CompletedProcess(arguments, 0, logs, '')
                if 'exec' in arguments and '/results/client.py' in arguments:
                    rows = case.responses
                    if case.mode == 'parity-error':
                        values = [json.loads(x) for x in rows.splitlines()]
                        values[0]['body']['choices'][0]['text'] = 'deliberately different'
                        rows = '\n'.join(json.dumps(x) for x in values)
                    case.events.append('saved-responses')
                    return subprocess.CompletedProcess(arguments, 0, rows, '')
                if 'exec' in arguments and '-i' in arguments:
                    return subprocess.CompletedProcess(arguments, 0, '', '')
                raise AssertionError('Unexpected Kubernetes call: ' + repr(arguments))
            def run_measurements(self, rendered, nodes, inventory, marker):
                record = json.loads(marker.read_text())
                assert record['validated'] and all(record['checks'].values())
                assert (case.run / 'parity/protocol.json').is_file()
                case.events.append('timing-substitute')
                if case.mode == 'timing-error':
                    raise serving.ServingError('injected timing failure')
            def failure_snapshot(self):
                case.events.append('failure-evidence-substitute')

        args = ['--run-dir', str(self.run), '--chart-path', str(self.root),
                '--epp-archive', str(self.root / 'unused.tar'),
                '--epp-sha256', '0' * 64, '--execute']
        # Real image-archive verification has separate tests; no image is loaded here.
        with patch.object(serving, 'Runner', ReplayRunner), patch.object(serving, 'verify_archive'):
            driver.main(args, cleanup=lambda run: self.events.append('cleanup-substitute'))

    def test_saved_run_reaches_timings_and_cleanup(self):
        self.execute()
        self.assertEqual(self.events.count('cleanup-substitute'), 1)
        self.assertEqual(self.events[-2:], ['timing-substitute', 'cleanup-substitute'])
        self.assertTrue((self.run / 'suite.sha256').exists())
        self.assertTrue(json.loads((self.run / 'serving/result.json').read_text())['completed'])

    def test_existing_render_preserved_and_does_not_abort_run(self):
        prior = self.run / 'rendered'
        prior.mkdir()
        (prior / 'old-evidence').write_text('preserve')
        self.execute()
        backups = list(self.run.glob('rendered-before-*'))
        self.assertEqual((backups[0] / 'old-evidence').read_text(), 'preserve')
        self.assertIn('timing-substitute', self.events)

    def test_failures_do_not_claim_success_and_still_cleanup(self):
        for mode in ('startup-error', 'parity-error', 'wrong-transport', 'timing-error'):
            with self.subTest(mode=mode):
                # Each attempt uses an isolated run directory.
                for name in ('serving', 'parity', 'rendered'):
                    shutil.rmtree(self.run / name, ignore_errors=True)
                (self.run / 'correctness-verified.json').unlink(missing_ok=True)
                self.events.clear()
                self.mode = mode
                with self.assertRaises((serving.ServingError, AssertionError)):
                    self.execute()
                self.assertEqual(self.events[-1], 'cleanup-substitute')
                self.assertEqual(self.events.count('cleanup-substitute'), 1)
                self.assertFalse((self.run / 'serving/result.json').exists())
                self.assertTrue((self.run / 'serving/failure.json').exists())
                if mode != 'timing-error':
                    self.assertNotIn('timing-substitute', self.events)

    def test_changed_suite_rejected_before_deployment(self):
        with (self.run / 'frozen-suite.json').open('a') as f:
            f.write(' ')
        with self.assertRaises(serving.ServingError):
            self.execute()
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
