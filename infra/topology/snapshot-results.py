#!/usr/bin/env python3
"""Stream verified result snapshots; live logs are bounded prefixes, not EOF reads."""
import argparse
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
from pathlib import Path

MANIFEST = '.snapshot-manifest.json'


class PrefixReader:
    def __init__(self, source, size):
        self.source, self.left = source, size
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        size = self.left if size < 0 else min(size, self.left)
        data = self.source.read(size)
        if len(data) != size:
            raise IOError('Result file was truncated during snapshot')
        self.left -= len(data)
        self.digest.update(data)
        return data


def snapshot(root, output, after_open=None):
    root = Path(root).resolve(strict=True)
    if (root / MANIFEST).exists():
        raise ValueError('Reserved snapshot-manifest name already exists')
    entries = []
    with tarfile.open(fileobj=output, mode='w|gz') as archive:
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ValueError('Result symlinks are not supported')
            if path.is_dir():
                continue
            with path.open('rb') as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('Result is not a regular file')
                if after_open:
                    after_open(path)
                reader = PrefixReader(source, before.st_size)
                name = path.relative_to(root).as_posix()
                member = tarfile.TarInfo(name)
                member.size, member.mtime, member.mode = before.st_size, int(before.st_mtime), 0o644
                archive.addfile(member, reader)
                after = os.fstat(source.fileno())
                try:
                    current = path.stat(follow_symlinks=False)
                    same_path = (current.st_dev, current.st_ino) == (before.st_dev, before.st_ino)
                except FileNotFoundError:
                    same_path = False
                changed = (not same_path or before.st_size != after.st_size
                           or before.st_mtime_ns != after.st_mtime_ns)
                entries.append({'path': name, 'size': before.st_size,
                                'sha256': reader.digest.hexdigest(),
                                'mutable_log_prefix': path.suffix == '.log',
                                'source_changed_during_snapshot': changed})
        manifest = json.dumps({'version': 1, 'files': entries}, sort_keys=True).encode()
        member = tarfile.TarInfo(MANIFEST)
        member.size = len(manifest)
        archive.addfile(member, io.BytesIO(manifest))


def validate(root):
    root = Path(root)
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest.get('version') != 1:
        raise ValueError('Unsupported result snapshot')
    entries = manifest['files']
    names = [entry['path'] for entry in entries]
    actual = [p.relative_to(root).as_posix() for p in root.rglob('*')
              if p.is_file() and p.relative_to(root).as_posix() != MANIFEST]
    if len(set(names)) != len(names) or set(actual) != set(names):
        raise ValueError('Snapshot file inventory mismatch')
    for entry in entries:
        name = Path(entry['path'])
        if name.is_absolute() or '..' in name.parts:
            raise ValueError('Unsafe snapshot path')
        path = root / name
        if path.is_symlink() or path.stat().st_size != entry['size']:
            raise ValueError('Snapshot file type or size mismatch')
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != entry['sha256']:
            raise ValueError('Snapshot checksum mismatch')
        is_log = path.suffix == '.log'
        if bool(entry['mutable_log_prefix']) != is_log:
            raise ValueError('Invalid mutable-log classification')
        if entry['source_changed_during_snapshot'] and not is_log:
            raise ValueError('Structured result changed during snapshot: ' + entry['path'])
    return {'files_verified': len(entries),
            'mutable_log_prefixes': [e['path'] for e in entries if e['mutable_log_prefix']],
            'logs_changed_during_snapshot': [e['path'] for e in entries
                if e['mutable_log_prefix'] and e['source_changed_during_snapshot']]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    snapshot(args.root, sys.stdout.buffer)
