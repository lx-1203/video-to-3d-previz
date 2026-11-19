"""Validate the real ZIP and run installation against an isolated CODEX_HOME."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile


def verify(archive_path, codex_exe=None, *, apply_local=False, codex_home=None):
    if codex_exe is not None:
        codex_exe = codex_exe.resolve(strict=True)
    if apply_local and (codex_exe is None or not codex_exe.is_file()):
        raise ValueError('A verified local update requires the target Codex executable')
    if codex_home is not None:
        codex_home = codex_home.resolve(strict=True)
        if not codex_home.is_dir():
            raise ValueError('Codex configuration directory is not a directory')
    with tempfile.TemporaryDirectory(prefix='updream-codex-offline-') as directory:
        temporary = Path(directory).resolve()
        assert temporary.parent == Path(tempfile.gettempdir()).resolve()
        with zipfile.ZipFile(archive_path) as z:
            names = z.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Duplicate ZIP entry')
            for name in names:
                parts = PurePosixPath(name)
                if (not parts.parts or parts.parts[0] != 'Updream-Codex'
                        or parts.is_absolute() or '..' in parts.parts or '\\' in name
                        or ':' in name):
                    raise ValueError('Invalid installer ZIP entry: ' + name)
                if any(p in {'.local', '.git', 'auth.json', 'connection.json', 'pairing.local.json', '.env'} for p in parts.parts):
                    raise ValueError('Private file in installer ZIP: ' + name)
            z.extractall(temporary)
        root = temporary / 'Updream-Codex'
        manifest = json.loads((root / 'bundle-manifest.json').read_text())
        expected_entries = {'Updream-Codex/' + name for name in manifest['files']}
        expected_entries.add('Updream-Codex/bundle-manifest.json')
        if expected_entries != set(names):
            raise ValueError('ZIP entries differ from the installer manifest')
        for name, expected in manifest['files'].items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise ValueError('Bundle checksum mismatch: ' + name)
        bat = (root / 'install.bat').read_bytes()
        assert bat.isascii() and b'\n' not in bat.replace(b'\r\n', b'')
        command = [str(root / 'runtime/node.exe'), str(root / 'scripts/smoke-install.cjs'), str(temporary / 'test-state')]
        if codex_exe:
            command.append(str(codex_exe))
        subprocess.run(command, cwd=root, check=True, timeout=180)
        print(json.dumps({'verified': True, 'files': len(names), 'browserConnectionTested': False}))
        if apply_local:
            command = [
                str(root / 'runtime/node.exe'), str(root / 'scripts/install.cjs'),
                '--update-existing', '--codex-exe', str(codex_exe),
            ]
            if codex_home is not None:
                command.extend(('--codex-home', str(codex_home)))
            subprocess.run(command, cwd=root, check=True, timeout=180)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--codex-exe', type=Path)
    parser.add_argument('--apply-local', action='store_true',
                        help='After isolated verification, update the registered local Updream plugin in place')
    parser.add_argument('--codex-home', type=Path,
                        help='Existing Codex configuration directory for --apply-local')
    args = parser.parse_args()
    if args.codex_home is not None and not args.apply_local:
        parser.error('--codex-home requires --apply-local')
    verify(args.archive.resolve(), args.codex_exe,
           apply_local=args.apply_local, codex_home=args.codex_home)
