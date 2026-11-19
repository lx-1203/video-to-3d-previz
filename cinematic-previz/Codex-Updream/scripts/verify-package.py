"""Validate the real ZIP and run installation against an isolated CODEX_HOME."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile


def verify(archive_path, codex_exe=None):
    with tempfile.TemporaryDirectory(prefix='updream-codex-offline-') as directory:
        temporary = Path(directory).resolve()
        assert temporary.parent == Path(tempfile.gettempdir()).resolve()
        with zipfile.ZipFile(archive_path) as z:
            names = z.namelist()
            assert len(names) == len(set(names))
            for name in names:
                parts = PurePosixPath(name)
                assert not parts.is_absolute() and '..' not in parts.parts and '\\' not in name
                assert parts.parts[0] == 'Updream-Codex'
                assert not any(p in {'.local', '.git', 'auth.json', 'connection.json', 'pairing.local.json', '.env'} for p in parts.parts)
            z.extractall(temporary)
        root = temporary / 'Updream-Codex'
        manifest = json.loads((root / 'bundle-manifest.json').read_text())
        assert len(manifest['files']) + 1 == len(names)
        for name, expected in manifest['files'].items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
        bat = (root / 'install.bat').read_bytes()
        assert bat.isascii() and b'\n' not in bat.replace(b'\r\n', b'')
        command = [str(root / 'runtime/node.exe'), str(root / 'scripts/smoke-install.cjs'), str(temporary / 'test-state')]
        if codex_exe:
            command.append(str(codex_exe))
        subprocess.run(command, cwd=root, check=True, timeout=180)
        print(json.dumps({'verified': True, 'files': len(names), 'browserConnectionTested': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--codex-exe', type=Path)
    args = parser.parse_args()
    verify(args.archive.resolve(), args.codex_exe)
