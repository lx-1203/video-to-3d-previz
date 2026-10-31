"""Explicit, auditable 2D actor association; never automatic multi-person 3D.

CLI: --detections frames.json[l] --mapping mapping.json --output candidate.json
Detection JSON is a list or {"frames": [...]}; JSONL uses one frame per line.
Canonical frames: {shot_id, frame (zero based), detections: [{track_id, source}]}.
Trial frames use window/source_zero_based/observed_tracks/window_local_track_id;
raw_detections, lost_buffer, PTS and all other input fields are retained verbatim.
Missing detection provenance is unknown; trial observed_tracks are observed.

Mapping: {"actor_ids": ["a"], "frames": [{"shot_id": "s", "frame": 0,
"expected_actor_ids": ["a"], "mappings": [{"actor_id": "a", "track_id": 1}]}]}.
Each input frame needs an explicit expected roster (including an explicit [] for
an empty frame). Every detection needs a unique mapping; track IDs are local to
a frame and shot, never actor identities. Actors may change tracks across frames.
Null track plus source missing/unknown records absence without inventing a pose.
Observed/filled/unknown detection sources are retained, never upgraded by mapping.
Unknown evidence can be structurally valid; validity is NOT visual acceptance.
Only supplied frames are checked: jointly omitted frames cannot be detected,
and this candidate does not prove full-video coverage.
Exit 0: consistent association candidate; 2: invalid audit, still written.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

SOURCES = {'observed', 'filled', 'missing', 'unknown'}


def _id(value):
    return (type(value) is int and value >= 0) or (isinstance(value, str) and bool(value.strip()))


def _key(row):
    shot = row.get('shot_id', row.get('window'))
    frame = row.get('frame', row.get('source_zero_based'))
    if not isinstance(shot, str) or not shot.strip() or type(frame) is not int or frame < 0:
        raise ValueError('frame requires nonempty shot_id/window and nonnegative zero-based frame/source_zero_based')
    if 'shot_id' in row and 'window' in row and row['shot_id'] != row['window']:
        raise ValueError('conflicting shot_id/window')
    if 'frame' in row and 'source_zero_based' in row and row['frame'] != row['source_zero_based']:
        raise ValueError('conflicting frame/source_zero_based')
    return shot, frame


def build_candidate(detections, manual):
    """Audit supplied frames only; retain input, never prove full-video coverage."""
    errors, output = [], []
    result = {'schema_version': 1, 'status': 'internal_candidate', 'valid': False,
              'delivery_ready': False, 'automatic_multi_actor_3d': False,
              'errors': errors, 'frames': output, 'mapping_input': copy.deepcopy(manual)}
    rows = detections.get('frames') if isinstance(detections, dict) else detections
    if not isinstance(rows, list) or not rows:
        errors.append('detections must contain a nonempty frames list')
        result['input'] = copy.deepcopy(detections)
        return result
    if not isinstance(manual, dict):
        errors.append('mapping must be an object')
        manual = {}
    actors = manual.get('actor_ids')
    if not isinstance(actors, list) or any(not isinstance(a, str) or not a.strip() for a in actors):
        errors.append('actor_ids must be a list of nonempty stable strings')
        actors = []
    if len(set(actors)) != len(actors):
        errors.append('duplicate actor_ids')
    map_rows = manual.get('frames')
    if not isinstance(map_rows, list):
        errors.append('mapping frames must be a list')
        map_rows = []
    indexed = {}
    for i, row in enumerate(map_rows):
        try:
            key = _key(row)
            if key in indexed:
                errors.append(f'duplicate mapping frame {key}')
            else:
                indexed[key] = row
        except (ValueError, AttributeError) as exc:
            errors.append(f'mapping frame {i}: {exc}')
    seen = set()
    for i, row in enumerate(rows):
        audit = {'input': copy.deepcopy(row), 'actors': []}
        output.append(audit)
        try:
            key = _key(row)
        except (ValueError, AttributeError) as exc:
            errors.append(f'detection frame {i}: {exc}')
            continue
        audit.update(shot_id=key[0], frame=key[1])
        prefix = f'{key}: '
        if key in seen:
            errors.append(prefix + 'duplicate detection frame')
        seen.add(key)
        mapping = indexed.get(key)
        if mapping is None:
            errors.append(prefix + 'missing explicit frame mapping')
            mapping = {}
        expected = mapping.get('expected_actor_ids')
        if (not isinstance(expected, list) or any(not isinstance(a, str) for a in expected)
                or (isinstance(expected, list) and len(set(expected)) != len(expected))):
            errors.append(prefix + 'expected_actor_ids must be an explicit unique list')
            expected = []
        if any(a not in actors for a in expected):
            errors.append(prefix + 'expected actor not declared in actor_ids')
        audit['expected_actor_ids'] = copy.deepcopy(expected)
        trial = 'observed_tracks' in row
        if trial and 'detections' in row:
            errors.append(prefix + 'ambiguous detections and observed_tracks')
        entries = row.get('observed_tracks' if trial else 'detections')
        if not isinstance(entries, list):
            errors.append(prefix + 'missing detection list')
            entries = []
        tracks = {}
        for j, detection in enumerate(entries):
            if not isinstance(detection, dict):
                errors.append(prefix + f'detection {j} must be an object')
                continue
            tid = detection.get('window_local_track_id' if trial else 'track_id')
            source = detection.get('source', 'observed' if trial else 'unknown')
            if not _id(tid):
                errors.append(prefix + f'detection {j} has invalid track ID')
                continue
            if tid in tracks:
                errors.append(prefix + f'duplicate detection track {tid}')
            if not isinstance(source, str) or source not in SOURCES:
                errors.append(prefix + f'invalid detection source {source}')
                source = 'unknown'
            if trial and detection.get('observed') is False:
                errors.append(prefix + f'observed track {tid} contradicts observed=false')
                source = 'unknown'
            tracks[tid] = (j, source)
        assignments = mapping.get('mappings', [])
        if not isinstance(assignments, list):
            errors.append(prefix + 'mappings must be a list')
            assignments = []
        used_actors, used_tracks = set(), set()
        for assignment in assignments:
            if not isinstance(assignment, dict):
                errors.append(prefix + 'mapping entry must be an object')
                continue
            actor, tid = assignment.get('actor_id'), assignment.get('track_id')
            if not isinstance(actor, str) or actor not in expected:
                errors.append(prefix + 'mapped actor absent from expected roster')
                continue
            if actor in used_actors:
                errors.append(prefix + f'duplicate actor mapping {actor}')
            used_actors.add(actor)
            item = {'actor_id': actor, 'track_id': tid, 'source': 'unknown', 'detection_index': None}
            audit['actors'].append(item)
            if tid is None:
                source = assignment.get('source', 'unknown')
                if source not in ('missing', 'unknown'):
                    errors.append(prefix + 'null track only permits missing/unknown')
                    source = 'unknown'
                item['source'] = source
                errors.append(prefix + f'actor {actor} has no detection ({source})')
                continue
            if not _id(tid):
                errors.append(prefix + 'invalid mapped track ID')
                continue
            if tid in used_tracks:
                errors.append(prefix + f'duplicate track mapping {tid}')
            used_tracks.add(tid)
            if tid not in tracks:
                errors.append(prefix + f'mapped track {tid} absent from detections')
                continue
            index, source = tracks[tid]
            item.update(source=source, detection_index=index)
            if 'source' in assignment and assignment['source'] != source:
                errors.append(prefix + 'mapping cannot override detection provenance')
            if source == 'missing':
                errors.append(prefix + f'track {tid} marked missing')
        for actor in expected:
            if actor not in used_actors:
                audit['actors'].append({'actor_id': actor, 'track_id': None,
                                        'source': 'missing', 'detection_index': None})
                errors.append(prefix + f'expected actor {actor} unmapped')
        audit['unmapped_detection_indices'] = [j for tid, (j, _) in tracks.items() if tid not in used_tracks]
        if audit['unmapped_detection_indices']:
            errors.append(prefix + 'unmapped detections')
    for key in indexed.keys() - seen:
        errors.append(f'{key}: mapping references absent frame')
    result['valid'] = not errors
    result['status'] = 'internal_candidate' if not errors else 'invalid_internal_candidate'
    return result


def _load(path, jsonl=False):
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f'nonfinite JSON number {value}')
        return number
    def reject(value):
        raise ValueError(f'nonfinite JSON value {value}')
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f'duplicate JSON key {key}')
            obj[key] = value
        return obj
    text = path.read_text(encoding='utf-8-sig')
    kwargs = {'parse_constant': reject, 'parse_float': finite_float, 'object_pairs_hook': unique}
    if jsonl:
        return {'frames': [json.loads(line, **kwargs) for line in text.splitlines() if line.strip()]}
    return json.loads(text, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--detections', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for input_path in (args.detections, args.mapping):
            if (args.output.resolve() == input_path.resolve() or
                    (args.output.exists() and input_path.exists() and args.output.samefile(input_path))):
                parser.error('output must not overwrite an input (including hard links)')
    except OSError as exc:
        parser.error(f'cannot establish input/output separation: {exc}')
    try:
        data = _load(args.detections, args.detections.suffix.lower() == '.jsonl')
        manual = _load(args.mapping)
        result = build_candidate(data, manual)
        result['input_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for name, path in [('detections', args.detections), ('mapping', args.mapping)]}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result = {'valid': False, 'delivery_ready': False, 'status': 'invalid_internal_candidate',
                  'errors': [str(exc)], 'frames': []}
    try:
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    except (OSError, ValueError) as exc:
        print(json.dumps({'valid': False, 'errors': [str(exc)]}))
        return 2
    print(json.dumps({'valid': result['valid'], 'output': str(args.output), 'errors': len(result['errors'])}))
    return 0 if result['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
