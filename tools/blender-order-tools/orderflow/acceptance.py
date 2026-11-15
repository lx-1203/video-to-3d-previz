"""Call the released same-frame overlay checker; never infer visual acceptance."""
import json
import math
import os
from pathlib import Path
import subprocess
import uuid
from . import core, media, motion


def assert_unchanged(result):
    if result.get('status') != 'passed' or not result.get('frozen_files'):
        raise ValueError('骨骼同帧审核未通过。')
    _assert_frozen_files(result)


def _assert_frozen_files(result):
    for name, record in result['frozen_files'].items():
        if core.sha256(record['path']) != record['sha256']:
            raise ValueError('骨骼同帧审核输入已变化，请重新审核：' + name)


def validate_for_package(job_dir, config):
    job_dir = Path(job_dir).resolve()
    result = {'status':'failed', 'errors':[], 'frozen_files':{}}
    result_path = job_dir/'acceptance_gate_result.json'
    try:
        if not isinstance(config, dict):
            raise ValueError('交稿需要骨骼与原视频同帧重合审核，请选择协议、报告和检查器。')
        for key in ('protocol', 'report', 'checker', 'motion_update_script'):
            if not isinstance(config.get(key), str) or not config[key].strip():
                raise ValueError('缺少骨骼同帧审核配置：'+key)
        job_path = job_dir/'job.json'
        job_sha = core.sha256(job_path)
        spec = core.load_job(job_path)
        result['frozen_files']['job'] = {'path':str(job_path), 'sha256':job_sha}
        required_inputs = {'source_blend', 'snapshot_blend', 'reference_video'}
        if spec.get('model_standard'):
            required_inputs.add('model_standard')
        if set(spec['input_sha256']) != required_inputs:
            raise ValueError('冻结 job 缺少完整输入 SHA，必须重新创建导出任务。')
        # Expected identities come from the render job, never a post-gate observation.
        for name, expected_sha in spec['input_sha256'].items():
            result['frozen_files'][name] = {'path':str(Path(spec[name]).resolve()), 'sha256':expected_sha}
        media.validate_inputs(spec, job_dir)
        files = {'candidate':job_dir/'scene.blend', 'source_video':Path(spec['reference_video']), 'protocol':Path(config['protocol']), 'report':Path(config['report'])}
        for name in ('candidate', 'protocol', 'report'):
            path = files[name].resolve()
            if not path.is_file():
                raise ValueError('缺少骨骼审核输入：'+name)
            result['frozen_files'][name] = {'path':str(path), 'sha256':core.sha256(path)}
        _assert_frozen_files(result)
        actors = config.get('required_actors')
        if not isinstance(actors, list) or not actors or any(not isinstance(actor,str) or not actor.strip() or actor != actor.strip() for actor in actors) or len(set(actors)) != len(actors):
            raise ValueError('请明确填写非空且不重复的角色 actor IDs。')
        source_frame = media.source_offset(spec)*spec['fps_num']/spec['fps_den']
        if not math.isclose(source_frame, round(source_frame), abs_tol=1e-6, rel_tol=0):
            raise ValueError('同帧审核 v1 仅支持同 FPS 的整数源帧起点，当前时间映射不支持。')
        expected = dict(blender_start=spec['frame_start'], source_start=round(source_frame), frame_count=spec['frame_end']-spec['frame_start']+1, fps_num=spec['fps_num'], fps_den=spec['fps_den'])
        # Every package invocation performs the normal online gate and fresh skill read.
        gate = motion._gate({'media_python':spec['media_python'], 'motion_update_script':config['motion_update_script']})
        media.validate_inputs(spec, job_dir)
        _assert_frozen_files(result)
        skill = Path(gate['skill_file']).resolve()
        checker = skill.parent/'scripts'/'validate_skeleton_overlay.py'
        if not checker.is_file() or Path(config.get('checker','')).resolve() != checker:
            raise ValueError('骨骼检查器尚未正式发布或路径不符；必须使用本次在线门禁返回技能中的 scripts/validate_skeleton_overlay.py。')
        for name, path in {'checker':checker, 'skill':skill}.items():
            path = path.resolve()
            if not path.is_file():
                raise ValueError('缺少骨骼审核输入：' + name)
            result['frozen_files'][name] = {'path':str(path), 'sha256':core.sha256(path)}
        result.update(expected=expected, required_actors=actors, gate=gate)
        argv = [spec['media_python'], '-B', str(checker), '--protocol', str(files['protocol'].resolve()), '--report', str(files['report'].resolve()), '--candidate', str(files['candidate']), '--source-video', str(files['source_video']), '--media-python', spec['media_python']]
        for key, value in expected.items():
            argv += ['--'+key.replace('_','-'),str(value)]
        for actor in actors:
            argv += ['--required-actor', actor]
        log = job_dir/('acceptance_checker_'+uuid.uuid4().hex+'.log')
        result['log'] = str(log)
        core.atomic_json(log.with_suffix('.command.json'),argv)
        with log.open('w',encoding='utf-8') as stream:
            process = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, shell=False, timeout=1800, env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1'), creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            payload = json.loads(log.read_text(encoding='utf-8-sig'))
        except ValueError as exc:
            raise ValueError('骨骼检查器未返回有效 JSON，请查看：'+str(log)) from exc
        if process.returncode != 0 or not isinstance(payload,dict) or payload.get('status') != 'passed' or payload.get('errors') != []:
            raise ValueError('骨骼同帧审核未通过：'+str(payload)+'；日志：'+str(log))
        result.update(status='passed', checker_result=payload)
        assert_unchanged(result)
        core.atomic_json(result_path,result)
        return result
    except Exception as exc:
        if isinstance(exc,subprocess.TimeoutExpired):
            phase = '检查器' if result.get('log') else '在线更新门禁'
            message = f'骨骼审核{phase}执行超时（{exc.timeout} 秒），未允许交稿。日志：'+str(result.get('log', '请查看 package.log'))
        else:
            message = str(exc)
        result.update(status='failed', errors=[message])
        core.atomic_json(result_path,result)
        raise ValueError(message) from exc
