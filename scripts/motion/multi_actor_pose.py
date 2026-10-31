"""Standard-library adapter for official GEM body pose-video (not Motion).

plan --candidate JSON --source VIDEO --source-sha256 SHA --intrinsics JSON
     --python EXE --repo GEM --checkpoint FILE --soma-assets DIR
     --device cpu|cuda --work NEW_DIR [--allow-inferred]
execute --plan PLAN_JSON
import-result --plan PLAN_JSON --manifest BINDING_JSON

Candidate is multi_actor.build_candidate output. Each input frame must supply
integer pts and rational time_base; detections supply box/box_xyxy/raw_box_xyxy
(xyxy), or square_bbox_xys/bbox (official xys). No decoding or gap filling.
Observed refers ONLY to the crop, never to visibility of all predicted joints.
Unknown/filled crops require opt-in and remain unobserved hypotheses.
Identity hypothesis/unknown also requires --allow-inferred, independently of
crop observation. Explicit identity status/evidence is retained; unmarked legacy
identity is unreviewed, never confirmed by a scope label or an observed crop.

Import manifest: {fingerprint, bindings: <exact plan bindings>,
 result: {path, sha256}, data_scope: "formal_arm_points_candidate_2d"|...}.
This is an externally asserted binding, not proof of inference or anatomy.
Opaque results (.pt included) are hashed, NEVER deserialized. Import does not
create an execution receipt or certify 77 joints, 3D, contacts or temporal fit.
Receipt reuse requires exact plan identity, unchanged inputs and result/log SHA.
Existing unbound outputs are refused; failures never qualify for cache reuse.
expected_actor_ids means this request's usable crop subset, NOT the scene cast.
Other actors and chains remain unverified. Actor presence, usable person crop,
and visible joint chains are independent; frame_input retains their annotations.
Existing EXISTING_POSE_BINDINGS.json is also accepted: its proposal/receipt/review
evidence chain must match source, checkpoint and exact official boxes SHA.
Execution clears PYTHONPATH/PYTHONSAFEPATH/PYTHONHOME and uses -E -s -B so the
local regular GEM package is resolved from cwd, ignoring Python env/user site.
Old plans/receipts without this controlled environment must be regenerated.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from fractions import Fraction


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _file(path):
    p = Path(path).resolve(strict=True)
    return {'path': str(p), 'sha256': sha256(p)}


def _tree(path, suffixes=None):
    root = Path(path).resolve(strict=True)
    if not root.is_dir(): raise ValueError('expected directory')
    return {str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob('*'))
            if p.is_file() and (suffixes is None or p.suffix in suffixes)}


def _write(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def _numbers(value, count):
    if (not isinstance(value, list) or len(value) != count or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError('expected finite numeric vector')
    return value


def _identity(candidate, row, actor, detection, allow_inferred):
    """Carry identity claims separately from crop/joint observation."""
    claims = [actor, detection]
    for mapped in candidate.get('mapping_input', {}).get('frames', []):
        if (mapped.get('shot_id', mapped.get('window')) == row['shot_id'] and
                mapped.get('frame', mapped.get('source_zero_based')) == row['frame']):
            claims.extend(m for m in mapped.get('mappings', [])
                          if m.get('actor_id') == actor['actor_id'] and
                          m.get('track_id') == actor['track_id'])
    statuses = {c['identity_status'] for c in claims if 'identity_status' in c}
    if statuses - {'confirmed', 'hypothesis', 'unknown', 'unreviewed'}:
        raise ValueError('unsupported identity status')
    if len(statuses) > 1:
        raise ValueError('conflicting identity status')
    status = next(iter(statuses), None)
    explicitly_unverified = any(c.get('identity_verified') is False for c in claims)
    if status == 'confirmed' and explicitly_unverified:
        raise ValueError('confirmed identity contradicts identity_verified=false')
    if status is None:
        status = ('unknown' if explicitly_unverified or any(
            c.get('identity_verified') is False for c in (candidate, row, row['input']))
            else 'unreviewed')
    if status in ('hypothesis', 'unknown') and not allow_inferred:
        raise ValueError('unverified identity requires explicit allow-inferred')
    evidence = next((c['evidence'] for c in claims if 'evidence' in c), [])
    return {'identity_status': status, 'evidence': copy.deepcopy(evidence),
            'identity_verified': status == 'confirmed',
            'identity_provenance': [copy.deepcopy(c) for c in claims
                                    if any(k in c for k in ('identity_status', 'identity_verified', 'evidence'))]}


def _environment_policy(repo):
    return {'cwd': str(repo), 'python_flags': ['-E', '-s', '-B'],
            'variables': {'PYTHONPATH': None, 'PYTHONSAFEPATH': None, 'PYTHONHOME': None,
                          'PYTHONDONTWRITEBYTECODE': '1'}}


def _local_package(repo):
    for name in ('gem/__init__.py', 'gem/inference/__init__.py', 'gem/inference/__main__.py'):
        if not (Path(repo)/name).is_file():
            raise ValueError('repo must contain local regular GEM package: '+name)


def _boxes(candidate, intrinsics, allow_inferred):
    if candidate.get('valid') is not True or candidate.get('errors') != []:
        raise ValueError('requires valid multi_actor candidate with no errors')
    if len(intrinsics) != 3: raise ValueError('intrinsics must be 3x3')
    for row in intrinsics: _numbers(row, 3)
    if intrinsics[0][0] <= 0 or intrinsics[1][1] <= 0 or intrinsics[2] != [0, 0, 1]:
        raise ValueError('invalid pinhole intrinsics')
    frames, bindings = {}, []
    for row in candidate['frames']:
        frame, raw = row['frame'], row['input']
        if type(frame) is not int or frame < 0 or str(frame) in frames:
            raise ValueError('duplicate or invalid source frame')
        if raw.get('frame', raw.get('source_zero_based')) != frame:
            raise ValueError('source frame mismatch')
        if raw.get('shot_id', raw.get('window')) != row['shot_id']:
            raise ValueError('shot mismatch')
        pts, tb = raw.get('pts'), raw.get('time_base')
        if type(pts) is not int or not isinstance(tb, str) or Fraction(tb) <= 0:
            raise ValueError('requires original integer PTS and positive time_base')
        trial = 'observed_tracks' in raw
        detections = raw['observed_tracks' if trial else 'detections']
        instances, actors, used = [], set(), set()
        for actor in row['actors']:
            aid, index, source = actor['actor_id'], actor['detection_index'], actor['source']
            if not isinstance(aid, str) or not aid.strip() or aid in actors:
                raise ValueError('duplicate or invalid actor')
            actors.add(aid)
            if type(index) is not int or not 0 <= index < len(detections) or index in used:
                raise ValueError('invalid detection binding')
            used.add(index)
            detection = detections[index]
            if (detection.get('source', 'observed' if trial else 'unknown') != source or
                    detection.get('window_local_track_id' if trial else 'track_id') != actor['track_id']):
                raise ValueError('detection provenance mismatch')
            if source != 'observed' and not (allow_inferred and source in ('unknown', 'filled')):
                raise ValueError('unobserved crop requires explicit allow-inferred')
            if source == 'observed' and detection.get('observed') is False:
                raise ValueError('contradictory observed crop')
            identity = _identity(candidate, row, actor, detection, allow_inferred)
            if 'square_bbox_xys' in detection or 'bbox' in detection:
                bbox = _numbers(detection.get('square_bbox_xys', detection.get('bbox')), 3)
                if bbox[2] <= 0: raise ValueError('crop size must be positive')
            else:
                x1, y1, x2, y2 = _numbers(detection.get('raw_box_xyxy',
                    detection.get('box_xyxy', detection.get('box'))), 4)
                if x2 <= x1 or y2 <= y1: raise ValueError('invalid xyxy crop')
                bbox = [(x1+x2)/2, (y1+y2)/2, 1.2*max(y2-y1, (x2-x1)/.75)]
            item = {'track_id': aid, 'bbox': bbox, 'bbox_observed': source == 'observed'}
            score = detection.get('detection_score', detection.get('score', detection.get('raw_score')))
            if score is not None:
                _numbers([score], 1)
                if not 0 <= score <= 1: raise ValueError('invalid detection score')
                item['detection_score'] = score
            instances.append(item)
            bindings.append({**identity, 'shot_id': row['shot_id'], 'actor_id': aid,
                             'source_frame': frame, 'pts': pts, 'time_base': tb,
                             'source': source, 'detection': copy.deepcopy(detection),
                             'frame_input': copy.deepcopy(raw),
                             'track_id': actor['track_id'], 'visible_joint_truth': False})
        if actors != set(row['expected_actor_ids']) or len(used) != len(detections):
            raise ValueError('incomplete actor/detection mapping')
        frames[str(frame)] = {'instances': instances}
    if not bindings: raise ValueError('no inference instances')
    return {'intrinsics': intrinsics, 'frames': frames}, bindings


def prepare(candidate, *, source, source_sha256, intrinsics, python, repo,
            checkpoint, soma_assets, device, work, allow_inferred=False, official_boxes=None,
            output=None):
    boxes, bindings = _boxes(candidate, intrinsics, allow_inferred)
    source = _file(source)
    if source['sha256'] != source_sha256: raise ValueError('source SHA mismatch')
    for binding in bindings: binding['source_sha256'] = source['sha256']
    repo = str(Path(repo).resolve(strict=True))
    _local_package(repo)
    assets = str(Path(soma_assets).resolve(strict=True))
    work = Path(work).resolve()
    if work.exists(): raise ValueError('plan needs a new work directory')
    plan = {'schema_version': 1, 'source': source, 'checkpoint': _file(checkpoint),
            'python': _file(python), 'repo': repo, 'code_sha256': _tree(Path(repo)/'gem', {'.py'}),
            'soma_assets': assets, 'assets_sha256': _tree(assets),
            'candidate_sha256': _digest(candidate), 'bindings': bindings,
            'device': device, 'work': str(work), 'pose_visual_accepted': False,
            'all_actors_covered': False,
            'execution_environment': _environment_policy(repo),
            'temporal_multiactor_or_contact_solved': False, 'delivery_ready': False}
    box_path = work/'boxes.json'
    output = Path(output).resolve() if output is not None else work/'pose.pt'
    if output.exists() or output in {work/name for name in (
            'boxes.json', 'plan.json', 'receipt.json', 'process.log', 'execute.lock', 'import_binding.json')}:
        raise ValueError('output must be new and distinct from inputs and runner metadata')
    box_bytes = (json.dumps(boxes, indent=2, ensure_ascii=False, allow_nan=False)+'\n').encode()
    if official_boxes is not None:
        if _load(official_boxes) != boxes: raise ValueError('official boxes semantic mismatch')
        box_bytes = Path(official_boxes).read_bytes()
    plan['boxes'] = {'path': str(box_path), 'sha256': hashlib.sha256(box_bytes).hexdigest()}
    plan['output'] = str(output)
    plan['argv'] = [plan['python']['path'], *plan['execution_environment']['python_flags'],
                    '-m', 'gem.inference', 'pose-video',
                    '--task', 'body', '--input', source['path'], '--boxes', str(box_path),
                    '--checkpoint', plan['checkpoint']['path'], '--release', 'body',
                    '--soma-assets', assets, '--device', device, '--no-square-pixels',
                    '--output', str(output)]
    plan['fingerprint'] = _digest(plan)
    work.mkdir(parents=True)
    with box_path.open('xb') as stream: stream.write(box_bytes)
    _write(work/'plan.json', plan)
    return plan


def _verify(plan):
    identity = dict(plan)
    fingerprint = identity.pop('fingerprint')
    if _digest(identity) != fingerprint: raise ValueError('plan fingerprint mismatch')
    if plan.get('execution_environment') != _environment_policy(plan['repo']):
        raise ValueError('controlled Python environment policy missing/changed; regenerate plan')
    _local_package(plan['repo'])
    for key in ('source', 'boxes', 'checkpoint', 'python'):
        if _file(plan[key]['path']) != plan[key]: raise ValueError(key+' SHA mismatch')
    if _tree(Path(plan['repo'])/'gem', {'.py'}) != plan['code_sha256']:
        raise ValueError('GEM code changed')
    if _tree(plan['soma_assets']) != plan['assets_sha256']:
        raise ValueError('SOMA assets changed')


def execute(plan):
    """Run official argv without a shell; serialize identical job executions."""
    _verify(plan)
    work = Path(plan['work'])
    lock = work/'execute.lock'
    with lock.open('x') as stream: stream.write(str(os.getpid()))
    try:
        receipt_path = work/'receipt.json'
        imported_path = work/'import_binding.json'
        if imported_path.exists():
            imported = _load(imported_path)
            _verify_import(plan, imported)
            return {**imported, 'reused': True}
        if receipt_path.exists():
            receipt = _load(receipt_path)
            if (receipt.get('fingerprint') == plan['fingerprint'] and receipt.get('exit_code') == 0
                    and receipt.get('status') == 'process_output_unvalidated'
                    and receipt.get('inputs_unchanged') is True
                    and receipt.get('execution_environment') == plan['execution_environment']
                    and receipt.get('result') and receipt['result'] == _file(plan['output'])
                    and receipt.get('log') == _file(work/'process.log')):
                return {**receipt, 'reused': True}
            raise ValueError('existing receipt is not reusable; use new work directory')
        if Path(plan['output']).exists(): raise ValueError('unbound output already exists')
        start = time.time()
        error, exit_code = None, None
        with (work/'process.log').open('xb') as log:
            try:
                env = os.environ.copy()
                for name, value in plan['execution_environment']['variables'].items():
                    # Environment keys are case insensitive on Windows; also
                    # remove spelling variants when tested on POSIX.
                    for inherited in list(env):
                        if inherited.upper() == name: del env[inherited]
                    if value is not None: env[name] = value
                exit_code = subprocess.run(plan['argv'], cwd=plan['repo'], env=env,
                    stdout=log, stderr=subprocess.STDOUT, shell=False,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode
            except OSError as exc:
                error = str(exc)
                log.write(error.encode('utf-8'))
        unchanged = True
        try: _verify(plan)
        except (ValueError, OSError) as exc: unchanged, error = False, str(exc)
        result = _file(plan['output']) if Path(plan['output']).is_file() else None
        receipt = {'fingerprint': plan['fingerprint'], 'argv': plan['argv'],
                   'execution_environment': copy.deepcopy(plan['execution_environment']),
                   'exit_code': exit_code, 'started_unix': start, 'elapsed_seconds': time.time()-start,
                   'error': error, 'inputs_unchanged': unchanged, 'reused': False,
                   'result': result, 'log': _file(work/'process.log'),
                   'status': 'process_output_unvalidated' if exit_code == 0 and result and unchanged else 'failed',
                   'pose_visual_accepted': False, 'delivery_ready': False,
                   'temporal_multiactor_or_contact_solved': False}
        _write(receipt_path, receipt)
        return receipt
    finally:
        lock.unlink()


def _verify_import(plan, manifest):
    if (manifest['fingerprint'] != plan['fingerprint'] or manifest['bindings'] != plan['bindings']
            or _file(manifest['result']['path']) != manifest['result']):
        raise ValueError('import binding/SHA mismatch')
    if not isinstance(manifest.get('data_scope'), str) or not manifest['data_scope'].strip():
        raise ValueError('import requires explicit data_scope')
    for path, digest in manifest.get('evidence_sha256', {}).items():
        if sha256(path) != digest: raise ValueError('import evidence changed')


def _legacy_import(plan, manifest):
    """Bind reviewed sparse 2D anchors using their immutable legacy evidence."""
    evidence = manifest.get('evidence_sha256', {})
    for path, digest in evidence.items():
        if sha256(path) != digest: raise ValueError('legacy evidence SHA mismatch')
    def document(name):
        matches = [path for path in evidence if Path(path).name == name]
        if len(matches) != 1: raise ValueError('legacy import needs '+name)
        return _load(matches[0])
    proposal = document('GPU_POSE_3ANCHOR_PROPOSAL.json')
    receipt = document('cpu_trial_result.json')
    review = document('POSE_RESULT_REVIEW.json')
    for key in ('source', 'checkpoint', 'boxes'):
        if (proposal[key]['sha256'] != plan[key]['sha256'] or
                _file(proposal[key]['path'])['sha256'] != plan[key]['sha256']):
            raise ValueError('legacy '+key+' mismatch')
    proposal_path = next(path for path in evidence if Path(path).name == 'GPU_POSE_3ANCHOR_PROPOSAL.json')
    if (receipt.get('exit_code') != 0 or receipt.get('proposal_sha256') != sha256(proposal_path)
            or receipt.get('source_sha256_after') != plan['source']['sha256']
            or receipt.get('boxes_sha256_after') != plan['boxes']['sha256']):
        raise ValueError('legacy receipt mismatch')
    result = _file(receipt['output'])
    if (result['sha256'] != receipt['output_sha256'] or result['sha256'] != review['input_sha256']
            or review['source_sha256'] != plan['source']['sha256']
            or manifest['source']['sha256'] != plan['source']['sha256']):
        raise ValueError('legacy review/result mismatch')
    expected = {(b['actor_id'], b['source_frame']): b for b in plan['bindings']}
    seen = set()
    for binding in manifest['bindings']:
        key = (binding['actor_id'], binding['source_zero_based'])
        if key not in expected or key in seen: raise ValueError('legacy actor/frame mismatch')
        seen.add(key)
        current = expected[key]
        if (binding['source_sha256'] != plan['source']['sha256'] or binding['pts'] != current['pts']
                or Fraction(binding['time_base']) != Fraction(current['time_base'])):
            raise ValueError('legacy PTS/source mismatch')
        if not isinstance(binding.get('formal_arm_points'), dict):
            raise ValueError('legacy import needs sparse formal_arm_points')
    if seen != set(expected): raise ValueError('legacy bindings incomplete for requested crop subset')
    return {'fingerprint': plan['fingerprint'], 'bindings': copy.deepcopy(plan['bindings']),
            'result': result, 'data_scope': 'formal_arm_points_candidate_2d_not_full_77',
            'evidence_sha256': copy.deepcopy(evidence), 'legacy_manifest': copy.deepcopy(manifest)}


def import_result(plan, manifest):
    """Verify opaque bytes and caller binding only; never manufacture a run."""
    _verify(plan)
    if 'fingerprint' not in manifest:
        manifest = _legacy_import(plan, manifest)
    _verify_import(plan, manifest)
    result = {**copy.deepcopy(manifest), 'status': 'imported_binding_unvalidated',
              'pose_visual_accepted': False, 'delivery_ready': False,
              'temporal_multiactor_or_contact_solved': False, 'content_deserialized': False}
    _write(Path(plan['work'])/'import_binding.json', result)
    return result


def _load(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj: raise ValueError('duplicate JSON key')
            obj[key] = value
        return obj
    def reject(value): raise ValueError('nonfinite JSON: '+value)
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number): raise ValueError('nonfinite JSON number')
        return number
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      object_pairs_hook=unique, parse_constant=reject, parse_float=finite_float)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest='command', required=True)
    prepare_parser = subs.add_parser('plan')
    for option in ('candidate', 'source', 'source-sha256', 'intrinsics', 'python', 'repo',
                   'checkpoint', 'soma-assets', 'device', 'work'):
        prepare_parser.add_argument('--'+option, required=True)
    prepare_parser.add_argument('--allow-inferred', action='store_true')
    prepare_parser.add_argument('--official-boxes', help='Preserve exact bytes after semantic comparison')
    prepare_parser.add_argument('--output', help='New result path (default: WORK/pose.pt)')
    for command in ('execute', 'import-result'):
        sub = subs.add_parser(command)
        sub.add_argument('--plan', required=True)
        if command == 'import-result': sub.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            options = vars(args).copy()
            del options['command']
            options['candidate'] = _load(args.candidate)
            options['intrinsics'] = _load(args.intrinsics)
            result = prepare(**options)
        elif args.command == 'execute': result = execute(_load(args.plan))
        else: result = import_result(_load(args.plan), _load(args.manifest))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if args.command == 'execute' and result['status'] == 'failed':
            return result['exit_code'] if result['exit_code'] and result['exit_code'] > 0 else 2
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
