#!/usr/bin/env python3
"""Check a standalone skill, or refresh its explicitly registered project source."""
import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import uuid

NAME = 'cinematic-previz'
STATE = '.skill-source.json'
LOCK = '.skill-update.lock'
BACKUPS = '.skill-backups'
IGNORED = {STATE, LOCK, BACKUPS, '.git', '__pycache__', 'node_modules', '.local'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, relative):
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or '..' in parts.parts or '\\' in relative or ':' in relative:
        raise ValueError(f'UNSAFE_PATH: {relative}')
    target = root.joinpath(*parts.parts)
    if not target.resolve().is_relative_to(root) or target.is_symlink():
        raise ValueError(f'UNSAFE_PATH: {relative}')
    return target


def source_folder(project):
    for candidate in (project/'skills'/NAME, project):
        manifest = candidate/'SKILL.md'
        if manifest.is_file() and re.search(r'^name:\s*'+NAME+r'\s*$', manifest.read_text(encoding='utf-8'), re.M):
            if not candidate.resolve().is_relative_to(project):
                raise ValueError('UNSAFE_PATH: skill source resolves outside the registered project')
            return candidate.resolve()
    raise ValueError('SOURCE_NOT_FOUND: project must contain cinematic-previz/SKILL.md or skills/cinematic-previz/SKILL.md')


def snapshot(source):
    result = {}
    for path in sorted(source.rglob('*')):
        rel = path.relative_to(source)
        if any(part in IGNORED or (part.startswith('.') and part != '.gitignore') for part in rel.parts):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError(f'UNSAFE_PATH: source link {rel}')
        if path.is_file():
            result[rel.as_posix()] = path.read_bytes()
    return result


def standalone_status(target):
    """A downloaded skill is usable without a developer's private registration.

    This fingerprints local contents only: it neither updates nor claims to have
    checked a remote repository. Existing registration errors never reach here.
    """
    target = Path(target).resolve()
    if safe_path(target, LOCK).exists():
        raise ValueError('BUSY: another skill update owns the lock; do not remove a live lock')
    if not (target/'SKILL.md').is_file() or source_folder(target) != target:
        raise ValueError('SOURCE_NOT_FOUND: standalone target must contain its own SKILL.md')
    hashes = {name:digest(data) for name,data in snapshot(target).items()}
    return {'status':'current', 'mode':'standalone', 'remote_checked':False,
            'project_root':None, 'source':str(target), 'target':str(target),
            'skill_file':str(target/'SKILL.md'), 'changed':[],
            'revision':digest(json.dumps(hashes,sort_keys=True).encode())}


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.skill-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync(project, target, *, register=False, check=False):
    project, target = Path(project).resolve(), Path(target).resolve()
    source = source_folder(project)
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('OVERLAPPING_PATHS: project skill and installed target must be separate')
    lock = safe_path(target, LOCK)
    if lock.exists():
        raise ValueError('BUSY: another skill update owns the lock; do not remove a live lock')
    if not check:
        target.mkdir(parents=True, exist_ok=True)
    try:
        guard = nullcontext() if check else lock.open('x', encoding='utf-8')
        with guard:
            state_path = safe_path(target, STATE)
            old_state_bytes = state_path.read_bytes() if state_path.exists() else None
            state = json.loads(old_state_bytes) if old_state_bytes else None
            if state and (state.get('schema') != 1 or not isinstance(state.get('files'), dict)):
                raise ValueError('INVALID_STATE: unsupported local update manifest')
            if not state and not register:
                raise ValueError('REGISTER_REQUIRED: first register an explicit trusted project root')
            if state and Path(state['project_root']).resolve() != project and not register:
                raise ValueError('SOURCE_CHANGED: changing the registered project requires --register')
            incoming = snapshot(source)
            hashes = {name:digest(data) for name,data in incoming.items()}
            revision = digest(json.dumps(hashes, sort_keys=True).encode())
            previous = state['files'] if state else {}
            for name in previous:
                safe_path(target, name)
                if any(p in IGNORED or p.startswith('.') for p in PurePosixPath(name).parts) and name != '.gitignore':
                    raise ValueError(f'UNSAFE_PATH: reserved manifest entry {name}')
            originals, changes, conflicts = {}, [], []
            for name in sorted(set(previous)|set(incoming)):
                path = safe_path(target, name)
                current = path.read_bytes() if path.exists() else None
                current_hash = digest(current) if current is not None else None
                expected = previous.get(name)
                desired = hashes.get(name)
                if current_hash == desired:
                    continue
                # Explicit first registration backs up and adopts an existing installation.
                if state and current_hash != expected:
                    conflicts.append(name)
                elif not state and current is not None and not register:
                    conflicts.append(name)
                originals[name] = current
                changes.append(name)
            if conflicts:
                raise ValueError('LOCAL_CONFLICT: merge installed edits into the project first: '+', '.join(conflicts))
            report = {'status':'update_available' if changes else 'current', 'mode':'project',
                      'remote_checked':False, 'project_root':str(project),
                      'source':str(source), 'target':str(target), 'skill_file':str(target/'SKILL.md'),
                      'revision':revision, 'changed':changes}
            if check:
                return report
            # Detect edits while the source snapshot was being prepared.
            if snapshot(source) != incoming:
                raise ValueError('SOURCE_CHANGED: project files changed during update; retry the check')
            backup = None
            if changes:
                backup = safe_path(target, BACKUPS+'/'+uuid.uuid4().hex)
                backup.mkdir(parents=True)
                for name,data in originals.items():
                    if data is not None:
                        atomic_write(safe_path(backup,name),data)
                if old_state_bytes:
                    atomic_write(backup/STATE,old_state_bytes)
            written = []
            try:
                for name in changes:
                    path = safe_path(target,name)
                    current = path.read_bytes() if path.exists() else None
                    if current != originals[name]:
                        raise ValueError('LOCAL_CONFLICT: target changed during update: '+name)
                    if name in incoming:
                        atomic_write(path,incoming[name])
                    else:
                        path.unlink()
                    written.append(name)
                new_state = {'schema':1, 'project_root':str(project), 'source':str(source), 'revision':revision, 'files':hashes}
                atomic_write(state_path,(json.dumps(new_state,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
            except BaseException:
                # Restore only files still matching this updater's writes; preserve concurrent edits.
                for name in reversed(written):
                    path = safe_path(target,name)
                    current = path.read_bytes() if path.exists() else None
                    if current != incoming.get(name):
                        continue
                    if originals[name] is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path,originals[name])
                raise
            report['status'] = 'updated' if changes else 'current'
            if backup:
                report['backup'] = str(backup)
            return report
    except FileExistsError as exc:
        raise ValueError('BUSY: another update acquired the lock') from exc
    finally:
        # A check-only run never creates or removes a lock.
        if not check and 'guard' in locals() and getattr(guard,'closed',False):
            lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--target', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--register', action='store_true', help='Register an explicitly chosen project; first install backs up overwritten files')
    parser.add_argument('--check', action='store_true', help='Compare only; do not update files or registration')
    args = parser.parse_args()
    try:
        project = args.project_root or os.environ.get('CINEMATIC_PREVIZ_PROJECT')
        if not project:
            state_path = safe_path(args.target.resolve(), STATE)
            if not state_path.exists():
                if args.register:
                    raise ValueError('REGISTER_REQUIRED: --register requires an explicit --project-root')
                print(json.dumps(standalone_status(args.target),ensure_ascii=False))
                return 0
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if not isinstance(state,dict) or not state.get('project_root'):
                raise ValueError('INVALID_STATE: registration must declare project_root')
            project = state['project_root']
        print(json.dumps(sync(project,args.target,register=args.register,check=args.check),ensure_ascii=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'error','message':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
