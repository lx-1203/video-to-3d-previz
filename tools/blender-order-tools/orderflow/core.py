"""Portable job data and delivery checks. No Blender or media imports here."""
import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import struct
import uuid
import zipfile
import zlib

MODE_SETTINGS = {'preview': {'width': 1280, 'height': 720}, 'final': {'width': 3840, 'height': 2160}}
STATES = {'queued', 'rendering', 'encoding', 'verifying', 'complete', 'failed', 'cancelled'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value in {'.', '..'} or value.endswith((' ', '.')):
        raise ValueError('name must be a simple file name, without path separators')
    return value.strip()


def inside(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    return path != parent and path.is_relative_to(parent)


def _file(value, key):
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise ValueError(f'{key}: an existing file is required')
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f'{key}: file does not exist: {path}')
    return str(path)


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError('job must be an object')
    fields = ('schema_version', 'name', 'source_blend', 'snapshot_blend', 'reference_video', 'output_root', 'frame_start', 'frame_end', 'scene_frame_start', 'fps_num', 'fps_den', 'source_start_seconds', 'mode', 'camera', 'ffmpeg', 'media_python')
    missing = [key for key in fields if key not in spec]
    if missing:
        raise ValueError('Missing fields: ' + ', '.join(missing))
    result = dict(spec)
    if type(result['schema_version']) is not int or result['schema_version'] != 1:
        raise ValueError('Unsupported schema_version')
    result['name'] = _name(result['name'])
    for key in ('frame_start', 'frame_end', 'scene_frame_start', 'fps_num', 'fps_den'):
        if type(result[key]) is not int or result[key] < 1:
            raise ValueError(f'{key} must be a positive integer')
    if not result['scene_frame_start'] <= result['frame_start'] <= result['frame_end']:
        raise ValueError('Invalid frame range')
    offset = result['source_start_seconds']
    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0:
        raise ValueError('source_start_seconds must be finite and nonnegative')
    if result['mode'] not in MODE_SETTINGS:
        raise ValueError('mode must be preview or final')
    if not isinstance(result['camera'], str) or not result['camera'].strip():
        raise ValueError('A camera name is required')
    for key in ('source_blend', 'snapshot_blend', 'reference_video', 'ffmpeg', 'media_python'):
        result[key] = _file(result[key], key)
    if result['snapshot_blend'] == result['source_blend']:
        raise ValueError('snapshot must not overwrite the source')
    if not str(result['output_root']):
        raise ValueError('output_root is required')
    result['output_root'] = str(Path(result['output_root']).expanduser().resolve())
    if Path(result['output_root']).exists() and not Path(result['output_root']).is_dir():
        raise ValueError('output_root is not a directory')
    if not inside(result['snapshot_blend'], result['output_root']):
        raise ValueError('snapshot must be within output_root')
    result['model_standard'] = _file(result['model_standard'], 'model_standard') if result.get('model_standard') else ''
    result.update(MODE_SETTINGS[result['mode']])
    return result


def new_job_dir(output_root, name):
    name = _name(name)
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    job = root / (name + '_' + uuid.uuid4().hex)
    job.mkdir(exist_ok=False)
    return job


def write_job(job_dir, spec):
    spec = validate_spec(spec)
    job_dir = Path(job_dir).resolve()
    if job_dir.parent != Path(spec['output_root']) or Path(spec['snapshot_blend']) != job_dir/'snapshot.blend':
        raise ValueError('job must be a new direct child of output_root, with snapshot.blend')
    if not re.fullmatch(re.escape(spec['name']) + r'_[0-9a-f]{32}', job_dir.name):
        raise ValueError('job directory must be created by new_job_dir')
    if any((job_dir/name).exists() for name in ('job.json', 'scene.blend', 'white.mp4', 'comparison.mp4')):
        raise FileExistsError('job outputs already exist')
    if any(inside(spec[key], job_dir) for key in ('source_blend', 'reference_video')):
        raise ValueError('source inputs must not be job outputs')
    spec['job_id'] = job_dir.name
    spec['input_sha256'] = {key: sha256(spec[key]) for key in ('source_blend', 'snapshot_blend', 'reference_video', 'model_standard') if spec.get(key)}
    path = job_dir/'job.json'
    # Exclusive create protects against two launchers targeting the same job.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(spec, stream, ensure_ascii=False, indent=2)
    write_status(job_dir, 'queued', message='Frozen snapshot queued', mode=spec['mode'], visual_status='unverified')
    return path


def load_job(path):
    path = Path(path).resolve()
    spec = validate_spec(read_json(path))
    if path.name != 'job.json' or path.parent.name != spec.get('job_id') or Path(spec['snapshot_blend']).parent != path.parent or path.parent.parent != Path(spec['output_root']):
        raise ValueError('job path and frozen job identity disagree')
    if not isinstance(spec.get('input_sha256'), dict):
        raise ValueError('Job has no input hashes')
    return spec


def write_status(job_dir, state, **details):
    if state not in STATES:
        raise ValueError('Unknown job state: ' + str(state))
    details.update(state=state, updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    atomic_json(Path(job_dir)/'status.json', details)


def read_status(job_dir):
    path = Path(job_dir)/'status.json'
    return read_json(path) if path.is_file() else {'state': 'queued', 'message': 'No worker status yet'}


def blender_worker_command(blender, job_path):
    return [str(blender), '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--python', str(Path(__file__).with_name('worker.py')), '--', '--job', str(Path(job_path).resolve())]


def _png_dimensions(path):
    data = Path(path).read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Invalid PNG signature')
    cursor, compressed, header, ended = 8, bytearray(), None, False
    while cursor + 12 <= len(data):
        length = struct.unpack('>I', data[cursor:cursor+4])[0]
        kind = data[cursor+4:cursor+8]
        payload = data[cursor+8:cursor+8+length]
        trailer = data[cursor+8+length:cursor+12+length]
        if len(trailer) != 4 or zlib.crc32(kind+payload) != struct.unpack('>I', trailer)[0]:
            raise ValueError('Truncated PNG or CRC mismatch')
        if kind == b'IHDR':
            if header is not None or length != 13:
                raise ValueError('Invalid PNG header')
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT': compressed.extend(payload)
        elif kind == b'IEND': ended = True; break
        cursor += length+12
    if header is None or not ended or not compressed:
        raise ValueError('Incomplete PNG')
    w, h, depth, color, compression, filtering, interlace = header
    channels = {0:1, 2:3, 3:1, 4:2, 6:4}.get(color)
    if not channels or not w or not h or interlace or compression or filtering:
        raise ValueError('Unsupported PNG layout (noninterlaced required)')
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        raise ValueError('Corrupt PNG data') from exc
    row_size = (w*channels*depth+7)//8
    if len(raw) != h*(row_size+1) or any(raw[row*(row_size+1)] > 4 for row in range(h)):
        raise ValueError('Corrupt PNG scanlines')
    return w, h


def inspect_png_frames(frames_dir, start, end, width, height):
    frames_dir = Path(frames_dir).resolve()
    expected_names = {f'frame_{frame:06d}.png' for frame in range(start, end+1)}
    if {path.name for path in frames_dir.glob('frame_*.png')} != expected_names:
        raise ValueError('PNG frame set differs from the frozen sequence')
    records = []
    for frame in range(start, end+1):
        path = frames_dir/f'frame_{frame:06d}.png'
        if not path.is_file() or not inside(path, frames_dir):
            raise ValueError(f'Missing or escaped frame: {path}')
        try:
            dimensions = _png_dimensions(path)
        except (OSError, ValueError, struct.error) as exc:
            raise ValueError(f'Invalid frame {path}: {exc}') from exc
        if dimensions != (width, height):
            raise ValueError(f'Frame {path.name} is {dimensions}, expected {(width,height)}')
        records.append(dict(name=path.name, frame=frame, sha256=sha256(path), path=str(path)))
    return records


def package_submission(job_dir, acceptance=None):
    job_dir = Path(job_dir).resolve()
    if read_status(job_dir)['state'] != 'complete':
        raise ValueError('Job is not technically complete')
    report = read_json(job_dir/'verification.json')
    manifest = read_json(job_dir/'technical_manifest.json')
    job = load_job(job_dir/'job.json')
    if job['mode'] != 'final':
        raise ValueError('预览不能生成交稿包，请先完成原生 4K 导出。')
    if any(item.get('mode') != 'final' or item.get('native_render_dimensions') != [3840,2160] for item in (report, manifest)):
        raise ValueError('交稿包模式或原生 4K 尺寸记录不一致，请重新导出。')
    if report.get('technical_passed') is not True or manifest.get('technical_passed') is not True:
        raise ValueError('Technical verification is not passed')
    members = manifest.get('members', {})
    required = {'scene.blend', 'white.mp4', 'comparison.mp4', 'usage.txt', 'verification.json'}
    if not isinstance(members, dict) or not required.issubset(members):
        raise ValueError('Incomplete explicit delivery manifest')
    for name, expected in members.items():
        if not isinstance(name, str) or '\\' in name or PurePosixPath(name).as_posix() != name or any(part in ('..', '.') for part in PurePosixPath(name).parts):
            raise ValueError(f'Unsafe delivery member name: {name}')
        path = job_dir/name
        allowed = name in required or name.startswith('dependencies/') or name.startswith('model_standard/')
        if not allowed or not inside(path, job_dir) or not path.is_file() or sha256(path) != expected:
            raise ValueError(f'Stale or unsafe delivery member: {name}')
    if report.get('file_sha256', {}).get('scene.blend') != members['scene.blend']:
        raise ValueError('Verification and delivery scene disagree')
    from . import acceptance as overlay_acceptance
    gate_result = overlay_acceptance.validate_for_package(job_dir, acceptance)
    overlay_acceptance.assert_unchanged(gate_result)
    destination = job_dir/(job_dir.name + '_' + uuid.uuid4().hex[:8] + '.zip')
    # QA reports stay beside the job, never in the customer-facing archive.
    expected_members = {name: digest for name, digest in members.items() if name != 'verification.json'}
    created = False
    try:
        # Reserve exclusively before claiming ownership for failure cleanup.
        with destination.open('xb') as output:
            created = True
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for name in expected_members:
                    archive.write(job_dir/name, name)
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None or len(archive.namelist()) != len(expected_members) or set(archive.namelist()) != set(expected_members):
                raise ValueError('ZIP CRC or exact member check failed')
            for name, expected in expected_members.items():
                digest = hashlib.sha256()
                with archive.open(name) as stream:
                    for data in iter(lambda: stream.read(1024*1024), b''): digest.update(data)
                if digest.hexdigest() != expected:
                    raise ValueError('ZIP member SHA mismatch: ' + name)
        overlay_acceptance.assert_unchanged(gate_result)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return destination
