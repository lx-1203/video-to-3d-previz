#!/usr/bin/env python3
"""Self-contained file installer; never runs a model, plugin or dependency installer.

The builder copies this exact standard-library file to bundle-root install.py.
Using the main skill still requires its normal online update gate afterwards.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys


BUNDLE_TYPE = 'order_workbench_private_windows_nvidia'
ROOT_FILES = {'install.py', 'install.bat', 'INSTALL.md'}
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'CLOCK$'} | {
    prefix + number for prefix in ('COM', 'LPT') for number in '123456789¹²³'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key: ' + key)
        value[key] = item
    return value


def _relative(name):
    if not isinstance(name, str) or not name or '\\' in name or name.startswith('/'):
        raise ValueError('Expected a canonical relative path: ' + repr(name))
    parts = name.split('/')
    for part in parts:
        if (part in ('', '.', '..') or part.rstrip(' .') != part
                or re.search(r'[\x00-\x1f<>:"|?*]', part)):
            raise ValueError('Unsafe relative path: ' + name)
        if part.split('.')[0].upper() in RESERVED:
            raise ValueError('Windows reserved path: ' + name)
    return PurePosixPath(name)


def _path_set(names):
    """Reject Windows aliases and a file being another member's parent."""
    spellings = {}
    files = set()
    for name in names:
        parts = _relative(name).parts
        for length in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:length])
            folded = prefix.casefold()
            if folded in spellings and spellings[folded] != prefix:
                raise ValueError('Case-insensitive path collision: ' + prefix)
            spellings[folded] = prefix
            if length < len(parts) and folded in files:
                raise ValueError('File/directory path collision: ' + prefix)
        folded = name.casefold()
        if any(key.startswith(folded + '/') for key in spellings):
            raise ValueError('File/directory path collision: ' + name)
        files.add(folded)


def _reject_link(path):
    if path.is_symlink():
        raise ValueError('Symbolic link is not allowed: ' + str(path))
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    except FileNotFoundError:
        return
    if attributes & 0x400:  # Windows FILE_ATTRIBUTE_REPARSE_POINT, including junctions.
        raise ValueError('Reparse link is not allowed: ' + str(path))


def _absolute(path):
    # abspath preserves link spelling so checks cannot be bypassed by resolve().
    return Path(os.path.abspath(os.fspath(path)))


def _check_ancestors(path):
    for item in reversed((path, *path.parents)):
        _reject_link(item)
        if item.exists() and item != path and not item.is_dir():
            raise ValueError('Destination parent is a file: ' + str(item))
        if item.parent.is_dir() and item.name:
            aliases = [child.name for child in item.parent.iterdir()
                       if child.name.casefold() == item.name.casefold()]
            if any(name != item.name for name in aliases):
                raise ValueError('Destination case collision: ' + str(item))


def _check_existing(path, data):
    _check_ancestors(path)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise ValueError('Existing destination conflict; refusing overwrite: ' + str(path))
        return True
    return False


def _inventory(root):
    _check_ancestors(root)
    if not root.is_dir():
        raise ValueError('Bundle root must be an existing directory')
    files = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        for name in directories + filenames:
            path = Path(current) / name
            _reject_link(path)
            if not path.is_file() and not path.is_dir():
                raise ValueError('Unsupported bundle member: ' + str(path))
        files.extend((Path(current) / name).relative_to(root).as_posix() for name in filenames)
    _path_set(files)
    return set(files)


def _skill_name(record, label):
    if not isinstance(record, dict) or not isinstance(record.get('name'), str):
        raise ValueError('Invalid ' + label + ' name')
    name = record['name']
    if len(_relative(name).parts) != 1:
        raise ValueError('Skill name must be one path component')
    return name


def _manifest(root):
    actual = _inventory(root)
    if 'bundle-manifest.json' not in actual:
        raise ValueError('Missing bundle-manifest.json')
    raw = (root / 'bundle-manifest.json').read_bytes()
    try:
        manifest = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                              parse_constant=lambda value: (_ for _ in ()).throw(
                                  ValueError('Invalid JSON number: ' + value)))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid manifest JSON: ' + str(error)) from error
    if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or manifest.get('bundle_type') != BUNDLE_TYPE):
        raise ValueError('Invalid manifest schema_version or bundle_type')
    public = _skill_name(manifest.get('public_skill'), 'public_skill')
    private = _skill_name(manifest.get('private_skill'), 'private_skill')
    version = manifest['public_skill'].get('version')
    if not isinstance(version, str) or not version.strip() or public.casefold() == private.casefold():
        raise ValueError('Invalid public version or duplicate skill names')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Manifest files must be a nonempty object')
    _path_set(files)
    registrations = {}
    for label, folder, status, suffix in (
            ('profiles', 'payload/profiles/', 'candidate_requires_calibration', '.json'),
            ('addons', 'payload/addons/', 'packaged_not_installed', '.zip')):
        records = manifest.get(label)
        if not isinstance(records, list) or not records:
            raise ValueError('Manifest ' + label + ' must be a nonempty list')
        for record in records:
            if not isinstance(record, dict) or record.get('status') != status:
                raise ValueError('Invalid ' + label + ' registration status')
            name = record.get('path')
            _relative(name)
            if (not name.startswith(folder) or not name.lower().endswith(suffix)
                    or len(PurePosixPath(name).parts) != 3):
                raise ValueError('Invalid ' + label + ' registration path')
            if name.casefold() in registrations:
                raise ValueError('Duplicate registered profile/addon path')
            registrations[name.casefold()] = name
            if name not in files:
                raise ValueError('Registered member missing from files: ' + name)
    required = {f'payload/skills/{public}/SKILL.md', f'payload/skills/{private}/SKILL.md',
                f'payload/skills/{public}/scripts/check_update.py',
                f'payload/skills/{public}/scripts/motion/motion_workbench.py',
                f'payload/skills/{public}/scripts/motion/setup_backend.py',
                f'payload/skills/{private}/assets/client_character.blend',
                f'payload/skills/{private}/assets/client_source_original.blend'} | ROOT_FILES
    if not required.issubset(files):
        raise ValueError('Missing required bundle member: ' + ', '.join(sorted(required - files.keys())))
    for name, record in files.items():
        if (not isinstance(record, dict) or type(record.get('size')) is not int or record['size'] < 0
                or not isinstance(record.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', record['sha256'])
                or not isinstance(record.get('role'), str) or not record['role'].strip()):
            raise ValueError('Invalid file size/hash/role record: ' + name)
        if PurePosixPath(name).name.casefold() == 'config.local.json':
            raise ValueError('Creator config.local.json cannot be installed')
        skill_prefixes = (f'payload/skills/{public}/', f'payload/skills/{private}/')
        if (name not in ROOT_FILES and not name.startswith(skill_prefixes)
                and registrations.get(name.casefold()) != name):
            raise ValueError('Unexpected bundle member path: ' + name)
    expected = set(files) | {'bundle-manifest.json'}
    if actual != expected:
        raise ValueError('Bundle inventory mismatch; unlisted or missing members: ' +
                         ', '.join(sorted(actual ^ expected)))
    contents = {}
    for name, record in sorted(files.items()):
        data = (root / name).read_bytes()
        if len(data) != record['size'] or _sha(data) != record['sha256']:
            raise ValueError('File size/SHA256 hash mismatch: ' + name)
        contents[name] = data
    return manifest, _sha(raw), contents


def _destination(name, target, skills_root):
    parts = PurePosixPath(name).parts
    if parts[:2] == ('payload', 'skills'):
        return skills_root.joinpath(*parts[2:])
    if parts[0] == 'payload':
        return target.joinpath(*parts[1:])
    return target / name


def _catalog(manifest, manifest_sha, target, skills_root):
    public = skills_root / manifest['public_skill']['name']
    private = skills_root / manifest['private_skill']['name']
    cli = public / 'scripts/motion/motion_workbench.py'
    setup = public / 'scripts/motion/setup_backend.py'
    gate = public / 'scripts/check_update.py'
    workspace = target / 'backend'
    def asset(name):
        rel = 'payload/skills/' + manifest['private_skill']['name'] + '/assets/' + name
        return {'path': str(private / 'assets' / name), 'sha256': manifest['files'][rel]['sha256']}
    return {
        'schema_version': 1, 'bundle_type': BUNDLE_TYPE, 'bundle_manifest_sha256': manifest_sha,
        'target': str(target), 'skills_root': str(skills_root),
        'public_skill': dict(manifest['public_skill'], path=str(public)),
        'private_skill': dict(manifest['private_skill'], path=str(private)),
        'template': asset('client_character.blend'),
        'source_original': asset('client_source_original.blend'),
        'cli': {'path': str(cli), 'command': [sys.executable, str(cli)]},
        'profiles': [dict(record, path=str(_destination(record['path'], target, skills_root)))
                     for record in sorted(manifest['profiles'], key=lambda item: item['path'])],
        'addons': [dict(record, path=str(_destination(record['path'], target, skills_root)), installed=False)
                   for record in sorted(manifest['addons'], key=lambda item: item['path'])],
        'backend_workspace': str(workspace),
        'setup': {'script': str(setup), 'blender': None, 'ffmpeg': None,
                  'required_local_arguments': ['--blender', '--ffmpeg'],
                  'command_template': [sys.executable, str(setup), '--workspace', str(workspace),
                                       '--blender', '<LOCAL_BLENDER_EXE>', '--ffmpeg', '<LOCAL_FFMPEG_EXE>'],
                  'executed': False, 'weights_downloaded': False},
        'online_gate': {'status': 'not_run', 'required_before_skill_use': True,
                        'command': [sys.executable, str(gate)],
                        'instruction': 'Run the normal online gate before AI uses the main skill. '
                                       'Continue only on exit 0, current/updated and rerun_required=false. '
                                       'Preserve edits and live locks; do not bypass failures.'},
        'ready_for_inference': False,
    }


def _prepare(bundle_root, target, skills_root):
    root, target, skills_root = map(_absolute, (bundle_root, target, skills_root))
    manifest, manifest_sha, contents = _manifest(root)
    catalog = _catalog(manifest, manifest_sha, target, skills_root)
    writes = []
    for name, data in sorted(contents.items()):
        destination = _destination(name, target, skills_root)
        writes.append((destination, data, name))
    installation = {
        'schema_version': 1, 'bundle_manifest_sha256': manifest_sha,
        'target': str(target), 'skills_root': str(skills_root),
        'status': 'files_installed_dependencies_unverified',
        'online_gate': catalog['online_gate'], 'ready_for_inference': False,
        'files': {str(path): {'size': len(data), 'sha256': _sha(data), 'bundle_path': name}
                  for path, data, name in writes},
        'catalog_sha256': _sha(_json_bytes(catalog)),
    }
    writes.extend(((target / 'catalog.json', _json_bytes(catalog), 'generated:catalog'),
                   (target / 'installation.json', _json_bytes(installation), 'generated:installation')))
    by_volume = {}
    for path, _, _ in writes:
        by_volume.setdefault(path.anchor.casefold(), []).append(path.relative_to(path.anchor).as_posix())
    for paths in by_volume.values():
        _path_set(paths)
    destinations = set()
    operations = []
    for path, data, origin in writes:
        if path == root or root in path.parents:
            raise ValueError('Destination overlaps source bundle: ' + str(path))
        folded = str(path).casefold()
        if folded in destinations:
            raise ValueError('Duplicate destination path collision: ' + str(path))
        destinations.add(folded)
        exists = _check_existing(path, data)
        operations.append({'destination': str(path), 'origin': origin, 'size': len(data),
                           'sha256': _sha(data), 'action': 'reuse' if exists else 'create'})
    plan = {'ok': True, 'code': 'PLAN_ONLY', 'target': str(target), 'skills_root': str(skills_root),
            'bundle_manifest_sha256': manifest_sha, 'operations': operations,
            'catalog': str(target / 'catalog.json'), 'installation': str(target / 'installation.json'),
            'online_gate': catalog['online_gate'], 'ready_for_inference': False,
            'note': 'File installation only. Online gate, local dependencies, plugin registration '
                    'and actual video/visual validation have not run.'}
    return plan, writes


def plan_install(bundle_root, target, skills_root):
    """Validate the complete source and every destination without creating files."""
    return _prepare(bundle_root, target, skills_root)[0]


def install_bundle(bundle_root, target, skills_root, plan_only=False):
    """Install a validated byte snapshot, refusing any differing existing file."""
    plan, writes = _prepare(bundle_root, target, skills_root)
    if plan_only:
        return plan
    for path, data, _ in writes:
        # Repeat protection immediately before writing to detect concurrent changes.
        if _check_existing(path, data):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(data)
    return dict(plan, code='FILES_INSTALLED_DEPENDENCIES_UNVERIFIED')


def main(argv=None):
    home = Path(os.environ.get('USERPROFILE') or Path.home())
    codex_home = os.environ.get('CODEX_HOME')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=home / 'OrderWorkbench')
    parser.add_argument('--skills-root', type=Path,
                        default=(Path(codex_home) if codex_home else home / '.codex') / 'skills')
    parser.add_argument('--plan-only', action='store_true', help='Validate all files and targets; write nothing.')
    args = parser.parse_args(argv)
    try:
        result = install_bundle(Path(__file__).absolute().parent, args.target, args.skills_root, args.plan_only)
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'code': 'INSTALL_REFUSED', 'message': str(error),
                          'online_gate': {'status': 'not_run'}, 'ready_for_inference': False}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
