"""CPU-only official GEM-X YOLOX/ByteTracker evidence entry point.

--shots is a JSON file containing [{"shot_id":"s1","source_zero_start":0,
"source_zero_end":149}]; bounds are inclusive decoded-source indices. Sorted,
nonoverlapping ranges only. Every shot resets tracking, never actor identity.
--threads2 is explicit documentation of the fixed two-thread CPU policy.
--plan-only uses the standard library, creates nothing and downloads nothing.

Outputs: frames.jsonl plus receipt.json in an exclusively NEW directory.
raw_detections means ALL results returned by official detect(), including invalid
and low-score results, NOT pre-threshold logits or pre-NMS model outputs. Invalid
nonfinite numeric values are retained as NaN/Infinity/-Infinity strings with an
explicit raw_numeric_encoding declaration. Lost-buffer boxes are never promoted
to observations. Requested-range completion does not prove full-video coverage,
identity correctness, skeleton agreement, pose, 3D or delivery acceptance.
"""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import sys
import types

DEPENDENCIES = ['av', 'numpy', 'onnxruntime', 'cv2', 'scipy']
CODE_FILES = ('gem/common/detectors/yolox.py', 'gem/common/detectors/bytetrack.py')
PARAMETERS = {'detector': {'device': 'cpu', 'input_size': [640, 640], 'score_thr': .5, 'nms_thr': .45},
              'tracker': {'high_thr': .5, 'match_thr': .3, 'max_lost': 30}}
RESOURCES = {'device': 'cpu', 'gpu_used': False, 'ort_intra_threads': 2, 'ort_inter_threads': 1,
             'decode_threads': 2, 'opencv_threads': 1, 'blas_threads': 2}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def read_shots(path):
    def nonfinite(value):
        raise ValueError(f'nonfinite JSON: {value}')
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=_unique, parse_constant=nonfinite)
    return validate_shots(value)


def validate_shots(shots):
    if not isinstance(shots, list) or not shots:
        raise ValueError('shots must be a nonempty JSON list')
    previous_end, names = -1, set()
    for shot in shots:
        if not isinstance(shot, dict):
            raise ValueError('shot must be an object')
        name, start, end = (shot.get(k) for k in ('shot_id', 'source_zero_start', 'source_zero_end'))
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('shot_id must be unique and nonempty')
        if type(start) is not int or type(end) is not int or start < 0 or end < start or start <= previous_end:
            raise ValueError('shot bounds must be nonnegative sorted nonoverlapping inclusive integers')
        previous_end = end
        names.add(name)
    return shots


def _number(value):
    value = float(value)
    return value if math.isfinite(value) else ('NaN' if math.isnan(value) else 'Infinity' if value > 0 else '-Infinity')


def _snapshot(tracker):
    result = {}
    for track in tracker.tracks:
        tid = int(track.id)
        if tid in result:
            raise ValueError('duplicate tracker ID')
        result[tid] = {'time_since_update': int(track.time_since_update), 'hits': int(track.hits),
                       'last_observed_detection_score': _number(track.score)}
    return result


def process_frames(shots, frames, detector, tracker_factory):
    """Yield evidence for requested indices; injected fakes need no numpy/av.

    Tracker adapter accepts Python lists and exposes official tracks/update.
    EOF before requested end raises after any completed records were yielded.
    """
    validate_shots(shots)
    shot_index, tracker, count = 0, None, 0
    expected = sum(s['source_zero_end'] - s['source_zero_start'] + 1 for s in shots)
    for zero, frame in enumerate(itertools.islice(frames, shots[-1]['source_zero_end'] + 1)):
        while zero > shots[shot_index]['source_zero_end']:
            shot_index += 1
            tracker = None
        shot = shots[shot_index]
        if zero < shot['source_zero_start']:
            continue
        if tracker is None:
            tracker = tracker_factory()
        if frame.pts is None or frame.time_base is None:
            raise ValueError(f'missing source PTS/time_base at {zero}')
        time_base = Fraction(frame.time_base)
        if time_base <= 0 or type(frame.pts) is not int:
            raise ValueError(f'invalid source PTS/time_base at {zero}')
        boxes, scores = detector.detect(frame.to_ndarray(format='bgr24'))
        if len(boxes) != len(scores):
            raise ValueError('detector box/score count mismatch')
        raw, valid_boxes, valid_scores = [], [], []
        for index, (box, score) in enumerate(zip(boxes, scores)):
            box, score = [float(v) for v in box], float(score)
            valid = (len(box) == 4 and all(math.isfinite(v) for v in box) and math.isfinite(score)
                     and box[2] > box[0] and box[3] > box[1])
            raw.append({'frame_local_detection_index': index, 'box_xyxy': [_number(v) for v in box],
                        'score': _number(score), 'fed_to_tracker': valid, 'actor_id': None,
                        'source': 'observed', 'rejection_reason': None if valid else 'invalid_geometry_or_nonfinite'})
            if valid:
                valid_boxes.append(box)
                valid_scores.append(score)
        before = _snapshot(tracker)
        matched = tracker.update(valid_boxes, valid_scores)
        after = _snapshot(tracker)
        new, expired = sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())
        observed = []
        for box, track_id, score in matched:
            tid = int(track_id)
            if tid not in after or after[tid]['time_since_update'] != 0:
                raise ValueError('tracker returned non-observed track')
            observed.append({'window_local_track_id': tid, 'box_xyxy': [_number(v) for v in box],
                'score': _number(score), 'observed': True, 'source': 'observed', 'actor_id': None,
                'box_kind': 'official_Kalman_corrected_after_detection', 'new_track': tid in new,
                'recovered_from_lost': tid in before and before[tid]['time_since_update'] > 0,
                'time_since_update': 0, 'hits': after[tid]['hits']})
        ids = [o['window_local_track_id'] for o in observed]
        if len(set(ids)) != len(ids) or set(ids) != {t for t, state in after.items() if state['time_since_update'] == 0}:
            raise ValueError('tracker observed set inconsistent with buffer')
        lost = [{'window_local_track_id': tid, **state, 'observed': False, 'source': 'unknown',
                 'actor_id': None, 'predicted_box_saved': False}
                for tid, state in sorted(after.items()) if state['time_since_update'] > 0]
        lifecycle = {'new': new, 'expired': expired,
                     'recovered': sorted(o['window_local_track_id'] for o in observed if o['recovered_from_lost']),
                     'became_lost': sorted(tid for tid, state in after.items() if state['time_since_update'] > 0
                                           and before.get(tid, {}).get('time_since_update') == 0)}
        count += 1
        yield {'window': shot['shot_id'], 'source_zero_based': zero, 'blender_one_based': zero + 1,
               'pts': frame.pts, 'time_base': str(frame.time_base), 'seconds': float(frame.pts * time_base),
               'raw_detections': raw, 'observed_tracks': observed, 'lost_buffer': lost,
               'lifecycle': lifecycle, 'empty_observed_frame': not observed,
               'identity_correctness': 'unknown', 'raw_numeric_encoding': 'nonfinite values encoded as named strings'}
    if count != expected:
        raise ValueError(f'incomplete requested ranges: {count}/{expected} frames')


def _module(name, path):
    # Direct file loading avoids GEM package __init__ and its Torch imports.
    # compile/exec also avoids creating __pycache__ in the supplied repository.
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def load_runtime(args):
    """Only real execution imports optional packages or official local modules."""
    if 'torch' in sys.modules:
        raise RuntimeError('Torch already imported; use a fresh CPU-only process')
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '2'
    import av
    import numpy as np
    import cv2
    import onnxruntime as ort
    cv2.setNumThreads(1)
    yolo = _module('multi_detect_official_yolox', args.repo / CODE_FILES[0])
    tracking = _module('multi_detect_official_bytetrack', args.repo / CODE_FILES[1])
    original = ort.SessionOptions
    def options():
        value = original()
        value.intra_op_num_threads = 2
        value.inter_op_num_threads = 1
        return value
    ort.SessionOptions = options
    try:
        detector = yolo.YOLOXONNX(str(args.model), **{**PARAMETERS['detector'], 'input_size': (640, 640)})
    finally:
        ort.SessionOptions = original
    if detector.execution_providers != ['CPUExecutionProvider']:
        raise RuntimeError('CPU-only execution provider required')
    actual_options = detector.sess.get_session_options()
    if actual_options.intra_op_num_threads != 2 or actual_options.inter_op_num_threads != 1:
        raise RuntimeError('ONNX session did not retain thread limits')
    if 'torch' in sys.modules:
        raise RuntimeError('unexpected Torch import')
    class Adapter:
        def __init__(self):
            self.official = tracking.ByteTracker(**PARAMETERS['tracker'])

        @property
        def tracks(self):
            return self.official.tracks

        def update(self, boxes, scores):
            return self.official.update(np.asarray(boxes, dtype=np.float32).reshape(-1, 4),
                                        np.asarray(scores, dtype=np.float32).reshape(-1))

    def frames():
        with av.open(str(args.source)) as container:
            stream = container.streams.video[0]
            stream.codec_context.thread_count = 2
            yield from container.decode(stream)
    return detector, Adapter, frames


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ('source', 'repo', 'model', 'shots', 'output-new-dir'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--model-sha256', required=True)
    parser.add_argument('--threads2', action='store_true', help='fixed policy: CPU two threads, never GPU')
    parser.add_argument('--plan-only', action='store_true')
    return parser


def make_plan(args, shots):
    argv = [sys.executable, '-B', str(Path(__file__).resolve())]
    for flag in ('source', 'source-sha256', 'repo', 'model', 'model-sha256', 'shots', 'output-new-dir'):
        argv.extend(['--' + flag, str(getattr(args, flag.replace('-', '_')))])
    argv.append('--threads2')
    return {'status': 'plan_only', 'argv': argv, 'dependencies': DEPENDENCIES,
            'resources': RESOURCES, 'shots': shots, 'parameters': PARAMETERS,
            'expected_frames': sum(s['source_zero_end'] - s['source_zero_start'] + 1 for s in shots),
            'downloads': False, 'delivery_ready': False,
            'warnings': ['Only requested ranges, not full-video coverage.',
                         'Track IDs are shot-local association, never actor identity.',
                         'Raw means official detect() output, after its threshold/NMS (.5/.45).']}


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def execute(args):
    """Execute or plan; failure receipt binds any partial JSONL actually written."""
    try:
        shots = read_shots(args.shots)
        for expected in (args.source_sha256, args.model_sha256):
            if len(expected) != 64 or any(c not in '0123456789abcdefABCDEF' for c in expected):
                raise ValueError('SHA256 must contain exactly 64 hexadecimal characters')
        if args.plan_only:
            print(json.dumps(make_plan(args, shots), ensure_ascii=False))
            return 0
        args.output_new_dir.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc), 'delivery_ready': False}))
        return 2
    receipt = {'status': 'running', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
               'actual_frames': 0, 'per_shot_actual_frames': {s['shot_id']: 0 for s in shots},
               'expected_frames': sum(s['source_zero_end'] - s['source_zero_start'] + 1 for s in shots),
               'requested_ranges_complete': False, 'full_video_coverage_verified': False,
               'delivery_ready': False, 'pose_or_3d_run': False, 'identity_correctness': 'unknown',
               'resources': RESOURCES, 'parameters': PARAMETERS, 'shots': shots,
               'inputs': {}, 'outputs': {}, 'errors': []}
    output_path = args.output_new_dir / 'frames.jsonl'
    bindings = {'source': args.source, 'model': args.model, 'shots': args.shots,
                'entry_code': Path(__file__).resolve(), **{p: args.repo / p for p in CODE_FILES}}
    initial = {}
    frame_iterator = None
    try:
        for name, path in bindings.items():
            initial[name] = sha256(path)
            receipt['inputs'][name] = {'path': str(path), 'sha256': initial[name]}
        if initial['source'] != args.source_sha256.lower() or initial['model'] != args.model_sha256.lower():
            raise ValueError('source or model SHA256 mismatch; inference not started')
        # Rebind parsed shots to the exact file digest before loading optional code.
        if read_shots(args.shots) != shots:
            raise ValueError('shots changed during preflight')
        detector, tracker_factory, frames = load_runtime(args)
        receipt['actual_interpreter'] = sys.executable
        receipt['actual_execution_providers'] = getattr(detector, 'execution_providers', None)
        frame_iterator = frames()
        with output_path.open('x', encoding='utf-8') as stream:
            for row in process_frames(shots, frame_iterator, detector, tracker_factory):
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                stream.flush()
                receipt['actual_frames'] += 1
                receipt['per_shot_actual_frames'][row['window']] += 1
        if 'torch' in sys.modules:
            raise RuntimeError('unexpected Torch import during tracking')
        for name, path in bindings.items():
            if sha256(path) != initial[name]:
                raise ValueError(f'input/code changed during execution: {name}')
        receipt['requested_ranges_complete'] = True
        receipt['status'] = 'requested_ranges_complete_manual_identity_review_required'
    except (Exception, KeyboardInterrupt) as exc:
        # Retain partial evidence and actual counts; never turn failure into success.
        receipt['status'] = 'failed'
        receipt['errors'].append(f'{type(exc).__name__}: {exc}')
    finally:
        if frame_iterator is not None and hasattr(frame_iterator, 'close'):
            try:
                frame_iterator.close()
            except (Exception, KeyboardInterrupt) as exc:
                receipt['status'] = 'failed'
                receipt['requested_ranges_complete'] = False
                receipt['errors'].append(f'decoder cleanup {type(exc).__name__}: {exc}')
    try:
        if output_path.is_file():
            receipt['outputs']['frames.jsonl'] = sha256(output_path)
        receipt['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        receipt['torch_imported'] = 'torch' in sys.modules
        receipt['receipt_self_hash_excluded'] = True
        _write(args.output_new_dir / 'receipt.json', receipt)
    except OSError as exc:
        print(json.dumps({'status': 'failed', 'error': f'receipt write failed: {exc}', 'delivery_ready': False}))
        return 2
    print(json.dumps({'status': receipt['status'], 'actual_frames': receipt['actual_frames'],
                      'receipt': str(args.output_new_dir / 'receipt.json'), 'delivery_ready': False}))
    return 0 if receipt['requested_ranges_complete'] else 2


if __name__ == '__main__':
    raise SystemExit(execute(make_parser().parse_args()))
