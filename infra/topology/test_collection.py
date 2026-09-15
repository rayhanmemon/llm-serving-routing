"""Exercise collection failure handling without a cluster or serving requests."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


class CollectionTest(unittest.TestCase):
    def test_complete_and_incomplete_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock = root / 'kubectl'
            mock.write_text('''#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
a=sys.argv
if "exec" in a:
    sys.stdout.buffer.write(Path(os.environ["REPORT_ARCHIVE"]).read_bytes())
elif "--raw" in a:
    print("example_metric 1")
    sys.exit(int(os.environ.get("METRICS_FAIL","0")))
elif "logs" in a:
    print("log entry")
elif "pods" in a:
    print(json.dumps({"items":[{"metadata":{"name":"bench","labels":{"app.kubernetes.io/name":"topology-benchmark"}},"spec":{"containers":[{"name":"benchmark","image":"benchmark"}]}}, {"metadata":{"name":"engine"},"spec":{"containers":[{"name":"modelserver","image":"vllm","ports":[{"name":"modelserver","containerPort":8000}]}]}}]}))
else:
    print(json.dumps({"items":[]}))
''')
            mock.chmod(0o755)
            for name, exit_code, report, metric_fail, expected in [
                ('ok', '0', True, False, True),
                ('failed-harness', '1', True, False, False),
                ('missing-report', '0', False, False, False),
                ('missing-exit', None, True, False, False),
                ('failed-metrics', '0', True, True, False),
            ]:
                with self.subTest(name=name):
                    archive = root / (name + '.tar')
                    with tarfile.open(archive, 'w') as tar:
                        files = {}
                        if exit_code is not None:
                            files['exit-code'] = exit_code.encode()
                        if report:
                            files['per_request_lifecycle_metrics.json'] = b'[]'
                        for path, data in files.items():
                            member = tarfile.TarInfo(path); member.size = len(data)
                            tar.addfile(member, io.BytesIO(data))
                    out = root / name
                    env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                           'REPORT_ARCHIVE': str(archive), 'METRICS_FAIL': str(int(metric_fail))}
                    r = subprocess.run([sys.executable, str(Path(__file__).with_name('collect.py')),
                        '--context', 'mock', '--benchmark-pod', 'bench', '--out', str(out)],
                        env=env, capture_output=True, text=True)
                    self.assertEqual(r.returncode == 0, expected, r.stdout + r.stderr)
                    manifest = json.loads((out / 'collection.json').read_text())
                    self.assertEqual(manifest['collection_complete'], expected)
                    self.assertTrue(manifest['sha256'])


if __name__ == '__main__':
    unittest.main()
