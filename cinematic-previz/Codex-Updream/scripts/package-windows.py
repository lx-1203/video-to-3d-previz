"""Build the Codex-native installer; no browser extension or pairing is bundled."""
from pathlib import Path
import argparse
import hashlib
import json
import urllib.request
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent
NODE_VERSION = '24.18.0'
NODE_SHA256 = '9a4eb5f1c29c6a2e93852ead46b999e284a6a5ca8bab4d4e241d587d025a52de'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def download(url, target):
    with urllib.request.urlopen(url, timeout=120) as response, target.open('wb') as output:
        shutil.copyfileobj(response, output)


def build(out, node_exe=None, node_license=None):
    cache = ROOT / '.runtime-cache'
    cache.mkdir(exist_ok=True)
    node_exe = node_exe or cache / 'node.exe'
    node_license = node_license or cache / 'NODE-LICENSE.txt'
    if not node_exe.exists():
        download(f'https://nodejs.org/dist/v{NODE_VERSION}/win-x64/node.exe', node_exe)
    if sha(node_exe.read_bytes()) != NODE_SHA256:
        raise ValueError('Node runtime checksum mismatch')
    if not node_license.exists():
        download(f'https://raw.githubusercontent.com/nodejs/node/v{NODE_VERSION}/LICENSE', node_license)
    version = json.loads((ROOT / 'plugin/.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
    files = {}
    for folder in ['plugin', '.agents', 'scripts']:
        for file in sorted((ROOT / folder).rglob('*')):
            if file.is_symlink():
                raise ValueError('Unexpected symbolic link')
            if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                files[file.relative_to(ROOT).as_posix()] = file.read_bytes()
    for name in ['install.bat', 'README.md', 'RELEASE.md']:
        files[name] = (ROOT / name).read_bytes()
    files['runtime/node.exe'] = node_exe.read_bytes()
    files['runtime/NODE-LICENSE.txt'] = node_license.read_bytes()
    manifest = {'format': 1, 'version': version, 'nodeSha256': NODE_SHA256, 'files': {name: sha(data) for name, data in sorted(files.items())}}
    files['bundle-manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f'updream-codex-{version}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name, data in sorted(files.items()):
            if any(p in {'.local', 'auth.json', 'connection.json', 'pairing.local.json', '.env'} for p in Path(name).parts):
                raise ValueError('Private file in release')
            info = zipfile.ZipInfo('Updream-Codex/' + name, date_time=(2026, 11, 9, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data, compresslevel=9)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name, data in files.items():
            assert z.read('Updream-Codex/' + name) == data
    digest = sha(archive.read_bytes())
    (out / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    receipt = {'version': version, 'archive': archive.name, 'sha256': digest, 'nodeSha256': NODE_SHA256, 'fileCount': len(files), 'sourceFiles': {name: sha(data) for name, data in files.items() if not name.startswith('runtime/')}}
    (out / 'build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(archive), 'sha256': digest, 'files': len(files), 'bytes': archive.stat().st_size}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts')
    parser.add_argument('--node-exe', type=Path)
    parser.add_argument('--node-license', type=Path)
    args = parser.parse_args()
    build(args.out.resolve(), args.node_exe, args.node_license)
