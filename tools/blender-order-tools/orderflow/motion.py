"""Isolated motion CLI bridge; never assigns motion to production objects."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

_HELPERS = []


def _env():
    return dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def _same(a, b):
    return isinstance(a, str) and Path(a).resolve() == Path(b).resolve()


def _job_file(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError('请选择已有动作任务目录')
    path = Path(value).resolve()
    if path.is_file() and path.name != 'job.json':
        raise ValueError('动作任务必须为任务目录或其中的 job.json')
    return path if path.is_file() else path / 'job.json'


def _validate_config(config):
    for key in ('media_python', 'motion_cli', 'motion_update_script'):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip() or not Path(value).is_file():
            raise ValueError('请配置有效路径：' + key)
    job_file = _job_file(config.get('motion_job'))
    if not job_file.is_file():
        raise ValueError('动作任务缺少 job.json')
    return job_file


def _run_json(argv, timeout=30, log=None):
    # Long stages log on disk and are polled by the parent UI, never run on bpy threads.
    if log:
        with Path(log).open('w', encoding='utf-8') as stream:
            process = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                     env=_env(), timeout=timeout, check=False,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = Path(log).read_text(encoding='utf-8-sig')
    else:
        process = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                                 env=_env(), timeout=timeout, check=False,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = process.stdout
    try:
        data = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise ValueError('工具未返回单个有效 JSON；请检查日志与配置') from exc
    if not isinstance(data, dict) or process.returncode:
        raise ValueError('工具执行失败：' + str(data.get('message', data.get('code', process.returncode))) if isinstance(data, dict) else '工具结果不是对象')
    return data


def _gate(config):
    argv = [config['media_python'], '-B', config['motion_update_script']]
    for attempt in range(2):
        data = _run_json(argv)
        if data.get('status') not in ('current', 'updated'):
            raise ValueError('在线更新门禁未通过；保留本地修改和更新锁')
        if data.get('rerun_required') is True or data.get('updater_changed') is True:
            if attempt == 0:
                continue
            raise ValueError('新版更新器复检仍未通过')
        if data.get('rerun_required') is not False:
            raise ValueError('更新器缺少明确复检结果')
        skill = Path(data['skill_file'])
        skill.read_text(encoding='utf-8-sig')  # Mandatory fresh read after online check.
        return dict(data, skill_sha256=_sha(skill), checked_at=time.time())
    raise ValueError('在线更新门禁未完成')


def run_action(action, config, log=None):
    result = dict(ok=False, state='failed', code='MOTION_BLOCKED', message='', artifacts={}, next_action=None)
    try:
        if action not in ('status', 'next'):
            raise ValueError('不支持的动作辅助操作')
        job_file = _validate_config(config)
        gate = _gate(config)
        job_sha = _sha(job_file)
        status = _run_json([config['media_python'], '-B', config['motion_cli'], 'status', '--job', config['motion_job']])
        if status.get('ok') is not True:
            raise ValueError('动作任务状态失败：' + str(status.get('code', 'unknown')))
        payload = status
        if action == 'next':
            next_action = status.get('next_action') or {}
            command = next_action.get('command')
            if command is None:
                result.update(ok=True, state=status.get('state', 'unknown'), code=status.get('code', 'OK'),
                              message='无自动下一步：' + str(next_action.get('reason') or '请查看动作证据并人工修订与验收'),
                              artifacts=status.get('artifacts', {}), next_action=status.get('next_action'),
                              review=status.get('review'))
                return dict(result, gate=gate)
            if (not isinstance(command, list) or len(command) != 5
                    or not all(isinstance(item, str) for item in command)
                    or not _same(command[0], config['media_python'])
                    or not _same(command[1], config['motion_cli'])
                    or command[2] not in ('extract', 'retarget', 'preview')
                    or command[3] != '--job' or not _same(command[4], config['motion_job'])):
                raise ValueError('下一步命令不在允许清单中；未执行')
            payload = _run_json([command[0], '-B', *command[1:]], timeout=24 * 3600, log=log)
            if payload.get('ok') is not True:
                raise ValueError('动作步骤未成功：' + str(payload.get('code', 'unknown')))
        result.update(ok=True, state=payload.get('state', 'unknown'), code=payload.get('code', 'OK'),
                      message=payload.get('message') or '动作状态已读取；候选仍需人工检查',
                      artifacts=payload.get('artifacts', {}), next_action=payload.get('next_action'),
                      review=payload.get('review'), gate=gate)
        if action == 'status':
            result['import_proof'] = dict(job_path=str(job_file), job_sha256=job_sha,
                                          created_at=time.time(), consumed=False,
                                          helper_path=str(Path(__file__).resolve()), helper_sha256=_sha(__file__),
                                          cli_path=str(Path(config['motion_cli']).resolve()),
                                          cli_sha256=_sha(config['motion_cli']),
                                          updater_path=str(Path(config['motion_update_script']).resolve()),
                                          updater_sha256=_sha(config['motion_update_script']))
    except Exception as exc:
        result['message'] = str(exc)[:700]
    return result


def _queue(action, settings, prefs):
    _HELPERS[:] = [process for process in _HELPERS if process.poll() is None]
    config = {key: getattr(prefs, key) for key in ('media_python', 'motion_cli', 'motion_update_script')}
    config.update(motion_job=settings.motion_job, source_start_seconds=settings.source_start_seconds)
    # Blender's // relative paths are resolved only on the main thread before spawning.
    try:
        import bpy
    except ImportError:
        pass
    else:
        for key in ('media_python', 'motion_cli', 'motion_update_script', 'motion_job'):
            if not str(config[key]).strip():
                raise ValueError('请配置有效路径：' + key)
            config[key] = bpy.path.abspath(config[key])
    root = _validate_config(config).parent / 'ui_tasks' / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    input_path, result_path = root / 'input.json', root / 'result.json'
    _write(input_path, config)
    with (root / 'helper.log').open('w', encoding='utf-8') as stream:
        process = subprocess.Popen([config['media_python'], '-B', str(Path(__file__).resolve()),
                                    '--ui-action', action, '--settings-json', str(input_path),
                                    '--result-json', str(result_path)], stdout=stream, stderr=subprocess.STDOUT,
                                   env=_env(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    _HELPERS.append(process)
    return dict(ok=True, state='queued', result_path=str(result_path), pid=process.pid,
                message='已在后台检查在线更新及动作任务')


def motion_status(settings, prefs):
    return _queue('status', settings, prefs)


def motion_next(settings, prefs):
    return _queue('next', settings, prefs)


def _artifact(record):
    path = Path(record['path'])
    if not path.is_file() or path.stat().st_size != record['bytes'] or _sha(path) != record['sha256']:
        raise ValueError('任务来源或候选 SHA 不一致：' + str(path))
    return path


def _curves(action):
    if hasattr(action, 'fcurves'):
        return list(action.fcurves)
    curves = []
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type != 'KEYFRAME':
                raise ValueError('暂不支持此动作层类型')
            for bag in strip.channelbags:
                curves.extend(bag.fcurves)
    return curves


def import_reference(context, jobdir):
    """Append a verified candidate as a hidden render reference, with one-use gate proof."""
    import bpy
    from mathutils import Matrix
    settings, scene = context.scene.orderflow, context.scene
    job_path = _job_file(jobdir)
    result_path = Path(bpy.path.abspath(settings.motion_result_path))
    result = _read(result_path)
    proof = result.get('import_proof') or {}
    gate = result.get('gate') or {}
    if (result.get('ok') is not True or proof.get('consumed') is not False
            or not _same(proof.get('job_path'), str(job_path))
            or proof.get('job_sha256') != _sha(job_path)
            or not 0 <= time.time() - proof.get('created_at', 0) <= 300
            or gate.get('status') not in ('current', 'updated')
            or gate.get('rerun_required') is not False
            or not _same(proof.get('helper_path'), str(Path(__file__).resolve()))
            or proof.get('helper_sha256') != _sha(__file__)
            or not 0 <= time.time() - gate.get('checked_at', 0) <= 300):
        raise ValueError('请先刷新动作状态；导入需要本次在线检查且来源未变化（5分钟内一次有效）')
    for path_key, sha_key in (('cli_path', 'cli_sha256'), ('updater_path', 'updater_sha256')):
        if _sha(proof[path_key]) != proof[sha_key]:
            raise ValueError('动作工具已变化；请重新刷新状态')
    if _sha(gate['skill_file']) != gate['skill_sha256']:
        raise ValueError('已检查技能已变化；请重新刷新状态')
    job = _read(job_path)
    if job.get('schema_version') != 1:
        raise ValueError('不支持的动作任务格式')
    for key in ('video', 'template', 'profile', 'config'):
        _artifact(job['inputs'][key])
    for key in ('candidate', 'motion', 'motion_metadata'):
        _artifact(job['outputs'][key])
    sidecar = _read(job['outputs']['motion_metadata']['path'])
    if sidecar.get('bvh_sha256') != job['outputs']['motion']['sha256']:
        raise ValueError('动作说明文件未绑定当前 BVH SHA')
    provenance = job['motion_provenance']
    if (not _same(provenance['path'], job['outputs']['motion']['path'])
            or provenance['sha256'] != job['outputs']['motion']['sha256']):
        raise ValueError('动作来源登记与当前动作不一致')
    profile = _read(job['inputs']['profile']['path'])
    anchor_name = settings.motion_anchor_name
    if anchor_name:
        anchor = scene.objects.get(anchor_name)
        if anchor is None:
            raise ValueError('参考锚点不存在')
        delta = anchor.matrix_world.copy()
    else:
        delta = Matrix.Translation(scene.cursor.location)
    source_start = float(settings.source_start_seconds)
    if not math.isfinite(source_start) or source_start < 0:
        raise ValueError('原片起点必须为非负秒数')
    proof['consumed'] = True
    _write(result_path, result)
    before_objects = set(bpy.data.objects)
    before_ids = set(bpy.data.user_map())
    collection = None
    try:
        with bpy.data.libraries.load(job['outputs']['candidate']['path'], link=False) as (source, target):
            names = [profile['rig_object'], profile['mesh_object']]
            if any(name not in source.objects for name in names):
                raise ValueError('候选缺少登记的绑定或网格')
            target.objects = names
        rig, mesh = target.objects
        if rig.type != 'ARMATURE' or mesh.type != 'MESH':
            raise ValueError('候选对象类型不符')
        expected = profile['expected']
        if (len(rig.data.bones) != expected['bones'] or len(mesh.data.vertices) != expected['vertices']
                or len(mesh.data.polygons) != expected['polygons']):
            raise ValueError('候选不符合登记模型几何和绑定规范')
        if not any(mod.type == 'ARMATURE' and mod.object == rig for mod in mesh.modifiers):
            raise ValueError('候选网格未绑定到登记骨架')
        imported = set(bpy.data.objects) - before_objects
        if any(obj.type in ('CAMERA', 'LIGHT') for obj in imported):
            raise ValueError('候选夹带相机或灯光依赖，已停止导入')
        # Only the requested rig and mesh are linked. WGTS dependency objects remain unlinked.
        actions = {}
        for obj in imported:
            animation = obj.animation_data
            if animation and len(animation.nla_tracks):
                raise ValueError('暂不支持候选 NLA；请先在候选副本烘焙为单一动作')
            if animation and animation.action:
                action = animation.action
                if hasattr(action, 'slots') and len(action.slots) > 1:
                    raise ValueError('暂不支持多槽动作；请先烘焙候选')
                actions.setdefault(action, None)
        fps = scene.render.fps / scene.render.fps_base
        metadata = job['video_metadata']
        candidate_fps = metadata['fps_num'] / metadata['fps_den']
        if not math.isfinite(candidate_fps) or candidate_fps <= 0:
            raise ValueError('候选帧率无效')
        scale = fps / candidate_fps
        start = scene.frame_start + (job['interval']['start_seconds'] - source_start) * fps
        for original in actions:
            copied = original.copy()
            actions[original] = copied
            for curve in _curves(copied):
                for key in curve.keyframe_points:
                    for point in (key.co, key.handle_left, key.handle_right):
                        point.x = start + (point.x - 1) * scale
                curve.update()
        for obj in imported:
            if obj.animation_data and obj.animation_data.action in actions:
                obj.animation_data.action = actions[obj.animation_data.action]
            obj.hide_render = True
            obj['orderflow_motion_reference'] = True
            obj['orderflow_review'] = job.get('review', {}).get('verdict', 'unverified')
        collection = bpy.data.collections.new('OrderFlow_MotionReference_' + str(job['job_id'])[:12])
        collection.hide_render = True
        collection['job_sha256'] = _sha(job_path)
        collection['candidate_sha256'] = job['outputs']['candidate']['sha256']
        collection['review'] = job.get('review', {}).get('verdict', 'unverified')
        scene.collection.children.link(collection)
        for obj in (rig, mesh):
            collection.objects.link(obj)
        for obj in (rig, mesh):
            if obj.parent not in imported:
                obj.matrix_world = delta @ obj.matrix_world
        imported_result = dict(ok=True, state='reference_only', code='REFERENCE_IMPORTED',
                    message='已追加独立动作参考（不参与渲染）；保留原动作与待修验收状态',
                    collection=collection.name, start_frame=start, artifacts=job['outputs'],
                    next_action=None)
        result.update(imported_result)
        _write(result_path, result)
        return imported_result
    except Exception as exc:
        # Remove every newly appended dependency as well as copied actions and collection.
        # Existing IDs are never included, even when the candidate fails after partial load.
        bpy.data.batch_remove(ids=set(bpy.data.user_map()) - before_ids)
        result.update(ok=False, state='failed', code='IMPORT_FAILED', message=str(exc)[:700])
        _write(result_path, result)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ui-action', choices=('status', 'next'), required=True)
    parser.add_argument('--settings-json', required=True)
    parser.add_argument('--result-json', required=True)
    args = parser.parse_args()
    try:
        result = run_action(args.ui_action, _read(args.settings_json), str(Path(args.result_json).with_suffix('.stage.log')))
    except Exception as exc:
        result = dict(ok=False, state='failed', code='HELPER_FAILED', message=str(exc)[:700], artifacts={}, next_action=None)
    _write(args.result_json, result)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
