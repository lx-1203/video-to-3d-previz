"""External media worker; run with the configured Python, never inside Blender."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

if __package__:
    from . import core
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from orderflow import core


class Cancelled(RuntimeError):
    pass


def check_cancel(job_dir):
    if (Path(job_dir)/'cancel.request').exists():
        core.write_status(job_dir, 'cancelled', message='Cancelled at a safe checkpoint', visual_status='unverified')
        raise Cancelled('Cancellation requested')


def source_offset(spec):
    return float(spec['source_start_seconds']) + float(Fraction(spec['frame_start']-spec['scene_frame_start'], 1) * Fraction(spec['fps_den'], spec['fps_num']))


def render_duration(spec):
    return float(Fraction(spec['frame_end']-spec['frame_start']+1, 1) * Fraction(spec['fps_den'], spec['fps_num']))


def validate_inputs(spec, job_dir):
    for key, expected in spec['input_sha256'].items():
        if not spec.get(key) or core.sha256(spec[key]) != expected:
            raise ValueError(f'Frozen input changed: {key}')
    return True


def validate_frame_records(records, frames_dir, start, end, width, height):
    actual = core.inspect_png_frames(frames_dir, start, end, width, height)
    # Compare content identity; absolute paths are local evidence, not portable identifiers.
    fields = ('name', 'frame', 'sha256')
    if [{k:r[k] for k in fields} for r in actual] != [{k:r[k] for k in fields} for r in records]:
        raise ValueError('Rendered PNGs changed since render_manifest.json')
    return actual


def run_process(argv, log_path, job_dir):
    check_cancel(job_dir)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_json(log_path.with_suffix('.command.json'), list(map(str, argv)))
    with log_path.open('wb') as log:
        process = subprocess.Popen(list(map(str, argv)), stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while process.poll() is None:
                check_cancel(job_dir)
                time.sleep(0.2)
        except BaseException:
            # Only the child we launched is stopped. Never enumerate Blender or system processes.
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            raise
    # A cooperative child can exit 2 after writing cancellation while we are
    # between polls. Preserve that outcome before classifying nonzero exits.
    check_cancel(job_dir)
    if process.returncode:
        raise RuntimeError(f'Process failed ({process.returncode}); see {log_path}')


def _ffmpeg(spec, args, log_path, job_dir):
    run_process([spec['ffmpeg'], '-hide_banner', '-nostdin', '-n', '-nostats', '-loglevel', 'warning', *args], log_path, job_dir)


def check_environment(spec, job_dir):
    """Called by the configured media Python before Blender opens or renders."""
    import importlib
    for name in ('av', 'numpy', 'PIL.Image'):
        importlib.import_module(name)
    log = Path(job_dir) / 'ffmpeg_version.log'
    run_process([spec['ffmpeg'], '-version'], log, job_dir)
    if not log.read_text(encoding='utf-8', errors='replace').lstrip().startswith('ffmpeg version'):
        raise RuntimeError('Configured executable is not FFmpeg; see ffmpeg_version.log')
    validate_reference(spec, job_dir)


def probe(path, *, decode=True, job_dir=None):
    import av
    result = {'path': str(Path(path).resolve()), 'streams': []}
    lookup = {}
    with av.open(str(path)) as container:
        for stream in container.streams:
            if stream.type not in ('video', 'audio'): continue
            ctx = stream.codec_context
            row = dict(type=stream.type, codec=ctx.name, packet_count=0, packet_bytes=0,
                       duration=float(stream.duration*stream.time_base) if stream.duration is not None else None,
                       decoded_frames=0, first_pts=None, last_pts=None, decoded_end=0.0)
            if stream.type == 'video':
                row.update(width=ctx.width, height=ctx.height, fps=str(stream.average_rate), pts_seconds=[])
            else:
                row.update(sample_rate=ctx.sample_rate, channels=ctx.channels, samples=0, packet_sha256=[])
            lookup[stream.index] = row
            result['streams'].append(row)
        for number, packet in enumerate(container.demux()):
            if job_dir is not None and number % 100 == 0: check_cancel(job_dir)
            row = lookup.get(packet.stream.index)
            if row is None: continue
            if packet.size:
                row['packet_count'] += 1
                row['packet_bytes'] += packet.size
                if row['type'] == 'audio': row['packet_sha256'].append(hashlib.sha256(bytes(packet)).hexdigest())
            if decode:
                for frame in packet.decode():
                    row['decoded_frames'] += 1
                    if frame.pts is None: raise ValueError(f'Missing PTS: {path}')
                    pts = float(frame.pts*frame.time_base)
                    if row['last_pts'] is not None and pts <= row['last_pts']:
                        raise ValueError(f'Nonmonotonic decoded PTS: {path}')
                    if row['first_pts'] is None: row['first_pts'] = pts
                    row['last_pts'] = pts
                    if row['type'] == 'video':
                        if (frame.width, frame.height) != (row['width'], row['height']): raise ValueError('Changing decoded geometry')
                        row['pts_seconds'].append(pts)
                        rate = packet.stream.average_rate
                        row['decoded_end'] = pts + float(1/rate) if rate else pts
                    else:
                        row['samples'] += frame.samples
                        row['decoded_end'] = pts + frame.samples/frame.sample_rate
        for row in result['streams']:
            row['actual_bit_rate'] = row['packet_bytes']*8/row['duration'] if row['duration'] else None
    return result


def only_stream(info, kind):
    rows = [r for r in info['streams'] if r['type'] == kind]
    if len(rows) != 1: raise ValueError(f'Expected exactly one {kind} stream: {info["path"]}')
    return rows[0]


def validate_reference(spec, job_dir):
    info = probe(spec['reference_video'], job_dir=job_dir)
    video, audio = only_stream(info, 'video'), only_stream(info, 'audio')
    offset, duration = source_offset(spec), render_duration(spec)
    tolerance = float(Fraction(spec['fps_den'], spec['fps_num'])) * 0.51
    if not video['decoded_frames'] or offset < (video['first_pts'] or 0) - tolerance or offset + duration > video['decoded_end'] + tolerance:
        raise ValueError('Requested scene range exceeds the reference video interval')
    audio_end = audio['decoded_end']
    if not audio['decoded_frames'] or offset < (audio['first_pts'] or 0) - 0.05 or audio_end + 0.05 < offset + duration:
        raise ValueError('Original audio does not cover the requested interval')
    return info


def _video_options(spec):
    fps = str(Fraction(spec['fps_num'], spec['fps_den']))
    options = ['-c:v', 'libx264', '-preset', 'veryfast', '-threads', '4', '-pix_fmt', 'yuv420p', '-r', fps]
    if spec['mode'] == 'final':
        options += ['-profile:v', 'high', '-level:v', '6.0', '-b:v', '30M', '-minrate', '30M', '-maxrate', '30M', '-bufsize', '30M', '-x264-params', 'nal-hrd=cbr:filler=1:force-cfr=1']
    else: options += ['-crf', '22']
    return options


def encode(job_path):
    import av  # Validate the configured environment before any expensive process.
    import numpy
    from PIL import Image
    job_path = Path(job_path).resolve()
    job_dir = job_path.parent
    spec = core.load_job(job_path)
    validate_inputs(spec, job_dir)
    check_cancel(job_dir)
    for name in ('white.mp4', 'comparison.mp4', 'verification.json', 'technical_manifest.json'):
        if (job_dir/name).exists(): raise FileExistsError(f'Output exists: {name}; create a new job')
    manifest = core.read_json(job_dir/'render_manifest.json')
    scene = job_dir/'scene.blend'
    if core.sha256(scene) != manifest['scene_sha256']: raise ValueError('Saved render scene changed')
    records = validate_frame_records(manifest['frames'], job_dir/'frames', spec['frame_start'], spec['frame_end'], spec['width'], spec['height'])
    for item in manifest.get('dependencies', []):
        path = job_dir/item['path']
        if not core.inside(path, job_dir) or not item['path'].startswith('dependencies/') or core.sha256(path) != item['sha256']:
            raise ValueError('Migrated dependency changed: ' + item['path'])
    stage = job_dir/'encoding'
    stage.mkdir(exist_ok=False)
    core.write_status(job_dir, 'encoding', message='Encoding shared source audio', visual_status='unverified', mode=spec['mode'])
    source_info = validate_reference(spec, job_dir)
    source_video, source_audio = only_stream(source_info,'video'), only_stream(source_info,'audio')
    offset, duration = source_offset(spec), render_duration(spec)
    frame_count = spec['frame_end']-spec['frame_start']+1
    fps = Fraction(spec['fps_num'], spec['fps_den'])
    video_end = source_video['decoded_end']
    if offset + duration > video_end + float(1/fps)*0.51:
        raise ValueError('Requested scene range exceeds the reference video interval')
    full_tail = offset+duration >= video_end-float(1/fps)*0.51
    source_audio_end = (source_audio['duration'] or source_audio['decoded_end']) + (source_audio['first_pts'] or 0)
    audio_duration = max(0.0, source_audio_end-offset) if full_tail else duration
    if audio_duration <= 0: raise ValueError('No original audio covers the requested interval')
    audio_args = ['-ss', format(offset,'.12f'), '-i', spec['reference_video'], '-map','0:a:0','-vn']
    if not full_tail: audio_args += ['-t',format(duration,'.12f')]
    audio_args += ['-c:a','aac','-aac_coder','fast','-aac_ms','0','-aac_is','0','-aac_pns','0','-b:a','384k' if spec['mode']=='final' else '192k','-ar','48000','-ac','2','-threads','4',str(stage/'shared_audio.m4a')]
    _ffmpeg(spec,audio_args,stage/'01_audio.log',job_dir)
    white_silent, comparison_silent = stage/'white_silent.mp4', stage/'comparison_silent.mp4'
    _ffmpeg(spec,['-filter_threads','4','-framerate',str(fps),'-start_number',str(spec['frame_start']),'-i',str(job_dir/'frames'/'frame_%06d.png'),'-map','0:v:0','-an','-vf','setsar=1','-frames:v',str(frame_count),*_video_options(spec),str(white_silent)],stage/'02_white.log',job_dir)
    width,height=spec['width'],spec['height']
    graph=(f'[0:v]setpts=PTS-STARTPTS,fps={fps}:start_time=0,scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,'
           f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[top];[1:v]setpts=PTS-STARTPTS,setsar=1[bottom];[top][bottom]vstack=inputs=2:shortest=1[v]')
    _ffmpeg(spec,['-filter_complex_threads','4','-ss',format(offset,'.12f'),'-i',spec['reference_video'],'-i',str(white_silent),'-filter_complex',graph,'-map','[v]','-an','-frames:v',str(frame_count),*_video_options(spec),str(comparison_silent)],stage/'03_comparison.log',job_dir)
    for name, silent in (('white.mp4',white_silent),('comparison.mp4',comparison_silent)):
        # No -shortest: genuine original audio beyond the final video frame is retained.
        _ffmpeg(spec,['-i',str(silent),'-i',str(stage/'shared_audio.m4a'),'-map','0:v:0','-map','1:a:0','-c','copy','-video_track_timescale',str(spec['fps_num']*1000),'-movie_timescale','48000','-movflags','+faststart',str(job_dir/name)],stage/(name+'.mux.log'),job_dir)
    core.write_status(job_dir,'verifying',message='Full decode and provenance checks',visual_status='unverified',mode=spec['mode'])
    checks = []
    outputs = {}
    audio_packets = None
    shared = only_stream(probe(stage/'shared_audio.m4a',job_dir=job_dir),'audio')
    for name, expected_height in (('white.mp4',height),('comparison.mp4',height*2)):
        info=probe(job_dir/name,job_dir=job_dir)
        video,audio=only_stream(info,'video'),only_stream(info,'audio')
        if video['codec'] != 'h264' or (video['width'],video['height']) != (width,expected_height) or video['decoded_frames'] != frame_count or Fraction(video['fps']) != fps:
            raise ValueError(f'Video codec, geometry, fps or frame count failed: {name}')
        if any(abs(pts-float(Fraction(i,1)/fps)) > 1e-5 for i,pts in enumerate(video['pts_seconds'])):
            raise ValueError('Video PTS timing failed: '+name)
        if spec['mode']=='final' and (video['actual_bit_rate'] or 0) < 18000000:
            raise ValueError(f'Actual video bitrate below 18 Mbps: {name}')
        if audio['codec'] != 'aac' or audio['sample_rate'] != 48000 or audio['channels'] != 2 or audio['decoded_frames'] <= 0:
            raise ValueError('Audio codec, layout or full decode failed: '+name)
        if abs((audio['duration'] or 0)-audio_duration)>0.05:
            raise ValueError(f'Original audio interval/tail not preserved: {name}: {audio["duration"]} vs {audio_duration}')
        if audio['packet_sha256'] != shared['packet_sha256']:
            raise ValueError('AAC packets differ from the shared encoding: '+name)
        if audio_packets is not None and audio_packets != audio['packet_sha256']:
            raise ValueError('Deliveries have different AAC packets')
        audio_packets=audio['packet_sha256']
        outputs[name]=info
        checks.append(dict(name=name,full_decode=True,frames=frame_count,pts=True,dimensions=True,actual_video_bit_rate=video['actual_bit_rate'],actual_audio_bit_rate=audio['actual_bit_rate'],shared_aac_packets=True))
    validate_inputs(spec,job_dir)
    validate_frame_records(records,job_dir/'frames',spec['frame_start'],spec['frame_end'],width,height)
    if core.sha256(scene)!=manifest['scene_sha256']: raise ValueError('Render scene changed during encoding')
    check_cancel(job_dir)
    file_hashes = {name:core.sha256(job_dir/name) for name in ('scene.blend','white.mp4','comparison.mp4')}
    report=dict(schema_version=1,technical_passed=True,visual_status='unverified',mode=spec['mode'],native_render_dimensions=[width,height],source_sha256=spec['input_sha256'],file_sha256=file_hashes,frame_sha256=[{'name':r['name'],'sha256':r['sha256']} for r in records],checks=checks,source_offset_seconds=offset,render_duration_seconds=duration,preserved_audio_tail=full_tail,audio_duration_seconds=audio_duration,requested_video_bit_rate=30000000 if spec['mode']=='final' else None,requested_audio_bit_rate=384000 if spec['mode']=='final' else 192000,media=outputs,tool_versions={'PyAV':av.__version__,'numpy':numpy.__version__,'Pillow':Image.__version__})
    core.atomic_json(job_dir/'verification.json',report)
    usage=('OrderFlow rendered scene\nSaved with Blender '+manifest.get('blender_version_string', 'unknown')+'. Open scene.blend using a compatible Blender version with automatic script execution disabled. Keep dependencies beside it.\nwhite.mp4: rendered scene with original audio.\ncomparison.mp4: reference above, render below; reference is scaled preserving aspect.\nFrame rate: '+str(fps)+'.\n'+('Preview: 1280x720; comparison: 1280x1440.\n' if spec['mode']=='preview' else 'Native render: 3840x2160; comparison: 3840x4320.\n'))
    (job_dir/'usage.txt').write_text(usage,encoding='utf-8')
    members=dict(file_hashes)
    members.update({'usage.txt':core.sha256(job_dir/'usage.txt'),'verification.json':core.sha256(job_dir/'verification.json')})
    for item in manifest.get('dependencies',[]):
        path=job_dir/item['path']
        if core.sha256(path)!=item['sha256']: raise ValueError('Dependency changed during encoding')
        members[item['path']]=item['sha256']
    if spec.get('model_standard'):
        directory=job_dir/'model_standard'
        directory.mkdir()
        path=directory/Path(spec['model_standard']).name
        shutil.copy2(spec['model_standard'],path)
        if core.sha256(path)!=spec['input_sha256']['model_standard']: raise ValueError('Model standard changed')
        members[path.relative_to(job_dir).as_posix()]=core.sha256(path)
    core.atomic_json(job_dir/'technical_manifest.json',dict(schema_version=1,technical_passed=True,visual_status='unverified',job_id=spec['job_id'],mode=spec['mode'],native_render_dimensions=[width,height],members=members))
    core.write_status(job_dir,'complete',message='内部候选（非交稿）；技术核验通过，待同帧骨骼审核。',technical_passed=True,visual_status='unverified',delivery_status='internal_candidate_pending_overlay',mode=spec['mode'])
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True,type=Path)
    parser.add_argument('--check-env', action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.check_env:
            spec = core.load_job(args.job)
            validate_inputs(spec, args.job.parent)
            check_environment(spec, args.job.parent)
        else:
            encode(args.job)
        return 0
    except Cancelled:
        return 2
    except Exception as exc:
        core.write_status(args.job.resolve().parent,'failed',error=str(exc),visual_status='unverified')
        print(f'ENCODING FAILED: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
