import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class TransferDeltasTest(unittest.TestCase):
    def test_expected_counts_and_notification_failures(self):
        script = Path(__file__).with_name('transfer-delta.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def snapshot(folder, count, timed_count, notifications=0):
                folder.mkdir(exist_ok=True)
                (folder / 'reports').mkdir(exist_ok=True); (folder / 'reports' / 'exit-code').write_text('0')
                names = {'bytes_transferred_sum': count * 100, 'bytes_transferred_count': count,
                         'xfer_time_seconds_sum': count * .01, 'xfer_time_seconds_count': timed_count,
                         'num_failed_transfers_total': 0, 'num_kv_expired_reqs_total': 0,
                         'num_failed_notifications_total': notifications}
                (folder / 'decoder-metrics.txt').write_text(''.join(f'vllm:nixl_{k} {v}\n' for k, v in names.items()))
                files = [folder / 'decoder-metrics.txt', folder / 'reports' / 'exit-code']
                (folder / 'collection.json').write_text(json.dumps({'collection_complete': True, 'errors': [],
                    'sha256': {str(f.relative_to(folder)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}))
            before = root / 'before'; snapshot(before, 1, 1)
            for name, count, timed_count, notifications, expected in [
                ('complete', 13, 13, 0, True), ('missing-pulls', 2, 2, 0, False),
                ('count-mismatch', 13, 12, 0, False), ('notification-failure', 13, 13, 1, False)]:
                with self.subTest(name=name):
                    after = root / name; snapshot(after, count, timed_count, notifications)
                    r = subprocess.run([sys.executable, str(script), '--before', str(before), '--after', str(after),
                        '--decoder-pod', 'decoder', '--expected-transfers', '12'], capture_output=True, text=True)
                    self.assertEqual(r.returncode == 0, expected, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
