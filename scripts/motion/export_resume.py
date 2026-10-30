"""Verified, CPU-only continuation of an extract that failed at BVH export.

Receipts are explicit retrospective audits for legacy jobs: they bind the
present failed snapshot, commands, backend and checkpoint bytes. They do not
claim that the old controller recorded hashes/exit receipts when producing PT.
"""
from pathlib import Path
import hashlib
import json
import math
import os
import re
import subprocess


def fail(wb, code, message):
    raise wb.WorkbenchError(code, message)


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


def fingerprint(job):
    value = {k:job.get(k) for k in ('schema_version','job_id','inputs','video_metadata','interval','outputs','motion_provenance','failure')}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def expected_commands(wb, directory, job, attempt, config, *, original_exporter):
    b = config['backend'];interval = job['interval']
    return {
        'inference':[b['python'],'-m','gem.inference','motion-video','--task','body',
                     '--input',job['inputs']['video']['path'],'--config',b['motion_config'],
                     '--start',str(interval['start_seconds']),'--duration',str(interval['duration']),
                     '--device',b.get('device','cuda'),'--output',str(attempt/'motion_raw.pt')],
        'contact_postprocess':[b['python'],'-m','gem.inference','motion-cache','--task','body',
                               '--input',str(attempt/'motion_raw.pt'),'--postprocess','--device','cpu',
                               '--soma-assets',b['soma_assets'],'--output',str(attempt/'motion.pt')],
        'export':[b['python'],str(original_exporter),'--input',str(attempt/'motion.pt'),
                  '--assets',b['soma_assets'],'--output',str(attempt/'motion.bvh'),'--fps','30'],
    }


def tree_digest(root, paths, *, absent_skip_worktree=()):
    root = Path(root).resolve();items=[]
    allowed_absent={canonical(p) for p in absent_skip_worktree}
    for path in sorted(paths,key=lambda x:str(x)):
        p = Path(path).resolve()
        if not p.is_relative_to(root):
            raise ValueError('Backend tree path is missing or escapes its root: '+str(p))
        if not p.exists() and canonical(p) in allowed_absent:
            # Sparse checkout intentionally leaves these tracked paths absent.
            # Their committed bytes are bound by the separately checked HEAD;
            # the absent/present state is part of this working-tree digest.
            items.append({'relative_path':p.relative_to(root).as_posix(),'absent_skip_worktree':True})
            continue
        if not p.is_file():
            raise ValueError('Backend tree path is missing or not a file: '+str(p))
        h = hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
        items.append({'relative_path':p.relative_to(root).as_posix(),'sha256':h.hexdigest(),'bytes':p.stat().st_size})
    return {'root':str(root),'file_count':len(items),'sha256':hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()}


def backend_snapshot(config, *, wb):
    b = config['backend'];root = Path(b['root']).resolve()
    rev = subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True,timeout=20)
    if rev.returncode or rev.stdout.strip()!=b['revision']:
        fail(wb,'BACKEND_REVISION_MISMATCH','Configured backend revision differs from the checkout.')
    ls = subprocess.run(['git','-C',str(root),'ls-files','-v','-z'],capture_output=True,timeout=20)
    if ls.returncode:fail(wb,'BACKEND_SNAPSHOT_FAILED','Cannot enumerate tracked backend files.')
    records=[x for x in ls.stdout.split(b'\0') if x]
    tracked=[root/os.fsdecode(x[2:]) for x in records]
    absent=[root/os.fsdecode(x[2:]) for x in records if x[:1]==b'S']
    assets=Path(b['soma_assets']).resolve()
    return {'revision':b['revision'],'tracked_source_tree':tree_digest(root,tracked,absent_skip_worktree=absent),
            'soma_assets_tree':tree_digest(assets,[p for p in assets.rglob('*') if p.is_file()]),
            'motion_config':wb.artifact(b['motion_config']),'python':wb.artifact(b['python'])}


def checkpoint_metadata(wb, path, job):
    import numpy as np
    import torch
    from export_gem_bvh import load_prediction_checkpoint
    unsafe = set(torch.serialization.get_unsafe_globals_in_checkpoint(path))
    allowed = {'builtins.bytes','numpy.ndarray','numpy.dtype','numpy._core.multiarray._reconstruct','numpy.core.multiarray._reconstruct'}
    if unsafe-allowed:fail(wb,'UNSAFE_CHECKPOINT_GLOBAL','Unexpected serialized globals: '+str(sorted(unsafe-allowed)))
    try:
        result = load_prediction_checkpoint(path)
    except Exception as exc:
        fail(wb,'CHECKPOINT_RESTRICTED_LOAD_FAILED',f'Restricted checkpoint load failed: {type(exc).__name__}: {exc}')
    if not isinstance(result,dict) or result.get('format')!='gem_body_video_predictions_v1':
        fail(wb,'CHECKPOINT_SCHEMA','Expected GEM body-video predictions.')
    source = result.get('video',{}).get('source',{});normal = result.get('video',{}).get('normalized',{})
    interval = job['interval'];meta = job['video_metadata']
    if canonical(source.get('path',''))!=canonical(job['inputs']['video']['path']):
        fail(wb,'CHECKPOINT_SOURCE_MISMATCH','Checkpoint source video path differs.')
    numeric=[('requested_start',interval['start_seconds']),('requested_duration',interval['duration']),
             ('selected_duration',interval['duration']),('fps',meta['fps_num']/meta['fps_den']),
             ('width',meta['width']),('height',meta['height']),('duration',meta['duration'])]
    if any(not isinstance(source.get(k),(int,float)) or not math.isclose(source[k],v,abs_tol=1e-6,rel_tol=0) for k,v in numeric):
        fail(wb,'CHECKPOINT_SOURCE_MISMATCH','Checkpoint source interval or video metadata differs.')
    n = int(torch.as_tensor(result['length']).reshape(-1)[0])
    if torch.as_tensor(result['length']).numel()!=1 or n<2:
        fail(wb,'CHECKPOINT_LENGTH','Expected one nonempty sequence.')
    if (normal.get('frame_count')!=n or not math.isclose(normal.get('fps',0),30.,abs_tol=1e-6)
            or abs(n/30-interval['duration'])>2/30):
        fail(wb,'CHECKPOINT_LENGTH','Normalized frame count/fps differs from the job interval.')
    times=torch.as_tensor(result.get('frame_times',[])).reshape(-1)
    if times.numel()!=n or not torch.isfinite(times).all() or not torch.allclose(times[1:]-times[:-1],torch.full_like(times[1:],1/30),atol=1e-6,rtol=0):
        fail(wb,'CHECKPOINT_TIMING','Expected a finite 30Hz frame timeline.')
    params=result.get('pred_soma_params_global',{})
    for name in ('body_pose','global_orient','identity_coeffs','scale_params','transl'):
        t=torch.as_tensor(params[name])
        if tuple(t.shape[:2])!=(1,n) or not torch.isfinite(t).all():
            fail(wb,'CHECKPOINT_PARAMETERS','Unexpected/non-finite '+name)
    return result, {'format':result['format'],'frames':n,'video':result['video'],
                    'sampling_fps':result.get('sampling_fps'),'coordinates':result.get('coordinates'),
                    'postprocessing_method':result.get('postprocessing',{}).get('method'),
                    'unsafe_globals_static':sorted(unsafe)}


def audit_checkpoint(wb, directory, attempt_name, raw_sha256, processed_sha256, *, original_exporter):
    directory,job=wb.load_job(directory)
    if not re.fullmatch(r'extract-[0-9a-f]{12}',attempt_name):
        fail(wb,'WRONG_ATTEMPT','Expected an exact extract attempt name, not a path.')
    attempt=(directory/'attempts'/attempt_name).resolve()
    if not attempt.is_relative_to((directory/'attempts').resolve()) or not attempt.is_dir():
        fail(wb,'WRONG_ATTEMPT','Attempt is outside the selected job.')
    if (job.get('outputs') or job.get('motion_provenance') is not None
            or job.get('failure',{}).get('stage')!='extract'
            or job.get('failure',{}).get('code')!='PROCESS_FAILED'):
        fail(wb,'WRONG_FAILURE_STAGE','Resume only an unregistered extract PROCESS_FAILED at export.')
    expected_failure=f'Process exited 1. Inspect {attempt/"export.log"}'
    if job['failure']['message']!=expected_failure:
        fail(wb,'WRONG_FAILURE_STAGE','Failure does not identify this exact export.log.')
    if any(not wb.intact(a) for a in job['inputs'].values()):
        fail(wb,'STALE_INPUTS','A job input changed.')
    raw=attempt/'motion_raw.pt';processed=attempt/'motion.pt'
    if (not re.fullmatch(r'[0-9a-f]{64}',raw_sha256) or not re.fullmatch(r'[0-9a-f]{64}',processed_sha256)
            or wb.digest(raw)!=raw_sha256 or wb.digest(processed)!=processed_sha256):
        fail(wb,'CHECKPOINT_SHA_MISMATCH','Checkpoint bytes differ from the explicitly selected SHA.')
    if (attempt/'motion.bvh').exists() or (attempt/'motion.bvh.json').exists():
        fail(wb,'PARTIAL_EXPORT_PRESENT','Do not register pre-existing BVH/sidecar leftovers.')
    config=wb.read_config(job['inputs']['config']['path']);original_exporter=Path(original_exporter).resolve()
    commands=expected_commands(wb,directory,job,attempt,config,original_exporter=original_exporter)
    evidence={}
    for name,expected in commands.items():
        p=attempt/(name+'.command.json')
        if wb.read_json(p)!=expected:
            fail(wb,'ATTEMPT_COMMAND_MISMATCH','Unexpected argv for '+name)
        evidence[name+'_command']=wb.artifact(p);evidence[name+'_log']=wb.artifact(attempt/(name+'.log'))
    raw_log=(attempt/'inference.log').read_text(encoding='utf8',errors='replace')
    post_log=(attempt/'contact_postprocess.log').read_text(encoding='utf8',errors='replace')
    export_log=(attempt/'export.log').read_text(encoding='utf8',errors='replace')
    if (not raw_log.rstrip().endswith('Saved motion-video predictions to '+str(raw))
            or not post_log.rstrip().endswith('Saved motion-cache predictions to '+str(processed))):
        fail(wb,'POSTPROCESS_NOT_COMPLETE','Both producing stages need their exact saved-output log marker.')
    if not ('Weights only load failed' in export_log and 'Unsupported global: GLOBAL bytes' in export_log):
        fail(wb,'WRONG_EXPORT_FAILURE','This recovery handles only the confirmed restricted bytes load failure.')
    raw_result,raw_meta=checkpoint_metadata(wb,raw,job);result,meta=checkpoint_metadata(wb,processed,job)
    if meta['postprocessing_method']!='soma_contact_translation_ccd_ik' or raw_meta['postprocessing_method'] is not None:
        fail(wb,'POSTPROCESS_NOT_COMPLETE','Expected raw inference then completed SOMA CPU postprocess.')
    import torch
    prior=result.get('raw_predictions',{}).get('pred_soma_params_global',{})
    for key,value in raw_result['pred_soma_params_global'].items():
        if key not in prior or not torch.equal(torch.as_tensor(value),torch.as_tensor(prior[key])):
            fail(wb,'RAW_PROCESSED_LINK_MISMATCH','Processed raw_predictions does not bind the selected raw PT: '+key)
    if raw_meta['video']!=meta['video']:
        fail(wb,'CHECKPOINT_SOURCE_MISMATCH','Raw and processed source metadata differ.')
    return {'schema_version':1,'kind':'retrospective_verified_export_checkpoint','created_at':wb.now(),
            'source_job':wb.artifact(directory/'job.json'),'job_fingerprint':fingerprint(job),
            'attempt':{'name':attempt_name,'path':str(attempt)},'inputs':job['inputs'],
            'interval':job['interval'],'backend':backend_snapshot(config,wb=wb),'stage_evidence':evidence,
            'raw_parameters':wb.artifact(raw),'processed_parameters':wb.artifact(processed),
            'raw_metadata':raw_meta,'processed_metadata':meta,'original_exporter':wb.artifact(original_exporter),
            'limitation':'Legacy extract did not write producing-time checkpoint hashes or successful exit-code receipts. This audit binds the present completed PT/source/argv/log evidence and caller-selected hashes; it is not a claim of historical byte attestation.'}


def validate_receipt(wb, directory, job, receipt):
    if receipt.get('schema_version')!=1 or receipt.get('kind')!='retrospective_verified_export_checkpoint':
        fail(wb,'INVALID_CHECKPOINT_RECEIPT','Unknown checkpoint receipt.')
    if fingerprint(job)!=receipt.get('job_fingerprint'):
        fail(wb,'RESUME_JOB_MISMATCH','Job identity, failed snapshot, inputs or interval changed.')
    if not wb.intact(receipt['source_job']):
        fail(wb,'STALE_SOURCE_JOB','Producing job snapshot changed since audit.')
    original_directory=Path(receipt['source_job']['path']).parent
    rebuilt=audit_checkpoint(wb,original_directory,receipt['attempt']['name'],
                             receipt['raw_parameters']['sha256'],receipt['processed_parameters']['sha256'],
                             original_exporter=Path(receipt['original_exporter']['path']))
    for key in ('job_fingerprint','attempt','inputs','interval','backend','stage_evidence','raw_parameters',
                'processed_parameters','raw_metadata','processed_metadata','original_exporter'):
        if rebuilt[key]!=receipt[key]:
            fail(wb,'STALE_CHECKPOINT_RECEIPT','Checkpoint binding changed: '+key)
    return rebuilt


def resume_export(wb, directory, job, receipt_path, receipt_sha256):
    receipt_path=Path(receipt_path).resolve()
    if not receipt_sha256 or wb.digest(receipt_path)!=receipt_sha256:
        fail(wb,'CHECKPOINT_RECEIPT_SHA_MISMATCH','Supply the exact audited receipt SHA.')
    receipt=wb.read_json(receipt_path)
    with wb.operation(directory,job,'extract',preserve_failed_snapshot=True):
        validate_receipt(wb,directory,job,receipt)
        config=wb.read_config(job['inputs']['config']['path']);b=config['backend']
        wb.invalidate(job,('motion','motion_metadata','candidate','preview','verification','bridge_report'))
        dest=wb.attempt_dir(directory,'export-recovery');motion=dest/'motion.bvh'
        env=wb.task_environment(directory);env['CUDA_VISIBLE_DEVICES']='';env['PATH']=str(Path(config['ffmpeg']).parent)+os.pathsep+env.get('PATH','')
        wb.run_logged([b['python'],wb.HERE/'export_gem_bvh.py','--input',receipt['processed_parameters']['path'],
                       '--assets',b['soma_assets'],'--output',motion,'--fps','30'],dest/'export.log',cwd=b['root'],env=env)
        # Recheck evidence after the subprocess before committing registration.
        validate_receipt(wb,directory,job,receipt)
        wb.register_extracted_motion(job,motion,Path(receipt['processed_parameters']['path']),
                                     Path(receipt['raw_parameters']['path']),b,
                                     recovery={'receipt':wb.artifact(receipt_path),'from_attempt':receipt['attempt'],
                                               'exporter':wb.artifact(wb.HERE/'export_gem_bvh.py'),'device':'cpu'})
    return wb.result(job,directory)
