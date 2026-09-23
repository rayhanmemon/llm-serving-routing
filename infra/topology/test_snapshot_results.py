"""Growing logs must not abort valid measurements; structured evidence remains strict."""
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

p=Path(__file__).with_name('snapshot-results.py')
s=importlib.util.spec_from_file_location('snapshots',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


class SnapshotTests(unittest.TestCase):
    def copy(self, root, target, hook=None):
        output=io.BytesIO();m.snapshot(root,output,after_open=hook);output.seek(0)
        with tarfile.open(fileobj=output,mode='r:gz') as archive:
            archive.extractall(target,filter='data')

    def test_growing_log_captures_original_prefix_and_validates_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';target=Path(temp)/'copy';root.mkdir()
            (root/'prefill.log').write_bytes(b'old log\n')
            (root/'complete.json').write_text('{"requests":6}')
            def grow(path):
                if path.suffix=='.log':
                    with path.open('ab') as f:f.write(b'new live log\n'*100)
            self.copy(root,target,grow)
            self.assertEqual((target/'prefill.log').read_bytes(),b'old log\n')
            self.assertEqual(m.validate(target)['logs_changed_during_snapshot'],['prefill.log'])
            self.assertEqual(json.loads((target/'complete.json').read_text()),{'requests':6})

    def test_changed_structured_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';target=Path(temp)/'copy';root.mkdir()
            (root/'requests.json').write_text('[]')
            self.copy(root,target,lambda path:path.write_text('[1]'))
            with self.assertRaisesRegex(ValueError,'Structured result changed'):m.validate(target)

    def test_truncated_log_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'prefill.log').write_text('a complete log')
            with self.assertRaisesRegex(IOError,'truncated'):
                m.snapshot(root,io.BytesIO(),after_open=lambda path:path.write_text(''))

    def test_missing_and_corrupted_data_fail_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';root.mkdir();(root/'complete.json').write_text('{}')
            for mode in ['missing','corrupt']:
                target=Path(temp)/mode;self.copy(root,target)
                if mode=='missing':(target/'complete.json').unlink()
                else:(target/'complete.json').write_text('[]')
                with self.assertRaises(ValueError):m.validate(target)

    def test_symlink_cannot_escape_result_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'link').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError,'symlinks'):m.snapshot(root,io.BytesIO())


if __name__=='__main__':unittest.main()
