"""Verify the delivered ZIP, then smoke-test it without opening any UI."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile


def verify(archive_path):
    with tempfile.TemporaryDirectory(prefix='updream-offline-') as directory:
        temporary = Path(directory).resolve()
        assert temporary.parent == Path(tempfile.gettempdir()).resolve()
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names)), 'Duplicate ZIP paths'
            for name in names:
                path = PurePosixPath(name)
                assert not path.is_absolute() and '..' not in path.parts and '\\' not in name
                assert path.parts[0] == 'Updream-MCP-Bridge'
                assert not any(p in {'.local', '.git', 'connection.json', 'pairing.local.json', '.env'} for p in path.parts), 'Private file in release'
            archive.extractall(temporary)
        root = temporary / 'Updream-MCP-Bridge'
        manifest = json.loads((root / 'bundle-manifest.json').read_text(encoding='utf-8'))
        assert len(manifest['files']) + 1 == len(names)
        for name, expected in manifest['files'].items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
        bat = (root / 'install.bat').read_bytes()
        assert bat.isascii() and b'\r\n' in bat and b'\n' not in bat.replace(b'\r\n', b'')
        subprocess.run([str(root / 'runtime/node.exe'), str(root / 'scripts/smoke-install.cjs'), str(temporary / 'test-state')], cwd=root, check=True, timeout=120)
        print(json.dumps({'verified': True, 'archive': str(archive_path), 'files': len(names), 'browserUiTested': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    verify(parser.parse_args().archive.resolve())
