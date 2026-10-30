#!/usr/bin/env python3
"""Local, model-independent motion workflow. No shell commands or model API keys."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent


class JobSnapshot(dict):
    """JSON data with the exact on-disk revision kept outside serialized fields."""
    pass


class WorkbenchError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def read_config(path):
    c = read_json(path)
    if not isinstance(c, dict) or c.get('schema_version') != 1 or not isinstance(c.get('backend', {}), dict):
        raise WorkbenchError('INVALID_CONFIG', 'Expected schema_version 1 and an object for backend.')
    for key in ('blender', 'ffmpeg', 'ffprobe', 'media_python'):
        if key in c and not isinstance(c[key], str):
            raise WorkbenchError('INVALID_CONFIG', key + ' must be a path string.')
    for key, value in c.get('backend', {}).items():
        if key in ('python', 'root', 'motion_config', 'soma_assets', 'revision', 'device') and not isinstance(value, str):
            raise WorkbenchError('INVALID_CONFIG', 'backend.' + key + ' must be a string.')
    return c


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def artifact(path):
    p = Path(path).resolve()
    if not p.is_file():
        raise WorkbenchError('MISSING_FILE', str(p))
    return {'path': str(p), 'sha256': digest(p), 'bytes': p.stat().st_size}


def intact(a):
    p = Path(a['path'])
    return p.is_file() and p.stat().st_size == a['bytes'] and digest(p) == a['sha256']


def derive_state(job):
    if any(not intact(a) for a in job['inputs'].values()):
        return 'stale_inputs'
    if job.get('failure'):
        return 'blocked'
    outputs = job['outputs']
    if any(not intact(a) for a in outputs.values()):
        return 'stale_artifacts'
    if 'candidate' in outputs:
        review = job.get('review')
        evidence_key = review.get('evidence_artifact_key', 'preview') if review else 'preview'
        if (review and evidence_key in ('preview', 'sequence_preview_video') and evidence_key in outputs
                and review['candidate_sha256'] == outputs['candidate']['sha256']
                and review['preview_sha256'] == outputs[evidence_key]['sha256']):
            return 'motion_candidate_reviewed' if review['verdict'] == 'passed' else 'needs_revision'
        return 'candidate_ready'
    return 'motion_ready' if 'motion' in outputs else 'initialized'


def next_action(job, directory):
    state = derive_state(job)
    if state == 'initialized' and 'selected_motion' in job['inputs']:
        return {'command':[sys.executable,str(HERE/'motion_workbench.py'),'attach-motion',
                           '--job',str(directory),'--motion',job['inputs']['selected_motion']['path']],
                'reason':'Explicitly selected external motion; do not start video inference.'}
    command = {'initialized': 'extract', 'motion_ready': 'retarget'}.get(state)
    if command:
        return {'command': [sys.executable, str(HERE / 'motion_workbench.py'), command,
                            '--job', str(directory)], 'reason': state}
    if state == 'candidate_ready':
        if 'sequence_preview_video' not in job['outputs']:
            return {'command': [sys.executable, str(HERE / 'motion_workbench.py'), 'preview',
                                '--job', str(directory)], 'reason': 'Generate the continuous source-audio comparison before sequence review.'}
        return {'command': None, 'reason': 'Inspect the actual sequence comparison and contact sheets, compare with the source, then record the observed review scope. Files do not prove visual correctness.'}
    if state == 'blocked':
        return {'command': None, 'reason': job['failure'],
                'recovery': 'Resolve the recorded cause; retry that same stage. Imported motion is a separate explicitly selected route.'}
    return {'command': None, 'reason': state}


def load_job(directory):
    p = Path(directory).resolve()
    raw = (p / 'job.json').read_bytes()
    j = JobSnapshot(json.loads(raw))
    j._snapshot_sha256 = hashlib.sha256(raw).hexdigest()
    if j.get('schema_version') != 1:
        raise WorkbenchError('SCHEMA_MISMATCH', 'Expected job schema_version 1')
    return p, j


def result(job, directory):
    state = derive_state(job)
    return {'ok': state not in ('blocked', 'stale_inputs', 'stale_artifacts'),
            'code': state.upper(), 'state': state, 'job': str(directory),
            'scope': 'motion candidate only; full scene/camera/audio/delivery are separate',
            'failure': job.get('failure'), 'next_action': next_action(job, directory),
            'artifacts': job['outputs'], 'review': job.get('review'),
            'preview_scope':job.get('preview_scope'), 'motion_provenance':job.get('motion_provenance'),
            'sequence_preview_scope':job.get('sequence_preview_scope'),
            'tracking_quality':job.get('tracking_quality')}


@contextmanager
def job_lock(directory, stage):
    lock = directory / '.operation.lock'
    try:
        f = lock.open('x', encoding='utf-8')
    except FileExistsError:
        raise WorkbenchError('BUSY', 'An operation lock exists. Inspect the process/log; do not start another writer.')
    try:
        with f:
            f.write(json.dumps({'pid': os.getpid(), 'stage': stage, 'started_at': now()}))
        yield
    finally:
        lock.unlink()


@contextmanager
def operation(directory, job, stage):
    with job_lock(directory, stage):
        expected = getattr(job, '_snapshot_sha256', None)
        if expected is not None and digest(directory / 'job.json') != expected:
            # Reject before entering the failure/save handlers: stale state must
            # never replace a newer success, even with a recorded failure.
            raise WorkbenchError('STALE_JOB', 'Job changed since it was read; reload status and retry against the current artifacts.')
        try:
            if any(not intact(a) for a in job['inputs'].values()):
                raise WorkbenchError('STALE_INPUTS', 'An input changed; initialize a new job from the intended sources.')
            yield
            job['failure'] = None
        except Exception as exc:
            if stage in ('extract', 'attach-motion'):
                invalidate(job, ('motion', 'motion_metadata', 'candidate', 'preview', 'verification', 'bridge_report'))
                job['motion_provenance'] = None
            elif stage == 'retarget':
                invalidate(job, ('candidate', 'preview', 'verification', 'bridge_report'))
            elif stage == 'preview':
                invalidate_sequence_preview(job)
            job['failure'] = {'stage': stage, 'code': getattr(exc, 'code', 'OPERATION_FAILED'),
                              'message': str(exc), 'at': now()}
            raise
        finally:
            job['updated_at'] = now()
            write_json(directory / 'job.json', job)
            if expected is not None:
                job._snapshot_sha256 = digest(directory / 'job.json')


def inspect_bvh(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    parts = re.split(r'(?m)^\s*MOTION\s*$', text, maxsplit=1)
    if len(parts) != 2 or not re.search(r'\bROOT\s+\S+', parts[0]):
        raise WorkbenchError('INVALID_BVH', 'Missing BVH hierarchy or motion')
    rows = [s.strip() for s in parts[1].strip().splitlines() if s.strip()]
    try:
        frames = int(re.fullmatch(r'Frames:\s*(\d+)', rows[0])[1])
        dt = float(re.fullmatch(r'Frame Time:\s*(\S+)', rows[1])[1])
        channels = sum(int(n) for n in re.findall(r'\bCHANNELS\s+(\d+)', parts[0]))
        if frames < 2 or not math.isfinite(dt) or dt <= 0 or channels < 6 or len(rows[2:]) != frames:
            raise ValueError('frame count/time/channel mismatch')
        for row in rows[2:]:
            values = [float(x) for x in row.split()]
            if len(values) != channels or not all(math.isfinite(x) for x in values):
                raise ValueError('nonfinite/truncated sample')
    except (ValueError, TypeError, IndexError) as exc:
        raise WorkbenchError('INVALID_BVH', str(exc)) from exc
    return {'frames': frames, 'frame_time': dt, 'fps': 1.0 / dt,
            'duration': frames * dt, 'channels': channels}


def run_logged(argv, log, *, cwd=None, env=None):
    write_json(Path(log).with_suffix('.command.json'), list(map(str, argv)))
    with Path(log).open('wb') as f:
        p = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdout=f,
                           stderr=subprocess.STDOUT,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode:
        raise WorkbenchError('PROCESS_FAILED', f'Process exited {p.returncode}. Inspect {log}')


def task_environment(directory):
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
               PYTHONDONTWRITEBYTECODE='1')
    for name, subdir in {'TEMP':'temp','TMP':'temp','HF_HOME':'hf','HF_XET_CACHE':'xet',
                         'WARP_CACHE_PATH':'warp','PYTORCH_EXTENSIONS_DIR':'torch_extensions',
                         'MPLCONFIGDIR':'matplotlib'}.items():
        path = directory/'cache'/subdir
        path.mkdir(parents=True,exist_ok=True)
        env[name] = str(path)
    return env


def probe_video(path, config):
    ffprobe = config.get('ffprobe') or shutil.which('ffprobe')
    if ffprobe and Path(ffprobe).is_file():
        p = subprocess.run([str(ffprobe), '-v', 'error', '-select_streams', 'v:0',
                            '-show_streams', '-show_format', '-of', 'json', str(path)],
                           capture_output=True, text=True, encoding='utf-8', timeout=60)
        if p.returncode:
            raise WorkbenchError('VIDEO_PROBE_FAILED', p.stderr[-1000:])
        data = json.loads(p.stdout)
        stream = data['streams'][0]
        for value in (stream.get('avg_frame_rate'), stream.get('r_frame_rate')):
            try:
                fps = Fraction(value)
                if fps > 0:
                    break
            except (ValueError, TypeError, ZeroDivisionError, OverflowError):
                continue
        else:
            raise WorkbenchError('INVALID_VIDEO_METADATA', 'ffprobe reported no positive, valid stream frame rate.')
        return {'fps_num': fps.numerator, 'fps_den': fps.denominator,
                'width': stream['width'], 'height': stream['height'],
                'duration': float(stream.get('duration') or data['format']['duration']),
                'probe_engine': 'ffprobe stream metadata'}
    # Media probing is the only optional dependency in the controller environment.
    # Use a separately configured Python so the AI host need not load GPU packages.
    code = ("import av,json,sys; c=av.open(sys.argv[1]); s=c.streams.video[0]; "
            "r=s.average_rate; print(json.dumps(dict(fps_num=r.numerator,fps_den=r.denominator,"
            "width=s.width,height=s.height,duration=float(s.duration*s.time_base) "
            "if s.duration is not None else c.duration/1000000,probe_engine='PyAV stream metadata')))" )
    p = subprocess.run([config.get('media_python', sys.executable), '-c', code, str(path)],
                       capture_output=True, text=True, encoding='utf-8', timeout=60)
    if p.returncode:
        raise WorkbenchError('MISSING_MEDIA_PROBE', 'Configure ffprobe, or install av in media_python. ' + p.stderr[-500:])
    meta = json.loads(p.stdout)
    if meta['fps_num'] <= 0 or meta['fps_den'] <= 0 or meta['duration'] <= 0:
        raise WorkbenchError('INVALID_VIDEO_METADATA', str(meta))
    return meta


def doctor(config_path):
    c = read_config(config_path)
    checks = {}
    for name in ('blender', 'ffmpeg'):
        p = c.get(name)
        checks[name] = bool(p and Path(p).is_file())
    b = c.get('backend', {})
    for name in ('python', 'root', 'motion_config', 'soma_assets'):
        checks['backend.' + name] = bool(b.get(name) and Path(b[name]).exists())
    return {'ok': all(checks.values()), 'code': 'ENVIRONMENT_PATHS_CHECKED',
            'state': 'paths_available' if all(checks.values()) else 'setup_required',
            'checks': checks, 'next_action': {'command': None,
                'reason': 'Path existence only. Successful inference is a separate check.'}, 'artifacts': {}}


def init_job(args):
    directory = Path(args.job).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with job_lock(directory, 'init'):
        if any(path.name != '.operation.lock' for path in directory.iterdir()):
            raise WorkbenchError('JOB_EXISTS', 'Use a new empty job directory; never overwrite another run.')
        inputs = {k: artifact(getattr(args, k)) for k in ('video', 'template', 'profile', 'config')}
        if args.motion:
            inputs['selected_motion'] = artifact(args.motion)
            selected_sidecar = Path(args.motion).with_suffix(Path(args.motion).suffix + '.json')
            if selected_sidecar.is_file():
                inputs['selected_motion_metadata'] = artifact(selected_sidecar)
        c = read_config(inputs['config']['path'])
        meta = probe_video(inputs['video']['path'], c)
        if (not math.isfinite(args.start) or not math.isfinite(args.duration)
                or args.start < 0 or args.duration <= 0 or args.duration > 60
                or args.start + args.duration > meta['duration'] + 1e-4):
            raise WorkbenchError('INVALID_INTERVAL', 'Select an interval within the input, with 0 < duration <= 60 seconds.')
        j = {'schema_version': 1, 'job_id': uuid.uuid4().hex, 'created_at': now(),
             'updated_at': now(), 'inputs': inputs, 'video_metadata': meta,
             'interval': {'start_seconds': args.start, 'duration': args.duration},
             'outputs': {}, 'motion_provenance': None, 'review': None, 'failure': None, 'history': []}
        write_json(directory / 'job.json', j)
    return result(j, directory)


def attempt_dir(directory, stage):
    p = directory / 'attempts' / (stage + '-' + uuid.uuid4().hex[:12])
    p.mkdir(parents=True)
    return p


def invalidate(job, keys):
    invalidate_sequence_preview(job)
    for key in keys:
        job['outputs'].pop(key, None)
    job['review'] = None
    job['preview_scope'] = None


def invalidate_sequence_preview(job):
    for key in list(job['outputs']):
        if key.startswith('sequence_preview_'):
            job['outputs'].pop(key)
    job['sequence_preview_scope'] = None
    job['tracking_quality'] = None


def verify_motion_duration(info, job):
    # One endpoint sample plus codec rounding are allowed; no silent motion stretching.
    tolerance = max(2.0 / info['fps'], 2.0 * job['video_metadata']['fps_den'] / job['video_metadata']['fps_num'])
    if abs(info['duration'] - job['interval']['duration']) > tolerance:
        raise WorkbenchError('MOTION_DURATION_MISMATCH', 'BVH duration does not match selected interval; explicitly prepare the intended clip.')


def extract(directory, job):
    with operation(directory, job, 'extract'):
        invalidate(job, ('motion', 'motion_metadata', 'candidate', 'preview', 'verification', 'bridge_report'))
        c = read_config(job['inputs']['config']['path'])
        b = c.get('backend', {})
        for key in ('python', 'root', 'motion_config', 'soma_assets', 'revision'):
            if not b.get(key) or (key != 'revision' and not Path(b[key]).exists()):
                raise WorkbenchError('BACKEND_NOT_READY', 'Missing backend.' + key)
        rev = subprocess.run(['git', '-C', b['root'], 'rev-parse', 'HEAD'], capture_output=True,
                             text=True, timeout=20)
        if rev.returncode or rev.stdout.strip() != b['revision']:
            raise WorkbenchError('BACKEND_REVISION_MISMATCH', 'Configured backend revision differs from checkout.')
        dest = attempt_dir(directory, 'extract')
        env = task_environment(directory)
        env['PATH'] = str(Path(c['ffmpeg']).parent) + os.pathsep + env.get('PATH', '')
        pt = dest / 'motion.pt'
        raw_pt = dest / 'motion_raw.pt'
        interval = job['interval']
        command = [b['python'], '-m', 'gem.inference', 'motion-video', '--task', 'body',
                   '--input', job['inputs']['video']['path'], '--config', b['motion_config'],
                   '--start', str(interval['start_seconds']), '--duration', str(interval['duration']),
                   '--device', b.get('device', 'cuda'), '--output', str(raw_pt)]
        run_logged(command, dest / 'inference.log', cwd=b['root'], env=env)
        # The tested Windows CUDA geometry compiler cannot resolve builtin.h
        # under this runtime. Official CPU Contact/IK keeps the same algorithm.
        run_logged([b['python'],'-m','gem.inference','motion-cache','--task','body',
                    '--input',raw_pt,'--postprocess','--device','cpu',
                    '--soma-assets',b['soma_assets'],'--output',pt],
                   dest/'contact_postprocess.log',cwd=b['root'],env=env)
        if not pt.is_file():
            raise WorkbenchError('MISSING_INFERENCE_OUTPUT', str(pt))
        motion = dest / 'motion.bvh'
        run_logged([b['python'], HERE / 'export_gem_bvh.py', '--input', pt, '--assets',
                    b['soma_assets'], '--output', motion, '--fps', '30'],
                   dest / 'export.log', cwd=b['root'], env=env)
        info = inspect_bvh(motion)
        verify_motion_duration(info, job)
        motion_artifact = artifact(motion)
        sidecar = motion.with_suffix(motion.suffix + '.json')
        if read_json(sidecar).get('bvh_sha256') != motion_artifact['sha256']:
            raise WorkbenchError('MOTION_METADATA_MISMATCH', 'Exporter coordinate metadata differs from BVH.')
        metadata_artifact = artifact(sidecar)
        parameters_artifact = artifact(pt)
        job['outputs'].update(motion=motion_artifact, motion_metadata=metadata_artifact)
        job['motion_provenance'] = {**motion_artifact, 'route':'gem_video_inference', 'completed_at':now()}
        job['motion_info'] = info
        job['history'].append({'stage': 'extract', 'at': now(), 'route': 'gem_video_inference',
                               'backend_revision': b['revision'], 'source_sha256': job['inputs']['video']['sha256'],
                               'parameters': parameters_artifact, 'raw_parameters':artifact(raw_pt),
                               'postprocess_device':'cpu', 'motion_sha256': job['outputs']['motion']['sha256']})
    return result(job, directory)


def attach_motion(directory, job, motion):
    with operation(directory, job, 'attach-motion'):
        info = inspect_bvh(motion)
        verify_motion_duration(info, job)
        src = artifact(motion)
        sidecar = Path(src['path']).with_suffix(Path(src['path']).suffix + '.json')
        if sidecar.is_file() and read_json(sidecar).get('bvh_sha256') != src['sha256']:
            raise WorkbenchError('MOTION_METADATA_MISMATCH', 'External BVH sidecar SHA does not match the motion.')
        dest = attempt_dir(directory, 'import') / 'motion.bvh'
        shutil.copyfile(src['path'], dest)
        invalidate(job, ('motion', 'motion_metadata', 'candidate', 'preview', 'verification', 'bridge_report'))
        job['outputs']['motion'] = artifact(dest)
        if sidecar.is_file():
            copied_sidecar = dest.with_suffix(dest.suffix + '.json')
            shutil.copyfile(sidecar, copied_sidecar)
            job['outputs']['motion_metadata'] = artifact(copied_sidecar)
        job['motion_info'] = info
        job['motion_provenance'] = {**job['outputs']['motion'], 'route':'imported_motion', 'completed_at':now()}
        job['history'].append({'stage': 'attach-motion', 'at': now(), 'route': 'imported_motion',
                               'original': src, 'motion_sha256': job['outputs']['motion']['sha256'],
                               'note': 'Imported external motion, not evidence that video inference ran.'})
    return result(job, directory)


def retarget(directory, job):
    with operation(directory, job, 'retarget'):
        provenance = job.get('motion_provenance') or {}
        motion = job['outputs'].get('motion') or {}
        if (provenance.get('route') not in ('gem_video_inference','imported_motion')
                or provenance.get('sha256') != motion.get('sha256') or provenance.get('path') != motion.get('path')):
            raise WorkbenchError('MOTION_PROVENANCE_REQUIRED', 'No completed producing operation is bound to this motion. Retry extraction or explicitly import the intended BVH.')
        if any(not intact(a) for k,a in job['outputs'].items() if k in ('motion','motion_metadata')):
            raise WorkbenchError('STALE_ARTIFACTS', 'Upstream motion or coordinate metadata changed; recreate that producing stage.')
        if provenance['route'] == 'gem_video_inference' and 'motion_metadata' not in job['outputs']:
            raise WorkbenchError('MOTION_METADATA_REQUIRED', 'Extracted SOMA motion requires its coordinate and bind-pose sidecar.')
        sidecar = Path(motion['path']).with_suffix(Path(motion['path']).suffix + '.json')
        metadata = job['outputs'].get('motion_metadata')
        if bool(metadata) != sidecar.is_file() or (metadata and Path(metadata['path']).resolve() != sidecar.resolve()):
            raise WorkbenchError('UNREGISTERED_MOTION_METADATA', 'Sidecar presence/path changed after motion registration. Explicitly re-import the intended BVH and sidecar.')
        if 'motion' not in job['outputs'] or not intact(job['outputs']['motion']):
            raise WorkbenchError('MOTION_REQUIRED', 'Generate/import verified motion before retargeting.')
        invalidate(job, ('candidate', 'preview', 'verification', 'bridge_report'))
        c = read_config(job['inputs']['config']['path'])
        if not c.get('blender') or not Path(c['blender']).is_file():
            raise WorkbenchError('BLENDER_NOT_READY', 'Configure an existing Blender executable.')
        dest = attempt_dir(directory, 'retarget')
        meta = job['video_metadata']
        request = {'template': job['inputs']['template']['path'],
                   'template_sha256': job['inputs']['template']['sha256'],
                   'profile': job['inputs']['profile']['path'],
                   'motion': job['outputs']['motion']['path'],
                   'motion_sha256': job['outputs']['motion']['sha256'],
                   'motion_metadata': metadata,
                   'output_dir': str(dest),
                   'source_fps': {'num': meta['fps_num'], 'den': meta['fps_den']},
                   'start_seconds': 0,
                   'source_interval_start_seconds': job['interval']['start_seconds'],
                   'duration': job['interval']['duration'], 'frame_start': 1}
        write_json(dest / 'request.json', request)
        run_logged([c['blender'], '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                    '--python', HERE / 'blender_bridge.py', '--', '--request', dest / 'request.json'],
                   dest / 'blender.log', env=task_environment(directory))
        report_path = dest / 'report.json'
        report = read_json(report_path)
        if report.get('ok') is not True:
            raise WorkbenchError('BRIDGE_FAILED', str(report))
        if (report.get('template_sha256') != request['template_sha256']
                or report.get('motion_sha256') != request['motion_sha256']):
            raise WorkbenchError('BRIDGE_PROVENANCE_MISMATCH', 'Worker used different input artifacts.')
        verified = read_json(report['verification'])
        for check in ('geometry_unchanged', 'rest_bones_unchanged', 'drivers_unchanged', 'curves_cover_all_frames'):
            if verified.get(check) is not True:
                raise WorkbenchError('REOPEN_VERIFICATION_FAILED', check)
        for key, field in (('candidate', 'blend'), ('preview', 'preview'), ('verification', 'verification')):
            p = Path(report[field]).resolve()
            if not p.is_relative_to(dest):
                raise WorkbenchError('UNEXPECTED_OUTPUT_PATH', str(p))
            job['outputs'][key] = artifact(p)
        job['outputs']['bridge_report'] = artifact(report_path)
        job['preview_scope'] = {'kind':'single_frame','blender_frames':[report['frame_start']],
                                'note':'The generated PNG does not cover the full animation.'}
        job['history'].append({'stage': 'retarget', 'at': now(), 'request': artifact(dest / 'request.json'),
                               'candidate_sha256': job['outputs']['candidate']['sha256']})
    return result(job, directory)


def preview_parameters(job):
    """Validate upstream evidence without touching a previous failure or review."""
    failure = job.get('failure')
    if failure and failure.get('stage') != 'preview':
        raise WorkbenchError('UPSTREAM_OPERATION_FAILED', 'Resolve and retry the recorded upstream operation before previewing.')
    if any(not intact(a) for a in job['inputs'].values()):
        raise WorkbenchError('STALE_INPUTS', 'An input changed; initialize a new job from the intended sources.')
    outputs = job['outputs']
    if 'candidate' not in outputs or 'motion' not in outputs:
        raise WorkbenchError('CANDIDATE_REQUIRED', 'A completed motion and registered candidate are required before previewing.')
    provenance = job.get('motion_provenance') or {}
    motion = outputs['motion']
    if (provenance.get('route') not in ('gem_video_inference', 'imported_motion')
            or provenance.get('sha256') != motion['sha256'] or provenance.get('path') != motion['path']):
        raise WorkbenchError('MOTION_PROVENANCE_REQUIRED', 'The candidate needs a completed, current motion producing operation.')
    if any(not intact(a) for key, a in outputs.items() if not key.startswith('sequence_preview_')):
        raise WorkbenchError('STALE_ARTIFACTS', 'An upstream artifact changed; recreate its producing stage before previewing.')
    sidecar = Path(motion['path']).with_suffix(Path(motion['path']).suffix + '.json')
    metadata = outputs.get('motion_metadata')
    if bool(metadata) != sidecar.is_file() or (metadata and Path(metadata['path']).resolve() != sidecar.resolve()):
        raise WorkbenchError('UNREGISTERED_MOTION_METADATA', 'Motion sidecar presence/path changed after registration.')
    if provenance['route'] == 'imported_motion':
        return None
    if metadata is None:
        raise WorkbenchError('MOTION_METADATA_REQUIRED', 'GEM motion requires its registered coordinate metadata.')
    for entry in reversed(job.get('history', [])):
        if (entry.get('stage') == 'extract' and entry.get('route') == 'gem_video_inference'
                and entry.get('motion_sha256') == motion['sha256']):
            parameters = entry.get('parameters')
            if not parameters or not intact(parameters):
                raise WorkbenchError('STALE_PARAMETERS', 'The current GEM cache changed or is missing; recreate its producing stage.')
            return parameters
    raise WorkbenchError('MOTION_PARAMETERS_REQUIRED', 'No registered GEM cache is bound to the current motion.')


def preview(directory, job):
    # Do this before operation(): a preview retry must never replace or clear
    # an earlier extraction/retarget failure using leftover files.
    preview_parameters(job)
    with operation(directory, job, 'preview'):
        parameters = preview_parameters(job)
        invalidate_sequence_preview(job)
        c = read_config(job['inputs']['config']['path'])
        for key in ('blender', 'ffmpeg'):
            if not c.get(key) or not Path(c[key]).is_file():
                raise WorkbenchError('PREVIEW_ENVIRONMENT_NOT_READY', 'Configure an existing ' + key + ' executable.')
        python = c.get('backend', {}).get('python')
        if not python or not Path(python).is_file():
            python = c.get('media_python', sys.executable)
        if not Path(python).is_file():
            raise WorkbenchError('PREVIEW_PYTHON_NOT_READY', 'Configure backend.python or media_python with the preview dependencies.')
        dest = attempt_dir(directory, 'preview')
        request = {'candidate': job['outputs']['candidate'],
                   'video': job['inputs']['video'], 'profile': job['inputs']['profile'],
                   'parameters': parameters, 'blender': c['blender'], 'ffmpeg': c['ffmpeg'],
                   'output_dir': str(dest),
                   'source_fps': {'num': job['video_metadata']['fps_num'], 'den': job['video_metadata']['fps_den']},
                   'interval': dict(job['interval'])}
        write_json(dest / 'request.json', request)
        run_logged([python, HERE / 'motion_preview.py', '--request', dest / 'request.json'],
                   dest / 'preview.log', env=task_environment(directory))
        report_path = dest / 'report.json'
        report = read_json(report_path)
        expected_sha = {key: request[key]['sha256'] if request[key] else None
                        for key in ('candidate', 'video', 'profile', 'parameters')}
        if report.get('ok') is not True or report.get('input_sha256') != expected_sha:
            raise WorkbenchError('PREVIEW_PROVENANCE_MISMATCH', 'Preview worker report belongs to different inputs or did not succeed.')
        # Recheck immutable inputs after the worker too, not only at launch.
        preview_parameters(job)
        artifacts = report.get('artifacts')
        required = {'comparison_video', 'inspection_blend', 'preview_manifest'}
        if not isinstance(artifacts, dict) or not required.issubset(artifacts):
            raise WorkbenchError('PREVIEW_REPORT_INVALID', 'Preview report is missing required artifacts.')
        contacts = sorted(int(key[len('contact_sheet_'):]) for key in artifacts if re.fullmatch(r'contact_sheet_[1-9]\d*', key))
        if contacts != list(range(1, len(contacts) + 1)) or not contacts:
            raise WorkbenchError('PREVIEW_REPORT_INVALID', 'Preview report needs consecutive contact sheets starting at one.')
        names = {'comparison_video': 'sequence_preview_video',
                 'inspection_blend': 'sequence_preview_inspection',
                 'preview_manifest': 'sequence_preview_manifest'}
        registered = {}
        for key, value in artifacts.items():
            if key not in names and not re.fullmatch(r'contact_sheet_[1-9]\d*', key):
                raise WorkbenchError('PREVIEW_REPORT_INVALID', 'Unknown preview artifact: ' + key)
            if not isinstance(value, dict):
                raise WorkbenchError('PREVIEW_REPORT_INVALID', 'Preview artifacts must contain path, bytes and SHA-256.')
            path = Path(value['path']).resolve()
            if not path.is_relative_to(dest):
                raise WorkbenchError('UNEXPECTED_OUTPUT_PATH', str(path))
            if not intact(value):
                raise WorkbenchError('PREVIEW_ARTIFACT_MISMATCH', 'Preview artifact content does not match its registration: ' + key)
            target = names.get(key, 'sequence_preview_contact_' + key.removeprefix('contact_sheet_'))
            registered[target] = artifact(path)
        if not isinstance(report.get('scope'), dict) or not isinstance(report.get('tracking_quality'), dict):
            raise WorkbenchError('PREVIEW_REPORT_INVALID', 'Preview scope and tracking quality must be explicit objects.')
        registered['sequence_preview_report'] = artifact(report_path)
        job['outputs'].update(registered)
        job['sequence_preview_scope'] = report['scope']
        job['tracking_quality'] = report['tracking_quality']
        job['history'].append({'stage': 'preview', 'at': now(), 'request': artifact(dest / 'request.json'),
                               'candidate_sha256': request['candidate']['sha256'],
                               'comparison_sha256': registered['sequence_preview_video']['sha256']})
    return result(job, directory)


def review(directory, job, args):
    with operation(directory, job, 'review'):
        if derive_state({**job, 'failure': None}) not in ('candidate_ready', 'motion_candidate_reviewed', 'needs_revision'):
            raise WorkbenchError('CANDIDATE_REQUIRED', 'A current verified candidate and preview are required.')
        evidence = artifact(args.evidence)
        evidence_key = next((key for key in ('preview', 'sequence_preview_video')
                             if evidence == job['outputs'].get(key)), None)
        if evidence_key is None:
            raise WorkbenchError('WRONG_REVIEW_EVIDENCE', 'Use this candidate\'s registered PNG or sequence comparison, not another file.')
        if not args.notes.strip() or not args.reviewer.strip():
            raise WorkbenchError('REVIEW_DETAILS_REQUIRED', 'Identify reviewer and record observed findings.')
        job['review'] = {'verdict': args.verdict, 'reviewer': args.reviewer,
                         'notes': args.notes, 'at': now(), 'evidence': evidence,
                         'evidence_artifact_key': evidence_key,
                         'evidence_kind': 'sequence' if evidence_key == 'sequence_preview_video' else 'single_frame',
                         'candidate_sha256': job['outputs']['candidate']['sha256'],
                         'preview_sha256': evidence['sha256'],
                         'scope': 'Reviewer assertion on supplied preview; not automatic certification or whole-order acceptance.'}
    return result(job, directory)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WorkbenchError('USAGE', message)


def main(argv=None):
    p = Parser(description=__doc__)
    subs = p.add_subparsers(dest='command', required=True)
    doctor_p = subs.add_parser('doctor')
    doctor_p.add_argument('--config', required=True)
    init_p = subs.add_parser('init')
    for key in ('job', 'video', 'template', 'profile', 'config'):
        init_p.add_argument('--' + key, required=True)
    init_p.add_argument('--start', type=float, default=0)
    init_p.add_argument('--duration', type=float, default=5, help='Duration in seconds, 0 < value <= 60; decimals accepted.')
    init_p.add_argument('--motion', help='Explicit external BVH route; next_action imports this file instead of running video inference.')
    for command in ('status', 'extract', 'attach-motion', 'retarget', 'preview', 'review'):
        sp = subs.add_parser(command)
        sp.add_argument('--job', required=True)
        if command == 'attach-motion':
            sp.add_argument('--motion', required=True)
        if command == 'review':
            sp.add_argument('--verdict', choices=('passed', 'failed'), required=True)
            for key in ('reviewer', 'evidence', 'notes'):
                sp.add_argument('--' + key, required=True)
    try:
        args = p.parse_args(argv)
        if args.command == 'doctor':
            out = doctor(args.config)
        elif args.command == 'init':
            out = init_job(args)
        else:
            directory, job = load_job(args.job)
            if args.command == 'status':
                out = result(job, directory)
                if (directory / '.operation.lock').exists():
                    out['active_operation'] = read_json(directory / '.operation.lock')
            elif args.command == 'extract':
                out = extract(directory, job)
            elif args.command == 'attach-motion':
                out = attach_motion(directory, job, args.motion)
            elif args.command == 'retarget':
                out = retarget(directory, job)
            elif args.command == 'preview':
                out = preview(directory, job)
            else:
                out = review(directory, job, args)
    except (WorkbenchError, OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, subprocess.SubprocessError) as exc:
        out = {'ok': False, 'code': getattr(exc, 'code', 'CONFIG_OR_IO_ERROR'),
               'state': 'blocked', 'message': str(exc), 'next_action': {'command': None,
                    'reason': 'Resolve this error and retry the same operation; do not infer success from old files.'}, 'artifacts': {}}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out['ok'] else 2


if __name__ == '__main__':
    # Protected installed skills permit only manifested source files. Keep
    # both this CLI and Python/Blender descendants from creating helper pyc.
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
