import copy
import importlib.util
from pathlib import Path
import unittest
import tarfile

spec = importlib.util.spec_from_file_location('tp4_evidence', Path(__file__).with_name('tp4-evidence.py'))
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def rows(rank, lane='cuda_ipc/cuda'):
    prefix = f'[123.00] [local:{100+rank}:0]'
    return (f'{prefix} | cfg#2 | remote memory read by ucp_get*(multi) into cuda/GPU{rank} from cuda/dev[0] |\n'
            f'{prefix} | 0 | copy-out | rc_mlx5/mlx5_0:1 |\n'
            f'{prefix} | 1..inf | zero-copy | {lane} |\n')


class TP4EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = '\n'.join(f'(Worker_TP{rank} pid={100+rank}) ready' for rank in range(4))
        self.logs = {str(100+r): rows(r) for r in range(4)}

    def test_each_local_rank_requires_selected_cuda_ipc(self):
        self.assertEqual(len(e.validate_rank_transports(self.engine, self.logs, 'local')), 4)
        for rank in range(4):
            bad = dict(self.logs)
            bad[str(100+rank)] = rows(rank, 'tcp/eth0')
            with self.assertRaisesRegex(ValueError, 'Unexpected'):
                e.validate_rank_transports(self.engine, bad, 'local')

    def test_available_transport_is_not_selected_read(self):
        bad = dict(self.logs)
        bad['100'] = '[123] [local:100:0] UCX available cuda_ipc/cuda'
        with self.assertRaisesRegex(ValueError, 'Missing'):
            e.validate_rank_transports(self.engine, bad, 'local')

    def test_remote_rc_and_dc_supported(self):
        for lane in ['rc_mlx5/mlx5_0:1/path0', 'dc_mlx5/mlx5_0:1', 'rc_verbs/mlx5_0:1']:
            logs = {str(100+r): rows(r, lane) for r in range(4)}
            self.assertEqual(len(e.validate_rank_transports(self.engine, logs, 'remote')), 4)

    def test_restart_missing_rank_or_wrong_gpu_fails(self):
        for log in [self.engine.split('\n', 1)[1], self.engine+'\n(Worker_TP0 pid=999) ready']:
            with self.assertRaises(ValueError):
                e.validate_rank_transports(log, self.logs, 'local')
        bad = dict(self.logs)
        bad['102'] = rows(2).replace('cuda/GPU2', 'cuda/GPU0')
        with self.assertRaises(ValueError):
            e.validate_rank_transports(self.engine, bad, 'local')

    def test_thread_interleaving_cannot_borrow_another_threads_lane(self):
        log = rows(0).splitlines()[0]+'\n[123] [local:100:1] | 1..inf | zero-copy | cuda_ipc/cuda |'
        self.assertEqual(e.read_protocol_rows(log), [])

    def test_host_memory_read_is_not_gpu_memory_read(self):
        log = rows(0).replace('into cuda/GPU0 from cuda/dev[0]', 'into host memory from host')
        self.assertEqual(e.read_protocol_rows(log), [])

    def test_saved_real_protocol_logs_and_counter_names(self):
        # TP1 records exercise actual UCX syntax only, not TP4 GPU qualification.
        root = Path(__file__).resolve().parents[2] / 'results/2026-09-20-paired-locality'
        for role in ['local', 'remote']:
            with tarfile.open(root / (role + '-evidence.tar.gz')) as archive:
                member = next(m for m in archive.getmembers() if m.name.endswith('decode.log'))
                parsed = e.read_protocol_rows(archive.extractfile(member).read().decode(errors='replace'))
                self.assertTrue(parsed)
                bulk = [r for r in parsed if r['range'].endswith('inf')]
                self.assertTrue(bulk)
                self.assertTrue(all(r['method'] == 'zero-copy' for r in bulk))
                self.assertTrue(all(('cuda_ipc' in r['lane']) == (role == 'local') for r in bulk))
        self.assertTrue(all(name.endswith('_total') for name in e.FAILURES))

    def test_small_message_emulation_does_not_describe_large_kv_blocks(self):
        logs = {str(100+r): rows(r, 'rc_mlx5/mlx5_0:1').replace(
            '| 1..inf | zero-copy',
            '| 1..64 | software emulation | rc_mlx5/mlx5_0:1 |\n'
            f'[123] [local:{100+r}:0] | 65..inf | zero-copy') for r in range(4)}
        self.assertEqual(len(e.validate_rank_transports(self.engine, logs, 'remote')), 4)

    def test_four_rank_counters_bytes_errors_and_timing(self):
        zero = dict.fromkeys((e.COUNT, e.BYTES, e.TIME_COUNT, e.TIME_SUM, *e.FAILURES), 0)
        before = {role: dict(zero) for role in ['prefill', 'local', 'remote']}
        after = copy.deepcopy(before)
        after['local'].update({e.COUNT:4, e.BYTES:16*2**30, e.TIME_COUNT:4, e.TIME_SUM:.4})
        result = e.transfer_delta(before, after, 'local', 16*2**30)
        self.assertEqual(result['local']['mean_rank_transfer_seconds'], .1)
        for field,value in [(e.COUNT,1),(e.BYTES,4*2**30),(e.TIME_COUNT,1),(e.FAILURES[0],1)]:
            bad = copy.deepcopy(after);bad['local'][field]=value
            with self.assertRaises(ValueError):
                e.transfer_delta(before,bad,'local',16*2**30)
        with self.assertRaises(ValueError):
            e.transfer_delta(before,after,'remote',16*2**30)


if __name__ == '__main__':
    unittest.main()
