#!/usr/bin/env python3
"""Fail-closed, per-invocation GitHub update gate. Python standard library only."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

NAME = 'video-to-3d-previz'
MANIFEST = 'release-manifest.json'
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
REQUIRED = {'SKILL.md', 'agents/openai.yaml', 'scripts/check_update.py', 'update-source.json'}


class GateError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def parse_json(data, code):
    try:
        obj = json.loads(data)
        if not isinstance(obj, dict):
            raise ValueError('object required')
        return obj
    except (ValueError, UnicodeError) as exc:
        raise GateError(code, 'Invalid JSON object.') from exc


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise GateError('INVALID_MANIFEST', 'Version must be major.minor.patch.')
    return tuple(map(int, version.split('.')))


def validate_manifest(m):
    if type(m.get('schema_version')) is not int or m.get('schema_version') != 1 or m.get('skill_name') != NAME:
        raise GateError('INVALID_MANIFEST', 'Unsupported schema or wrong skill identity.')
    version_tuple(m.get('version'))
    files = m.get('files')
    if not isinstance(files, dict) or not REQUIRED.issubset(files) or len(files) > 200:
        raise GateError('INVALID_MANIFEST', 'Required files missing or file count invalid.')
    names, total = set(), 0
    for name, meta in files.items():
        if not isinstance(name, str) or not name or '\\' in name or ':' in name:
            raise GateError('INVALID_MANIFEST', 'Unsafe package path.')
        path = PurePosixPath(name)
        if path.is_absolute() or str(path) != name or any(p in ('.', '..') for p in path.parts):
            raise GateError('INVALID_MANIFEST', 'Unsafe package path.')
        for part in path.parts:
            if (not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part.startswith('.')
                    or part.endswith(('.', ' ')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?', part)):
                raise GateError('INVALID_MANIFEST', 'Unsupported package filename.')
        if not (name in ('SKILL.md', 'update-source.json') or
                (path.parts[0] in ('agents', 'references', 'scripts') and len(path.parts) > 1)):
            raise GateError('INVALID_MANIFEST', 'File is outside the runtime package.')
        if path.suffix.lower() not in ('.md', '.json', '.yaml', '.py') or name.casefold() in names:
            raise GateError('INVALID_MANIFEST', 'Unsupported file type or case collision.')
        names.add(name.casefold())
        if not isinstance(meta, dict) or type(meta.get('size')) is not int or not 0 <= meta['size'] <= MAX_FILE:
            raise GateError('INVALID_MANIFEST', 'Invalid file size.')
        if not isinstance(meta.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', meta['sha256']):
            raise GateError('INVALID_MANIFEST', 'Invalid checksum.')
        total += meta['size']
    if total > MAX_TOTAL:
        raise GateError('INVALID_MANIFEST', 'Package exceeds size limit.')
    return m


def source_config(data):
    config = parse_json(data, 'INVALID_SOURCE')
    if (set(config) != {'repository', 'branch'} or
            not isinstance(config['repository'], str) or
            not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', config['repository']) or
            not isinstance(config['branch'], str) or
            not re.fullmatch(r'[A-Za-z0-9_-]+', config['branch'])):
        raise GateError('INVALID_SOURCE', 'Expected a GitHub owner/repository and simple branch name.')
    return config


def is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def local_files(root, manifest):
    expected = set(manifest['files']) | {MANIFEST}
    seen = set()
    for path in root.rglob('*'):
        if is_link(path):
            raise GateError('LOCAL_MODIFIED', 'Symlinks or junctions are not supported in an installed skill.')
        if path.is_file():
            seen.add(path.relative_to(root).as_posix())
    if seen - expected:
        raise GateError('LOCAL_EXTRA_FILES', 'Unmanaged files found; preserve and reconcile them before updating.')
    data = {}
    for name, meta in manifest['files'].items():
        path = root / name
        if not path.is_file() or path.stat().st_size != meta['size']:
            raise GateError('LOCAL_MODIFIED', 'Managed file missing or modified: ' + name)
        value = path.read_bytes()
        if digest(value) != meta['sha256']:
            raise GateError('LOCAL_MODIFIED', 'Managed file modified: ' + name)
        data[name] = value
    return data


def validate_contents(data, manifest, config):
    if source_config(data['update-source.json']) != config:
        raise GateError('REMOTE_SOURCE_CHANGED', 'Repository/branch changes require explicit maintenance, not automatic migration.')
    try:
        text = data['SKILL.md'].decode('utf-8')
    except UnicodeError as exc:
        raise GateError('INVALID_MANIFEST', 'SKILL.md must be UTF-8.') from exc
    if not re.search(r'^name:\s*video-to-3d-previz\s*$', text, re.M):
        raise GateError('INVALID_MANIFEST', 'Wrong skill name in SKILL.md.')
    version = re.search(r'^\s+version:\s*[\"\x27]?([0-9]+\.[0-9]+\.[0-9]+)[\"\x27]?\s*$', text, re.M)
    if not version or version[1] != manifest['version']:
        raise GateError('INVALID_MANIFEST', 'SKILL.md version differs from manifest.')


class GitHubSource:
    def __init__(self, repository, branch):
        self.repository = repository
        self.branch = branch
        self.deadline = time.monotonic() + 90

    def get(self, url, limit):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise GateError('NETWORK_ERROR', 'Update check exceeded its time budget; no offline fallback.')
        request = urllib.request.Request(url, headers={'User-Agent': NAME + '-update-gate',
            'Accept': 'application/vnd.github+json', 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
        try:
            with urllib.request.urlopen(request, timeout=min(15, remaining)) as response:
                target = urllib.parse.urlsplit(response.url)
                if target.scheme != 'https' or target.hostname not in ('api.github.com', 'raw.githubusercontent.com'):
                    raise GateError('NETWORK_ERROR', 'Only the configured GitHub HTTPS source is allowed.')
                data = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise GateError('NETWORK_ERROR', 'GitHub returned HTTP ' + str(exc.code) + '; no offline fallback.') from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GateError('NETWORK_ERROR', 'Could not reach GitHub; no offline fallback.') from exc
        if len(data) > limit:
            raise GateError('REMOTE_CHECKSUM_FAILED', 'Remote response exceeds its expected size.')
        return data

    def revision(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise GateError('NETWORK_ERROR', 'Update check exceeded its time budget.')
        ref = 'refs/heads/' + self.branch
        command = ['git', '-c', 'credential.helper=', '-c', 'credential.interactive=false',
                   'ls-remote', '--exit-code', '--refs',
                   'https://github.com/' + self.repository + '.git', ref]
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never',
               'GIT_TRACE': '0', 'GIT_TRACE_CURL': '0', 'GIT_CURL_VERBOSE': '0'}
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                    timeout=min(25, remaining), env=env)
        except FileNotFoundError as exc:
            raise GateError('GIT_REQUIRED', 'Install Git before using the update gate.') from exc
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise GateError('NETWORK_ERROR', 'GitHub remote check failed; no offline fallback.') from exc
        if result.returncode != 0:
            raise GateError('NETWORK_ERROR', 'GitHub remote branch is unreachable; no offline fallback.')
        rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
        if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != ref:
            raise GateError('INVALID_REMOTE_REVISION', 'Expected exactly the configured remote branch.')
        return rows[0][0]

    def file(self, revision, path):
        return self.get('https://raw.githubusercontent.com/' + self.repository + '/' + revision + '/' + path, MAX_FILE)


def atomic_json(path, value):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('xb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


@contextmanager
def locked(state_base):
    state_base.mkdir(parents=True, exist_ok=True)
    path = state_base / 'update.lock'
    token = uuid.uuid4().hex
    try:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(token)
    except FileExistsError as exc:
        raise GateError('LOCKED', 'An update lock exists. Do not remove a live lock or bypass the gate.') from exc
    try:
        yield
    finally:
        if path.is_file() and path.read_text(encoding='utf-8') == token:
            path.unlink()


def check_and_update(root, source=None, state_base=None):
    root = Path(root).absolute()
    if root.name != NAME or is_link(root) or root.resolve() != root or not root.is_dir():
        raise GateError('INVALID_INSTALL', 'Expected the real installed video-to-3d-previz directory.')
    if state_base is None:
        state_base = root.parent.parent / 'skill-updates' / (NAME + '-' + digest(str(root).encode())[:12])
    state_base = Path(state_base).absolute()
    if state_base.resolve() != state_base or state_base.is_relative_to(root) or root.is_relative_to(state_base):
        raise GateError('INVALID_INSTALL', 'Update state must be a separate real directory.')
    with locked(state_base):
        path = root / MANIFEST
        if not path.is_file():
            raise GateError('UNMANAGED_INSTALL', 'Release manifest missing. Install a verified release before using the skill.')
        local = validate_manifest(parse_json(path.read_bytes(), 'INVALID_MANIFEST'))
        state_path = state_base / 'state.json'
        state = parse_json(state_path.read_bytes(), 'INVALID_STATE') if state_path.exists() else None
        if state is not None and (not isinstance(state.get('revision'), str) or
                not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', state['revision']) or
                not isinstance(state.get('manifest_sha256'), str)):
            raise GateError('INVALID_STATE', 'Incomplete or corrupt update registration; do not bypass it.')
        if state and state.get('manifest_sha256') != digest(canonical(local)):
            raise GateError('LOCAL_MANIFEST_CHANGED', 'Installed manifest differs from the last verified release.')
        data = local_files(root, local)
        config = source_config(data['update-source.json'])
        validate_contents(data, local, config)
        if state and (state.get('source') != config or not isinstance(state.get('revision'), str)):
            raise GateError('INVALID_STATE', 'Registered source does not match this installation.')
        remote = source or GitHubSource(**config)
        revision = remote.revision()  # Deliberately never satisfied by a local timestamp or cached receipt.
        if not isinstance(revision, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', revision):
            raise GateError('INVALID_REMOTE_REVISION', 'GitHub did not provide a valid commit.')
        checked = datetime.now(timezone.utc).isoformat()
        result = {'status': 'current', 'revision': revision, 'version': local['version'],
                  'checked_at_utc': checked, 'skill_file': str(root / 'SKILL.md'),
                  'verified_files': len(local['files']), 'updater_changed': False, 'rerun_required': False}
        if state and state['revision'] == revision:
            atomic_json(state_path, {**state, 'checked_at_utc': checked})
            return result
        incoming_bytes = remote.file(revision, MANIFEST)
        if len(incoming_bytes) > MAX_FILE:
            raise GateError('INVALID_MANIFEST', 'Manifest too large.')
        incoming = validate_manifest(parse_json(incoming_bytes, 'INVALID_MANIFEST'))
        if state is None:
            if canonical(local) != canonical(incoming):
                raise GateError('UNREGISTERED_LOCAL', 'Unregistered local package differs from GitHub; preserve it and install a verified release.')
            atomic_json(state_path, {'source': config, 'revision': revision,
                        'manifest_sha256': digest(canonical(local)), 'checked_at_utc': checked})
            return {**result, 'registered': True}
        if version_tuple(incoming['version']) < version_tuple(local['version']):
            raise GateError('REMOTE_DOWNGRADE', 'Remote version is older; automatic downgrade is blocked.')
        candidate = {}
        for name, meta in incoming['files'].items():
            value = remote.file(revision, name)
            if len(value) != meta['size'] or digest(value) != meta['sha256']:
                raise GateError('REMOTE_CHECKSUM_FAILED', 'Checksum mismatch: ' + name)
            candidate[name] = value
        validate_contents(candidate, incoming, config)
        # Everything below is within the explicit installation and private update-state directories.
        batch = state_base / 'backups' / uuid.uuid4().hex
        stage, backup = batch / 'stage', batch / 'previous'
        stage.mkdir(parents=True)
        for name, value in candidate.items():
            dest = stage / name
            if not dest.resolve().is_relative_to(stage.resolve()):
                raise GateError('INVALID_MANIFEST', 'Staged path escaped its directory.')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(value)
        (stage / MANIFEST).write_bytes(incoming_bytes)
        local_files(stage, incoming)
        # Recheck before renaming: a separate editor may have changed files during the download.
        if digest(canonical(parse_json(path.read_bytes(), 'INVALID_MANIFEST'))) != digest(canonical(local)):
            raise GateError('LOCAL_MODIFIED', 'Manifest changed during download.')
        local_files(root, local)
        moved_old = moved_new = False
        try:
            if not backup.resolve().is_relative_to(state_base.resolve()) or stage.parent != backup.parent:
                raise GateError('INVALID_INSTALL', 'Unexpected update destination.')
            os.replace(root, backup)
            moved_old = True
            os.replace(stage, root)
            moved_new = True
            atomic_json(state_path, {'source': config, 'revision': revision,
                        'manifest_sha256': digest(canonical(incoming)), 'checked_at_utc': checked})
        except OSError as exc:
            try:
                if moved_new:
                    os.replace(root, stage)
                if moved_old:
                    os.replace(backup, root)
            except OSError as rollback:
                raise GateError('ROLLBACK_FAILED', 'Automatic rollback failed; preserve backup at ' + str(backup)) from rollback
            raise GateError('INSTALL_FAILED', 'Installation failed; the previous version was restored and remains blocked for this invocation.') from exc
        updater_changed = candidate['scripts/check_update.py'] != data['scripts/check_update.py']
        return {**result, 'status': 'updated', 'version': incoming['version'],
                'verified_files': len(incoming['files']), 'backup': str(backup),
                'updater_changed': updater_changed, 'rerun_required': updater_changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-dir', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = check_and_update(args.skill_dir)
    except GateError as exc:
        print(json.dumps({'status': 'blocked', 'code': exc.code, 'message': str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'code': 'UNEXPECTED_ERROR',
                          'message': 'Update check failed (' + type(exc).__name__ + '); do not use cached skill content.'}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
