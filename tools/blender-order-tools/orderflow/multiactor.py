"""Background bridge for the bundled Auto Box tools and the installed motion solver."""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from . import core, motion


AUTO_BOX = Path(__file__).with_name('auto_box')
LABELS = {'detect': '多人检测', 'review': '可疑帧审核', 'set': '确认身份锚', 'clear': '撤销身份锚',
          'pose': '多人姿态推理', 'correct': '按新锚点补算', 'skeleton': '原片骨架候选图'}


def backend_config(path, media_python, ffmpeg):
    path = Path(path)
    if not path.is_file():
        raise ValueError('请选择本机 Auto Box 后端配置 JSON。')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    base = path.parent.parent if path.parent.name == 'config' else path.parent
    result = {}
    if not isinstance(data, dict):
        raise ValueError('Auto Box 后端配置须为 JSON 对象。')
    defaults = {'python': media_python, 'pose_python': data.get('python') or media_python}
    for field in ('python', 'pose_python', 'gem_repo', 'checkpoint', 'soma_assets', 'yolox', 'intrinsics'):
        entry = data.get(field) or defaults.get(field)
        if isinstance(entry, str) and entry:
            result[field] = str((base / Path(entry).expanduser()).resolve())
    result['ffmpeg'] = ffmpeg
    return result


def create_job(output, name, source, shot, first, last, backend, update_script):
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError('请选择订单参考视频。')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', shot) or not 0 <= first <= last:
        raise ValueError('请输入镜头 ID 和原片零基闭区间。')
    folder = core.new_job_dir(output, name)
    core.atomic_json(folder / 'box_job.json', dict(reference=str(source), shot=shot, first=first, last=last,
                                                  backend=backend, update_script=update_script))
    core.atomic_json(folder / 'anchors_initial.json', {'actor_ids': [], 'anchors': []})
    core.atomic_json(folder / 'shots.json', [dict(shot_id=shot, source_zero_start=first, source_zero_end=last)])
    return folder


def read_job(folder):
    folder = Path(folder).resolve()
    if not (folder / 'box_job.json').is_file():
        raise ValueError('请选择接单助手创建的多人骨架任务。')
    spec = core.read_json(folder / 'box_job.json')
    ledger = folder / 'box_artifacts.json'
    return folder, spec, core.read_json(ledger) if ledger.is_file() else {}


def artifact(folder, records, key):
    value = records.get(key)
    if not value:
        raise ValueError(f'该任务尚无 {key} 输出，请先完成对应步骤。')
    path = (folder / value).resolve()
    if not core.inside(path, folder) or not path.is_file():
        raise ValueError(f'任务输出已丢失或越界：{key}')
    digest = records.get(key + '_sha256')
    if not digest or core.sha256(path) != digest:
        raise ValueError(f'任务输出已改变，须重新生成：{key}')
    return path


def _record(folder, records, key, path):
    records[key] = str(path.relative_to(folder))
    records[key + '_sha256'] = core.sha256(path)


def check_track(path, shot, track, first, last):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['window'] == shot and first <= row['source_zero_based'] <= last:
                if any(t.get('observed') and t.get('window_local_track_id') == track
                       for t in row.get('observed_tracks', [])):
                    return
    raise ValueError('该区间内没有观察到所选轨迹。')


def parse_frames(text, first, last):
    frames = [int(token.strip()) for token in text.split(',') if token.strip()]
    if not frames or any(not first <= frame <= last for frame in frames):
        raise ValueError('审核图帧号须落在镜头范围内，以英文逗号分隔。')
    return ','.join(str(frame) for frame in sorted(set(frames)))


def _path(config, name, is_dir=False):
    value = config.get(name)
    path = Path(value) if value else None
    if not path or not (path.is_dir() if is_dir else path.is_file()):
        raise ValueError(f'Auto Box 依赖缺失：{name}')
    return str(path.resolve())


def _run(config, script, *args):
    env = motion._env()
    ffmpeg = config.get('ffmpeg')
    if ffmpeg:
        env['PATH'] = str(Path(ffmpeg).parent) + os.pathsep + env.get('PATH', '')
    command = [_path(config, 'python'), '-u', str(AUTO_BOX / 'scripts' / f'{script}.py'), *map(str, args)]
    print('▶', script, flush=True)
    subprocess.run(command, env=env, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _attempt(folder, spec, records, stage, data, output):
    source, shot, first, last = (spec[key] for key in ('reference', 'shot', 'first', 'last'))
    source_sha = core.sha256(source)
    config = dict(spec['backend'], source=source, source_sha256=source_sha)
    if stage == 'detect':
        import av

        model = _path(config, 'yolox')
        target = output / 'detect'
        shots = output / 'shots.json'
        core.atomic_json(shots, [dict(shot_id=shot, source_zero_start=first, source_zero_end=last)])
        _run(config, 'multi_actor_detect_t4', '--source', source, '--source-sha256', source_sha,
             '--repo', _path(config, 'gem_repo', is_dir=True), '--model', model, '--model-sha256', core.sha256(model),
             '--shots', shots, '--output-new-dir', target, '--threads2')
        with av.open(source) as media:
            first_frame = next(media.decode(video=0), None)
        if first_frame is None or first_frame.pts is None or first_frame.time_base is None:
            raise ValueError('原片缺少首帧时间戳。')
        records.clear()
        records.update(source_sha=source_sha, pts_origin=str(first_frame.pts * first_frame.time_base))
        _record(folder, records, 'detections', target / 'frames.jsonl')
        return '检测完成。请生成审核图并确认演员身份。'
    if records.get('source_sha') != source_sha:
        raise ValueError('参考视频已变更，或任务尚未完成多人检测。')
    detection = artifact(folder, records, 'detections')
    if stage == 'review':
        _path(config, 'ffmpeg')
        _run(config, 'review_candidates', '--detections', detection, '--source', source, '--out', output, '--limit', 40)
        _record(folder, records, 'review', output / 'candidates.json')
        return '审核图已生成。请看图后手动确认身份。'
    anchors = artifact(folder, records, 'anchors') if 'anchors' in records else output / 'anchors_initial.json'
    if 'anchors' not in records:
        core.atomic_json(anchors, {'actor_ids': [], 'anchors': []})
    if stage in ('set', 'clear'):
        actor, note = data['actor'].strip(), data['evidence'].strip()
        a, b, track = (data[key] for key in ('first', 'last', 'track'))
        if not actor or actor.startswith('-') or not note or not first <= a <= b <= last:
            raise ValueError('填写演员 ID、检测轨迹 ID、证据和镜头内的原片帧区间。')
        if stage == 'set':
            check_track(detection, shot, track, a, b)
        target = output / 'anchors.json'
        _run(config, 'anchor_edit', '--anchors', anchors, '--out', target,
             '--' + stage, shot, actor, track, a, b, '--evidence=' + note)
        _record(folder, records, 'anchors', target)
        records.pop('skeleton', None)
        return '身份锚已保存。已有姿态须按新锚点补算后再导入。'
    if stage == 'skeleton':
        filled = current_filled(folder, records)
        _path(config, 'ffmpeg')
        _run(config, 'render_skeletons', '--filled', filled, '--source', source,
             '--intrinsics', _path(config, 'intrinsics'), '--out', output,
             '--frames', parse_frames(data['frames'], first, last))
        if not any(output.glob('*.png')):
            raise ValueError('选定的帧没有生成骨架图。')
        _record(folder, records, 'skeleton', next(output.glob('*.png')))
        return '骨架候选图已生成。'

    if not core.read_json(anchors).get('anchors'):
        raise ValueError('姿态推理之前，请从审核图确认至少一个人物身份。')
    if stage == 'correct':
        old = artifact(folder, records, 'pose_anchors')
        if core.sha256(old) == core.sha256(anchors):
            raise ValueError('姿态已对应当前锚点。')
        previous = artifact(folder, records, 'pose')
    for name in ('pose_python', 'intrinsics', 'checkpoint'):
        _path(config, name)
    for name in ('gem_repo', 'soma_assets'):
        _path(config, name, is_dir=True)
    gate = motion._gate({'media_python': _path(config, 'python'), 'motion_update_script': spec['update_script']})
    config['official_scripts'] = str(Path(gate['skill_file']).parent / 'scripts/motion')
    resolved = output / 'resolved.json'
    core.atomic_json(resolved, config)
    if stage == 'pose':
        _run(config, 'run_shot', '--config', resolved, '--shot', shot, '--detections', detection,
             '--anchors', anchors, '--work', output)
        pose = output / 'pose' / 'pose.pt'
    else:
        _run(config, 'correct', '--config', resolved, '--detections', detection, '--anchors-old', old,
             '--anchors-new', anchors, '--pose-existing', previous, '--work', output)
        pose = output / 'pose_patched.pt'
        if not pose.is_file() and not core.read_json(output / 'deltas.json')['deltas']:
            _record(folder, records, 'pose_anchors', anchors)
            return '身份区间变化未影响检测帧，保留原姿态。'
    _record(folder, records, 'pose', pose)
    _record(folder, records, 'filled', output / 'filled.json')
    _record(folder, records, 'pose_anchors', anchors)
    records.pop('skeleton', None)
    return '姿态候选已生成，可导入 Blender 或生成原片骨架图。'


def current_filled(folder, records):
    filled = artifact(folder, records, 'filled')
    if artifact(folder, records, 'pose_anchors') != artifact(folder, records, 'anchors'):
        raise ValueError('身份锚已修改，请先补算姿态。')
    return filled


def run_step(folder, stage, data):
    if stage not in LABELS:
        raise ValueError('未知的多人骨架阶段。')
    folder = Path(folder).resolve()
    lock = folder / 'box.lock'
    try:
        handle = lock.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise RuntimeError('该多人骨架任务仍被占用。') from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        status = folder / 'box_status.json'
        core.atomic_json(status, {'state': 'running', 'message': LABELS[stage] + '进行中…'})
        folder, spec, records = read_job(folder)
        output = core.new_job_dir(folder, stage)
        message = _attempt(folder, spec, records, stage, data, output)
        core.atomic_json(folder / 'box_artifacts.json', records)
        core.atomic_json(status, {'state': 'complete', 'message': message})
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as exc:
        core.atomic_json(folder / 'box_status.json', {'state': 'failed', 'message': f'{LABELS[stage]}失败：{exc}'})
        raise
    finally:
        lock.unlink(missing_ok=True)


def blender_frames(times, scene_start, fps, source_start):
    result = {}
    for index, seconds in times.items():
        frame_offset = (seconds - source_start) * fps
        result[index] = scene_start + int(frame_offset) if frame_offset.denominator == 1 else None
    return result


def import_skeleton(scene, folder, reference, offset):
    import math
    import bpy

    folder, spec, records = read_job(folder)
    if Path(reference).resolve() != Path(spec['reference']).resolve() or core.sha256(reference) != records.get('source_sha'):
        raise ValueError('骨架候选与当前订单参考视频不一致。')
    filled = core.read_json(current_filled(folder, records))
    parents = core.read_json(AUTO_BOX / 'references/joint_parents.json')['parents']
    edges = sorted({tuple(sorted((index, parent))) for index, row in enumerate(parents)
                    for parent in row if 0 <= parent < len(parents) and parent != index})
    origin = Fraction(records['pts_origin'])
    with artifact(folder, records, 'detections').open(encoding='utf-8') as stream:
        rows = [json.loads(line) for line in stream]
    times = {row['source_zero_based']: row['pts'] * Fraction(row['time_base']) - origin for row in rows}
    fps = Fraction(scene.render.fps, 1) / Fraction(scene.render.fps_base).limit_denominator(10000)
    mapped = blender_frames(times, scene.frame_start, fps, Fraction(offset).limit_denominator(100000))
    samples = {}
    for actor, track in filled['actors'].items():
        valid = []
        for source_frame, entry in track.items():
            frame = mapped.get(int(source_frame))
            joints, provenance = entry.get('joints'), entry.get('source')
            if (frame is None or not scene.frame_start <= frame <= scene.frame_end or
                    provenance not in ('observed', 'filled', 'held') or not joints):
                continue
            if (len(joints) != len(parents) or any(len(point) != 3 or
                    not all(math.isfinite(value) for value in point) for point in joints)):
                raise ValueError('姿态关节数量或坐标无效。')
            valid.append((frame, [(x, z, -y) for x, y, z in joints], provenance))
        if valid:
            if len(valid) != len({frame for frame, _, _ in valid}):
                raise ValueError('骨架时间戳重叠，无法唯一映射工程帧。')
            samples[actor] = sorted(valid)
    if not samples:
        raise ValueError('当前工程帧率或参考起点没有可精确对齐的骨架帧。')

    collection = bpy.data.collections.new(f"多人骨架参考 · {spec['shot']}")
    collection.hide_render = True
    collection['orderflow_internal_reference'] = True
    scene.collection.children.link(collection)
    for actor, frames in samples.items():
        mesh = bpy.data.meshes.new(f'骨架 {actor}')
        mesh.from_pydata([(0, 0, 0)] * len(parents), edges, [])
        mesh.update()
        obj = bpy.data.objects.new(mesh.name, mesh)
        obj.location = scene.cursor.location
        obj.show_in_front = True
        obj.hide_render = True
        obj['orderflow_pose_provenance'] = json.dumps({frame: evidence for frame, _, evidence in frames})
        collection.objects.link(obj)
        obj.shape_key_add(name='Basis')
        for frame, points, evidence in frames:
            key = obj.shape_key_add(name=f'{frame} · {evidence}')
            key.data.foreach_set('co', [component for point in points for component in point])
            for at, value in ((frame - 1, 0), (frame, 1), (frame + 1, 0)):
                key.value = value
                key.keyframe_insert(data_path='value', frame=at)
            key.value = 0
    return collection, sum(map(len, samples.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job')
    parser.add_argument('stage', choices=LABELS)
    parser.add_argument('--data', default='{}')
    args = parser.parse_args()
    run_step(args.job, args.stage, json.loads(args.data))


if __name__ == '__main__':
    main()
