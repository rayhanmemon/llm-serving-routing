import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('tp4_config', HERE / 'tp4-config.py')
tp4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp4)


class TP4ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config, self.architecture = tp4.load_config()

    def test_three_engines_use_disjoint_groups_and_matched_settings(self):
        local = tp4.engine_specs(self.config, 'local', '10.0.0.1')
        remote = tp4.engine_specs(self.config, 'remote', '10.0.0.2')
        self.assertEqual([s['env']['CUDA_VISIBLE_DEVICES'] for s in local], ['0,1,2,3', '4,5,6,7'])
        self.assertEqual(remote[0]['env']['CUDA_VISIBLE_DEVICES'], '0,1,2,3')
        self.assertEqual(local[1]['command'], remote[0]['command'])
        self.assertNotEqual(local[0]['env']['VLLM_NIXL_SIDE_CHANNEL_PORT'], local[1]['env']['VLLM_NIXL_SIDE_CHANNEL_PORT'])
        for engine in local + remote:
            self.assertIn('--enable-chunked-prefill', engine['command'])
            self.assertNotIn('--enforce-eager', engine['command'])
            self.assertNotIn('UCX_TLS', engine['env'])
            self.assertIn(self.config['revision'], engine['command'])

    def test_long_input_has_output_headroom(self):
        bad = copy.deepcopy(self.config)
        bad['input_tokens'][-1] = 131072
        with self.assertRaisesRegex(ValueError, 'context capacity'):
            tp4.validate(bad, self.architecture)

    def test_shared_gpu_group_is_rejected(self):
        bad = copy.deepcopy(self.config)
        bad['placement']['local']['decode'] = [0, 1, 2, 3]
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            tp4.validate(bad, self.architecture)

    def test_memory_formula_and_output_block_rounding(self):
        self.assertEqual(tp4.cache_bytes_per_rank(self.architecture, 65536), 4 * 2**30)
        rows = tp4.sizing(self.config, self.architecture)
        row = next(r for r in rows if r['input_tokens'] == 122880 and r['total_concurrent_requests'] == 8)
        self.assertEqual(row['allocated_tokens_per_request'], 122944)
        self.assertEqual(row['kv_gib_per_rank'], 60.03125)
        self.assertFalse(row['runtime_overhead_included'])
        self.assertLess(row['weights_plus_kv_gib_per_rank_approx'], 76)

    def test_architecture_tampering_cannot_change_capacity_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'config.json').write_text(json.dumps(self.config))
            (folder / 'model-config.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'digest'):
                tp4.load_config(folder / 'config.json')

    def test_kv_replication_or_sliding_window_invalidates_formula(self):
        for field, value in [('num_key_value_heads', 2), ('use_sliding_window', True)]:
            bad = dict(self.architecture)
            bad[field] = value
            with self.assertRaises(ValueError):
                tp4.validate(self.config, bad)

    def test_local_only_stage_does_not_render_remote(self):
        document = tp4.render(tp4.DEFAULT, {'local': 'node-a'})
        pods = [p for p in document['items'] if p['kind'] == 'Pod']
        self.assertEqual([p['metadata']['name'] for p in pods], ['local'])
        self.assertEqual(pods[0]['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu'], '8')

    def test_complete_manifest_keeps_node_separation_and_resources(self):
        document = tp4.render(tp4.DEFAULT, {'local': 'node-a', 'remote': 'node-b'}, 6900)
        pods = [p for p in document['items'] if p['kind'] == 'Pod']
        self.assertEqual([p['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu'] for p in pods], ['8', '4'])
        self.assertEqual([p['spec']['containers'][0]['resources']['limits']['memory'] for p in pods], ['256Gi', '128Gi'])
        for pod in pods:
            self.assertEqual(pod['spec']['activeDeadlineSeconds'], 6900)
            self.assertFalse(pod['spec']['automountServiceAccountToken'])
            self.assertNotIn('hostIPC', pod['spec'])
            self.assertEqual(pod['spec']['containers'][0]['securityContext']['capabilities']['add'], ['IPC_LOCK'])
        with self.assertRaisesRegex(ValueError, 'distinct'):
            tp4.render(tp4.DEFAULT, {'local': 'same', 'remote': 'same'})

    def test_material_drift_requires_revised_plan(self):
        for key, value in [('dtype', 'float16'), ('enable_prefix_caching', True),
                           ('enforce_eager', True), ('revision', 'main'),
                           ('tensor_parallel_size', 8), ('max_num_seqs', 64)]:
            bad = copy.deepcopy(self.config)
            bad[key] = value
            with self.assertRaises(ValueError):
                tp4.validate(bad, self.architecture)


if __name__ == '__main__':
    unittest.main()
