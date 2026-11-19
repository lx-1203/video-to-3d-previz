"""Build a credential-free, offline Windows x64 installer from this source."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
NODE_VERSION = '24.18.0'
NODE_SHA256 = '9a4eb5f1c29c6a2e93852ead46b999e284a6a5ca8bab4d4e241d587d025a52de'
STAMP = (2026, 11, 9, 0, 0, 0)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open('wb') as output:
        shutil.copyfileobj(response, output)


def build(out, node_exe=None, node_license=None):
    cache = ROOT / '.runtime-cache'
    cache.mkdir(exist_ok=True)
    node_exe = node_exe or cache / 'node.exe'
    if not node_exe.exists():
        download(f'https://nodejs.org/dist/v{NODE_VERSION}/win-x64/node.exe', node_exe)
    if digest(node_exe.read_bytes()) != NODE_SHA256:
        raise ValueError('Node binary does not match the pinned official SHA256')
    node_license = node_license or cache / 'NODE-LICENSE.txt'
    if not node_license.exists():
        download(f'https://raw.githubusercontent.com/nodejs/node/v{NODE_VERSION}/LICENSE', node_license)
    version = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']
    files = {}
    for folder in ['server', 'extension', 'scripts', 'references', 'node_modules']:
        base = ROOT / folder
        if not base.is_dir():
            raise ValueError(f'Missing {folder}; run npm ci --omit=dev --ignore-scripts first')
        for file in sorted(base.rglob('*')):
            rel = file.relative_to(ROOT)
            if file.is_symlink():
                if '.bin' in rel.parts:
                    continue
                raise ValueError(f'Unexpected symlink: {rel}')
            if not file.is_file() or any(p in {'.bin', '__pycache__', '.local', '.git'} for p in rel.parts):
                continue
            if file.name in {'connection.json', 'pairing.local.json'} or file.suffix == '.pyc':
                raise ValueError(f'Private/generated file in source: {rel}')
            files[rel.as_posix()] = file.read_bytes()
    for name in ['install.bat', 'README.md', 'INSTALL.md', 'AGENT-GUIDE.md', 'TOOLS.md', 'package.json', 'package-lock.json', 'PROVENANCE.md']:
        files[name] = (ROOT / name).read_bytes()
    files['runtime/node.exe'] = node_exe.read_bytes()
    files['runtime/NODE-LICENSE.txt'] = node_license.read_bytes()
    files['runtime/ORIGIN.json'] = (json.dumps({'version': NODE_VERSION, 'platform': 'win-x64', 'sha256': NODE_SHA256, 'source': f'https://nodejs.org/dist/v{NODE_VERSION}/win-x64/node.exe'}, indent=2) + '\n').encode()
    manifest = {'format': 1, 'version': version, 'runtimeVersion': NODE_VERSION, 'files': {name: digest(data) for name, data in sorted(files.items())}}
    files['bundle-manifest.json'] = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()
    out.mkdir(parents=True, exist_ok=True)
    archive_path = out / f'updream-mcp-bridge-{version}-windows-x64.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo('Updream-MCP-Bridge/' + name, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt ZIP entry')
        for name, data in files.items():
            if archive.read('Updream-MCP-Bridge/' + name) != data:
                raise ValueError('ZIP content mismatch: ' + name)
    checksum = digest(archive_path.read_bytes())
    (out / 'SHA256SUMS.txt').write_text(f'{checksum}  {archive_path.name}\n', encoding='ascii')
    (out / 'build-receipt.json').write_text(json.dumps({'version': version, 'archive': archive_path.name, 'sha256': checksum, 'fileCount': len(files), 'nodeSha256': NODE_SHA256, 'sourceFiles': {name: digest(data) for name, data in files.items() if not name.startswith(('runtime/', 'node_modules/'))}}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(archive_path), 'sha256': checksum, 'files': len(files), 'bytes': archive_path.stat().st_size}))
    return archive_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts')
    parser.add_argument('--node-exe', type=Path)
    parser.add_argument('--node-license', type=Path)
    args = parser.parse_args()
    build(args.out.resolve(), args.node_exe, args.node_license)
