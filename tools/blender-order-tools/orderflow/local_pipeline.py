"""Launch the user's local scene/people pipeline without copying private code or assets."""
from pathlib import Path
import uuid

from . import core


def launch_spec(*, mode, interpreter, runner, output, source, runtime, first, last,
                profile='', anchors='', binding='translation-adapter'):
    if mode not in {'scene', 'people-scene'} or not 0 <= first <= last:
        raise ValueError('请选择有效的本地流水线和原片零基帧段。')
    if Path(runner).name != 'pipeline_runner.py':
        raise ValueError('请选择本机可信的 pipeline_runner.py。')
    required = {'Python': interpreter, '本地流水线': runner, '原片': source, '场景运行配置': runtime}
    if mode == 'people-scene':
        required.update({'本地角色配置': profile, '身份锚': anchors})
        if binding not in {'translation-adapter', 'rigid-components'}:
            raise ValueError('未知的角色绑定策略。')
    for label, path in required.items():
        if not Path(path).is_file():
            raise ValueError(f'{label}不存在，请核对本机路径。')
    if not output or not Path(output).is_dir():
        raise ValueError('请选择该流水线允许的本地任务根目录。')
    job = Path(output).resolve() / f'orderflow_{uuid.uuid4().hex}'
    args = [interpreter, '-B', runner, mode, '--video', source, '--job', str(job),
            '--start-frame', str(first), '--frame-count', str(last - first + 1)]
    if mode == 'scene':
        args.extend(['--runtime', runtime])
    else:
        args.extend(['--scene-runtime', runtime, '--profile', profile, '--identity-anchors', anchors,
                     '--binding-profile', binding])
    return job, args


def candidate(job, source):
    job = Path(job).resolve()
    if not (job / 'run_report.json').is_file():
        raise ValueError('本地流水线还没有成功报告。')
    report = core.read_json(job / 'run_report.json')
    base = report.get('source_video')
    if not base and report.get('input_snapshot'):
        snapshot = _report_file(job, report['input_snapshot'], '.json')
        base = core.read_json(snapshot).get('video')
    if not base or not Path(source).is_file() or core.sha256(source) != base['sha256']:
        raise ValueError('候选报告对应另一份原片。')
    return _report_file(job, report['outputs']['blend'], '.blend')


def _report_file(job, record, suffix):
    path = Path(record['path']).resolve()
    if not core.inside(path, job) or path.suffix.lower() != suffix or not path.is_file() or core.sha256(path) != record['sha256']:
        raise ValueError('候选工程缺失、越界，或与报告 SHA 不一致。')
    return path
