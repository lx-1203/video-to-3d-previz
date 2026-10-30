#!/usr/bin/env python3
"""Recreate the pinned Windows CUDA backend in a chosen workspace.

Requires Git and uv on PATH and existing Blender/FFmpeg executables.
--plan-only does not install, download or create files. Model/source licenses
remain upstream; no client asset, credential or paid service is involved.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

COMMIT = '20555befe2fbbe5b2015e1fff7ff214dc18ea9d6'
HERE = Path(__file__).resolve().parent


def build_plan(workspace, uv, blender, ffmpeg):
    root = Path(workspace).resolve()
    runtime = root/'runtime'
    vendor = root/'vendor/GEM-X'
    if vendor.exists() and not (vendor/'.git').exists():
        raise ValueError('Refusing to overwrite a non-Git vendor/GEM-X directory')
    python = runtime/'backend/Scripts/python.exe'
    if os.name != 'nt':
        raise ValueError('This setup recipe is for Windows CUDA; use the upstream Linux installation on Linux.')
    env = {k: str(runtime/v) for k, v in {
        'UV_CACHE_DIR':'uv-cache', 'UV_PYTHON_INSTALL_DIR':'python',
        'TEMP':'temp', 'TMP':'temp', 'HF_HOME':'hf-home', 'HF_HUB_CACHE':'hf-cache'}.items()}
    commands = []
    def add(stage, argv, cwd=None):
        commands.append({'stage':stage, 'argv':list(map(str,argv)), 'cwd':str(cwd or root)})
    add('python', [uv,'python','install','3.12'])
    add('environment', [uv,'venv',runtime/'backend','--python','3.12','--allow-existing'])
    if not vendor.exists():
        add('source', ['git','clone','--filter=blob:none','--no-checkout',
                       'https://github.com/NVlabs/GEM-X.git',vendor])
    add('source-pin', ['git','-C',vendor,'checkout','--detach',COMMIT])
    add('torch', [uv,'pip','install','--python',python,'torch==2.10.0','torchvision==0.25.0',
                  '--index-url','https://download.pytorch.org/whl/cu126'])
    add('dependencies', [uv,'pip','install','--python',python,'-e',str(vendor)+'[video]',
                         'huggingface_hub[hf-xet]','av'])
    add('assets', [python,HERE/'download_backend_assets.py','--root',root,'--workers','2'])
    return {'ok':True,'code':'PLAN_ONLY','workspace':str(root),'source_commit':COMMIT,
            'environment':env,'commands':commands,'backend_python':str(python),
            'blender':str(Path(blender).resolve()),'ffmpeg_input':str(Path(ffmpeg).resolve()),
            'note':'A plan is not an installation result. End-to-end inference and Blender transfer require separate verification.'}


def setup(args):
    uv = shutil.which('uv') or 'uv'
    plan = build_plan(args.workspace, uv, args.blender, args.ffmpeg)
    if args.plan_only:
        return plan
    for name, path in (('Blender',args.blender),('FFmpeg',args.ffmpeg)):
        if not Path(path).is_file():
            raise ValueError(name+' executable is missing: '+str(path))
    if not shutil.which('git') or not shutil.which('uv'):
        raise ValueError('Install Git and uv before running this Windows setup recipe.')
    root = Path(plan['workspace'])
    root.mkdir(parents=True,exist_ok=True)
    (root/'vendor').mkdir(exist_ok=True)
    for path in plan['environment'].values():
        Path(path).mkdir(parents=True,exist_ok=True)
    env = os.environ.copy()
    env.update(plan['environment'])
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
    logs = root/'evidence/setup'/('run-' + uuid.uuid4().hex)
    logs.mkdir(parents=True,exist_ok=False)
    (logs/'plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    vendor = root/'vendor/GEM-X'
    if (vendor/'.git').exists():
        dirty = subprocess.run(['git','-C',str(vendor),'status','--porcelain','--untracked-files=no'],
                               capture_output=True,text=True,check=True)
        if dirty.stdout.strip():
            raise ValueError('Preserve local GEM source edits; setup refuses to change a dirty checkout.')
    for relative in ('config.generated.json', 'runtime/gem_body_video_8gb.yaml', 'runtime/bin/ffmpeg.exe'):
        previous = root/relative
        if previous.exists():
            if not previous.is_file():
                raise ValueError('Expected a managed file; preserve this path before setup: '+str(previous))
            backup = logs/'backup'/relative
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(previous,backup)
    for step in plan['commands']:
        log = logs/(step['stage']+'.log')
        with log.open('wb') as output:
            p = subprocess.run(step['argv'],cwd=step['cwd'],env=env,stdout=output,stderr=subprocess.STDOUT)
        if p.returncode:
            return {'ok':False,'code':'SETUP_STAGE_FAILED','stage':step['stage'],
                    'log':str(log),'exit_code':p.returncode,'next_action':'Resolve the log and rerun; valid pinned downloads can be reused.'}
    binary = root/'runtime/bin/ffmpeg.exe'
    binary.parent.mkdir(parents=True,exist_ok=True)
    if Path(args.ffmpeg).resolve() != binary.resolve():
        shutil.copyfile(args.ffmpeg,binary)
    # Resolve paths and reduce minibatches, without changing official vendor source.
    motion_config = root/'runtime/gem_body_video_8gb.yaml'
    edit = (
        'from omegaconf import OmegaConf; import sys; from pathlib import Path; '
        'r=Path(sys.argv[1]); c=OmegaConf.load(r/"vendor/GEM-X/weights/body_video_native77.yaml"); '
        'c.pose.checkpoint=str(r/"vendor/GEM-X/weights/body_s240000.ckpt"); c.pose.batch_size=1; '
        'c.observations.checkpoint=str(r/"vendor/GEM-X/weights/vitpose.pth"); c.observations.batch_size=1; '
        'c.detector.checkpoint=str(r/"vendor/GEM-X/weights/yolox_humanart.onnx"); '
        'c.motion.checkpoint=str(r/"vendor/GEM-X/weights/motion_native77_e774_s292175.ckpt"); '
        'c.motion.stats=str(r/"vendor/GEM-X/weights/motion_stats_raw.npz"); '
        'OmegaConf.save(c,sys.argv[2])')
    with (logs/'configuration.log').open('wb') as output:
        p = subprocess.run([plan['backend_python'],'-c',edit,str(root),str(motion_config)],
                           env=env,stdout=output,stderr=subprocess.STDOUT)
    if p.returncode:
        raise ValueError('Motion config generation failed; inspect '+str(logs/'configuration.log'))
    config = {'schema_version':1,'blender':plan['blender'],'ffmpeg':str(binary),
              'ffprobe':'','media_python':plan['backend_python'],
              'backend':{'python':plan['backend_python'],'root':str(vendor),
                         'motion_config':str(motion_config),'soma_assets':str(vendor/'assets/soma'),
                         'revision':COMMIT,'device':'cuda'}}
    config_path = root/'config.generated.json'
    config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'ok':True,'code':'SETUP_COMMANDS_FINISHED','config':str(config_path),'logs':str(logs),
            'next_action':{'command':[sys.executable,str(HERE/'motion_workbench.py'),'doctor','--config',str(config_path)],
                           'reason':'Installation commands finished; a real short video must still be validated.'}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',required=True)
    p.add_argument('--blender',required=True)
    p.add_argument('--ffmpeg',required=True)
    p.add_argument('--plan-only',action='store_true')
    args = p.parse_args()
    try:
        out = setup(args)
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        out = {'ok':False,'code':'SETUP_ERROR','message':str(exc)}
    print(json.dumps(out,ensure_ascii=False))
    return 0 if out['ok'] else 2


if __name__ == '__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
