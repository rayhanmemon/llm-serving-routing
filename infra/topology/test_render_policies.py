import base64
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import render
import workload


class PolicyConfigTest(unittest.TestCase):
    def config(self, policy, **overrides):
        values = {'allowance': 3, 'weight': 0.75, 'absolute_cap': 4,
                  'absolute_cap_fallback_on_empty': True}
        values.update(overrides)
        return render.epp_config(policy, **values)

    @staticmethod
    def plugin(config, name):
        return next(p for p in config['plugins'] if p.get('name', p['type']) == name)

    @staticmethod
    def decode_refs(config):
        return [p['pluginRef'] for p in next(p for p in config['schedulingProfiles']
                                              if p['name'] == 'decode')['plugins']]

    def test_diagnostic_pin_is_isolated_from_evaluated_policies(self):
        diagnostic = self.config('diagnostic')
        self.assertEqual(self.plugin(diagnostic, 'diagnostic-pin')['type'], 'session-affinity-filter')
        self.assertIn('diagnostic-pin', self.decode_refs(diagnostic))

        for policy in render.EVALUATED_POLICIES:
            with self.subTest(policy=policy):
                config = self.config(policy)
                self.assertNotIn('diagnostic-pin', self.decode_refs(config))
                self.assertFalse(any(p['type'] == 'session-affinity-filter' for p in config['plugins']))

    def test_absolute_cap_precedes_topology_and_is_explicit(self):
        config = self.config('absolute-cap', absolute_cap=7,
                             absolute_cap_fallback_on_empty=False)
        plugin = self.plugin(config, 'absolute-active-request-cap')
        self.assertEqual(plugin['type'], 'utilization-filter')
        self.assertEqual(plugin['parameters'], {
            'conditions': [{'metric': 'active-requests', 'maxValue': 7}],
            'fallbackOnEmpty': False,
        })
        refs = self.decode_refs(config)
        self.assertLess(refs.index('absolute-active-request-cap'), refs.index('topology-affinity-filter'))
        self.assertLess(refs.index('topology-affinity-filter'), refs.index('active-request-scorer'))

    def test_tunable_policy_parameters_are_rendered(self):
        allowance = self.config('allowance', allowance=9)
        self.assertEqual(self.plugin(allowance, 'topology-affinity-filter')['parameters']['loadAllowance'], 9)
        soft = self.config('soft', weight=1.25)
        soft_ref = next(p for p in next(p for p in soft['schedulingProfiles'] if p['name'] == 'decode')['plugins']
                        if p['pluginRef'] == 'topology-affinity-scorer')
        self.assertEqual(soft_ref['weight'], 1.25)

    def test_invalid_policy_values_fail(self):
        for policy, kwargs in [('missing', {}), ('allowance', {'allowance': -1}),
                               ('soft', {'weight': -0.1}), ('soft', {'weight': float('nan')}),
                               ('absolute-cap', {'absolute_cap': -1})]:
            with self.subTest(policy=policy, kwargs=kwargs), self.assertRaises(ValueError):
                self.config(policy, **kwargs)


class RenderedFilesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name) / 'rendered'
        subprocess.run([
            sys.executable, str(Path(render.__file__)), '--local-node', 'local',
            '--remote-node', 'remote', '--cpu-node', 'cpu', '--allowance', '5',
            '--soft-weight', '0.8', '--absolute-cap', '6', '--out', str(self.out),
        ], check=True, capture_output=True, text=True)

    def read(self, name):
        return yaml.safe_load((self.out / name).read_text())

    def test_first48_and_policy_contract(self):
        for tokens in (512, 8192):
            benchmark = self.read(f'benchmark-{tokens}.yaml')
            warmup = self.read(f'warmup-{tokens}.yaml')
            self.assertEqual(benchmark['data']['input_distribution']['mean'], tokens)
            self.assertEqual(benchmark['data']['output_distribution']['mean'], 128)
            self.assertEqual(benchmark['load']['stages'], [{'num_requests': 12, 'concurrency_level': 1}])
            self.assertEqual(warmup['load']['stages'], [{'num_requests': 1, 'concurrency_level': 1}])
        for policy in render.POLICIES:
            self.assertTrue((self.out / f'epp-{policy}.yaml').is_file())
            self.assertTrue((self.out / f'router-{policy}.values.yaml').is_file())

    def test_calibration_reaches_both_busy_candidate_levels(self):
        for tokens in (512, 8192):
            load = self.read(f'calibration-{tokens}.yaml')['load']
            stages = load['stages']
            self.assertEqual(load['num_workers'], 1)
            self.assertEqual([s['concurrency_level'] for s in stages], [1, 2, 4, 8])
            self.assertTrue(all(s['num_requests'] >= s['concurrency_level'] for s in stages))

    def test_heldout_traces_are_exact_mixed_inputs(self):
        expected_shape = {'heldout-low': (30, 116.0), 'heldout-burst': (210, 118.0)}
        for name, (expected_count, expected_last_offset) in expected_shape.items():
            config = self.read(f'{name}.yaml')
            self.assertEqual(config['load']['num_workers'], 1)
            trace = self.out / config['data']['trace']['file']
            rows = trace.read_text().splitlines()
            tokens = [int(row.split(',')[1]) for row in rows[1:]]
            self.assertEqual(len(tokens), expected_count)
            self.assertEqual(set(tokens), {512, 8192})
            self.assertTrue(all(int(row.split(',')[2]) == 128 for row in rows[1:]))
            expected = int(config['load']['stages'][0]['rate'] * config['load']['stages'][0]['duration'])
            self.assertEqual(expected, len(tokens))
            timestamps = [datetime.fromisoformat(row.split(',')[0].replace('Z', '+00:00'))
                          for row in rows[1:]]
            self.assertEqual((timestamps[-1] - timestamps[0]).total_seconds(), expected_last_offset)
            self.assertTrue(all(timestamp.microsecond % 10000 == 0 for timestamp in timestamps))


class WorkloadRenderTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'benchmark.yaml'
        self.config.write_text(yaml.safe_dump(render.benchmark(512, 'ns'), sort_keys=False))

    def test_policy_workload_has_no_pin_and_uses_cached_tokenizer(self):
        configmap, pod = workload.render_workload(self.config, 'run-1', 'cpu', 'ns')
        config = yaml.safe_load(configmap['data']['config.yaml'])
        self.assertEqual(config['api']['headers'], {'x-benchmark-run': 'run-1'})
        tokenizer = next(v for v in pod['spec']['volumes'] if v['name'] == 'tokenizer')
        self.assertEqual(tokenizer['hostPath']['type'], 'DirectoryOrCreate')
        command = pod['spec']['initContainers'][0]['command'][-1]
        self.assertIn('fcntl.flock', command)
        self.assertIn(render.REVISION, command)
        self.assertIn('/reports/exit-code', pod['spec']['containers'][0]['command'][-1])

    def test_diagnostic_pin_is_exact_endpoint_id(self):
        configmap, _ = workload.render_workload(self.config, 'run-1', 'cpu', 'ns', 'decode-local-abc')
        config = yaml.safe_load(configmap['data']['config.yaml'])
        expected = base64.b64encode(b'ns/decode-local-abc-rank-0').decode()
        self.assertEqual(config['api']['headers']['x-benchmark-decoder'], expected)

    def test_trace_is_bundled_and_rewritten(self):
        trace = self.root / 'mixed.trace.csv'
        trace.write_text('TIMESTAMP,ContextTokens,GeneratedTokens\n2026-09-16T00:00:00.000Z,512,128\n')
        config = render.trace_workload(trace.name, 1, 1, 'ns', 1)
        trace_config = self.root / 'trace.yaml'
        trace_config.write_text(yaml.safe_dump(config, sort_keys=False))
        configmap, _ = workload.render_workload(trace_config, 'trace-run', 'cpu', 'ns')
        rendered = yaml.safe_load(configmap['data']['config.yaml'])
        self.assertEqual(rendered['data']['trace']['file'], '/cfg/mixed.trace.csv')
        self.assertEqual(rendered['load']['trace']['file'], '/cfg/mixed.trace.csv')
        self.assertEqual(configmap['data']['mixed.trace.csv'], trace.read_text())

    def test_trace_cannot_escape_rendered_directory(self):
        outside = self.root.parent / f'{self.root.name}-outside.csv'
        outside.write_text('secret')
        self.addCleanup(outside.unlink)
        config = render.trace_workload(f'../{outside.name}', 1, 1, 'ns', 1)
        trace_config = self.root / 'trace.yaml'
        trace_config.write_text(yaml.safe_dump(config, sort_keys=False))
        with self.assertRaisesRegex(ValueError, 'inside the rendered directory'):
            workload.render_workload(trace_config, 'trace-run', 'cpu', 'ns')


if __name__ == '__main__':
    unittest.main()
