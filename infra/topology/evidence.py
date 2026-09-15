"""Shared integrity checks for collected diagnostic evidence."""
import hashlib
import json
from pathlib import Path


def require_collection(folder, measured=False):
    folder = Path(folder).resolve()
    manifest = json.loads((folder / 'collection.json').read_text())
    if manifest.get('collection_complete') is not True or manifest.get('errors'):
        raise ValueError('Collection is incomplete: ' + str(folder))
    checksums = manifest.get('sha256')
    if not isinstance(checksums, dict) or not checksums:
        raise ValueError('Collection checksums missing')
    for name, expected in checksums.items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise ValueError('Invalid or missing collected file: ' + name)
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Collected file checksum mismatch: ' + name)
    if measured:
        if (folder / 'reports' / 'exit-code').read_text().strip() != '0':
            raise ValueError('Benchmark did not exit successfully')
