import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from evidence import require_collection


class EvidenceTest(unittest.TestCase):
    def test_collection_integrity_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'reports').mkdir(); file = root / 'reports' / 'exit-code'
            file.write_text('0')
            def manifest(complete=True):
                (root / 'collection.json').write_text(json.dumps({'collection_complete': complete, 'errors': [],
                    'sha256': {'reports/exit-code': hashlib.sha256(file.read_bytes()).hexdigest()}}))
            manifest(); require_collection(root, measured=True)
            file.write_text('1')
            with self.assertRaises(ValueError): require_collection(root, measured=True)
            manifest()
            with self.assertRaises(ValueError): require_collection(root, measured=True)
            file.write_text('0'); manifest(False)
            with self.assertRaises(ValueError): require_collection(root, measured=True)
            (root / 'collection.json').unlink()
            with self.assertRaises(FileNotFoundError): require_collection(root)


if __name__ == '__main__':
    unittest.main()
