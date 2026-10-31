"""Expand explicit identity ranges, never recognize people or infer a new track.

--detections frames.jsonl --anchors hypotheses.json --output proposal.json
Contract: {actor_ids: [...], anchors: [{shot_id, track_id, actor_id,
start_frame, end_frame, confirmation: confirmed|hypothesis, evidence: ...}]}.
Ranges are inclusive source-zero-based frames, isolated by shot. A point anchor
uses equal endpoints; it does not implicitly extend to a shot boundary.
Optional required_actor_ids on each tracking frame overrides the global roster;
source_visibility is copied independently and defaults to unknown per actor.
Output mapping is a COMPLETE internal audit input for multi_actor (unresolved
tracks/missing actors intentionally fail that audit). crop_candidate contains
ONLY unconflicted confirmed identities with observed usable boxes. It is a
subset for Pose, never full-film/identity/visible-joint acceptance. No ML imports.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path


def _multi_actor():
    spec = importlib.util.spec_from_file_location('_identity_multi_actor', Path(__file__).with_name('multi_actor.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _roster(value):
    if (not isinstance(value, list) or any(not isinstance(a, str) or not a.strip() for a in value)
            or len(set(value)) != len(value)):
        raise ValueError('roster must be a unique list of nonempty actor IDs')
    return value


def _usable(d, trial):
    if d.get('source', 'observed' if trial else 'unknown') != 'observed' or d.get('observed') is False:
        return False
    square = 'square_bbox_xys' in d or 'bbox' in d
    box = d.get('square_bbox_xys', d.get('bbox')) if square else d.get('raw_box_xyxy', d.get('box_xyxy', d.get('box')))
    if (not isinstance(box, list) or len(box) != (3 if square else 4)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)):
        return False
    score = d.get('detection_score', d.get('score', d.get('raw_score')))
    if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
        return False
    return box[2] > 0 if square else box[2] > box[0] and box[3] > box[1]


def build_proposal(detections, hypotheses):
    """Preserve all raw input; expand only supplied ranges over supplied frames."""
    ma = _multi_actor()
    actors = _roster(hypotheses.get('actor_ids'))
    anchors = hypotheses.get('anchors')
    if not isinstance(anchors, list):
        raise ValueError('anchors must be a list')
    for a in anchors:
        if not isinstance(a, dict):
            raise ValueError('anchor must be an object')
        if (not isinstance(a.get('shot_id'), str) or not a['shot_id'].strip()
                or not ma._id(a.get('track_id')) or a.get('actor_id') not in actors
                or a.get('confirmation') not in ('confirmed', 'hypothesis')
                or not isinstance(a.get('evidence'), (str, dict, list)) or not a['evidence']
                or type(a.get('start_frame')) is not int or type(a.get('end_frame')) is not int
                or not 0 <= a['start_frame'] <= a['end_frame']):
            raise ValueError('invalid anchor identity/range/confirmation/evidence')
    rows = detections.get('frames') if isinstance(detections, dict) else detections
    if not isinstance(rows, list) or not rows:
        raise ValueError('tracking requires a nonempty frames list')
    result = dict(schema_version=1, status='internal_identity_proposal', delivery_ready=False,
                  all_actors_covered=False, full_video_pass=False, scope='supplied_frames_subset_only',
                  automatic_identity_recognition=False, input=copy.deepcopy(detections),
                  hypotheses_input=copy.deepcopy(hypotheses), frames=[], unresolved_detections=[],
                  excluded_crops=[], conflicts=[])
    mapping = dict(actor_ids=copy.deepcopy(actors), frames=[])
    crop_rows, crop_maps, seen = [], [], set()
    for row in rows:
        key = ma._key(row)
        if key in seen:
            raise ValueError('duplicate tracking frame')
        seen.add(key)
        trial = 'observed_tracks' in row
        field = 'observed_tracks' if trial else 'detections'
        if trial and 'detections' in row:
            raise ValueError('ambiguous detection schema')
        entries = row.get(field)
        if not isinstance(entries, list):
            raise ValueError('missing detection list')
        roster = _roster(row.get('required_actor_ids', actors))
        if any(a not in actors for a in roster):
            raise ValueError('required actor not in actor_ids')
        active = [a for a in anchors if a['shot_id'] == key[0] and a['start_frame'] <= key[1] <= a['end_frame']]
        # Conflicts include claims for absent tracks: absence cannot resolve ambiguity.
        bad_tracks = {a['track_id'] for a in active if
                      len({b['actor_id'] for b in active if b['track_id'] == a['track_id']}) > 1 or
                      len({b['track_id'] for b in active if b['actor_id'] == a['actor_id']}) > 1}
        identities, assignments, selected, selected_maps = [], [], [], []
        track_ids = set()
        for index, d in enumerate(entries):
            if not isinstance(d, dict):
                raise ValueError('detection must be an object')
            tid = d.get('window_local_track_id' if trial else 'track_id')
            if not ma._id(tid) or tid in track_ids:
                raise ValueError('invalid or duplicate detection track')
            track_ids.add(tid)
            claims = [a for a in active if a['track_id'] == tid]
            status, actor = 'unknown', None
            if claims and tid not in bad_tracks:
                actor = claims[0]['actor_id']
                status = 'confirmed' if any(a['confirmation'] == 'confirmed' for a in claims) else 'hypothesis'
            item = dict(detection_index=index, track_id=tid, actor_id=actor,
                        identity_status=status, evidence=copy.deepcopy(claims))
            identities.append(item)
            if status == 'unknown' or status == 'hypothesis':
                result['unresolved_detections'].append(dict(shot_id=key[0], frame=key[1], **copy.deepcopy(item)))
            if tid in bad_tracks:
                result['conflicts'].append(dict(shot_id=key[0], frame=key[1], **copy.deepcopy(item)))
            if actor in roster:
                assignments.append(dict(actor_id=actor, track_id=tid, identity_status=status,
                                        evidence=copy.deepcopy(claims)))
            if status == 'confirmed' and actor in roster and _usable(d, trial):
                selected.append(copy.deepcopy(d))
                selected_maps.append(dict(actor_id=actor, track_id=tid,
                                          identity_status='confirmed', evidence=copy.deepcopy(claims)))
            else:
                result['excluded_crops'].append(dict(shot_id=key[0], frame=key[1],
                    reason=('identity_not_confirmed' if status != 'confirmed' else
                            'outside_required_roster' if actor not in roster else 'no_usable_observed_box'),
                    **copy.deepcopy(item)))
        present = {i['actor_id'] for i in identities if i['actor_id'] in roster}
        missing = [a for a in roster if a not in present]
        assignments.extend(dict(actor_id=a, track_id=None, source='missing', identity_status='unknown') for a in missing)
        mapping['frames'].append(dict(shot_id=key[0], frame=key[1], expected_actor_ids=copy.deepcopy(roster), mappings=assignments))
        result['frames'].append(dict(shot_id=key[0], frame=key[1], required_actor_ids=copy.deepcopy(roster),
                                     source_visibility=copy.deepcopy(row.get('source_visibility', {a: 'unknown' for a in roster})),
                                     identities=identities, missing_actor_ids=missing))
        if selected:
            cropped = copy.deepcopy(row)
            cropped[field] = selected
            cropped['identity_proposal_scope'] = 'confirmed_observed_crop_subset'
            cropped['original_detection_indices'] = [i['detection_index'] for i in identities
                if any(m['track_id'] == i['track_id'] for m in selected_maps)]
            crop_rows.append(cropped)
            crop_maps.append(dict(shot_id=key[0], frame=key[1], expected_actor_ids=[m['actor_id'] for m in selected_maps], mappings=selected_maps))
    result['mapping'] = mapping
    result['audit_candidate'] = ma.build_candidate(detections, mapping)
    # The legacy audit checks association structure, not identity confirmation.
    result['audit_candidate'].update(identity_verified=False, full_video_pass=False)
    for audit, proposal in zip(result['audit_candidate']['frames'], result['frames']):
        by_track = {i['track_id']: i for i in proposal['identities']}
        for actor in audit['actors']:
            identity = by_track.get(actor['track_id'], {})
            actor['identity_status'] = identity.get('identity_status', 'unknown')
            actor['evidence'] = copy.deepcopy(identity.get('evidence', []))
    candidate = ma.build_candidate({'frames': crop_rows}, dict(actor_ids=actors, frames=crop_maps))
    # The legacy builder drops extra mapping fields; restore only metadata from
    # the confirmed selections, never infer confirmation from legacy actors.
    for cropped, mapped in zip(candidate['frames'], crop_maps):
        by_identity = {(m['actor_id'], m['track_id']): m for m in mapped['mappings']}
        for actor in cropped['actors']:
            selected = by_identity[(actor['actor_id'], actor['track_id'])]
            actor['identity_status'] = selected['identity_status']
            actor['evidence'] = copy.deepcopy(selected['evidence'])
    candidate.update(scope='confirmed_observed_crop_subset', all_actors_covered=False, full_video_pass=False)
    result['crop_candidate'] = candidate
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--detections', type=Path, required=True)
    parser.add_argument('--anchors', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    for path in (args.detections, args.anchors):
        if args.output.resolve() == path.resolve() or (args.output.exists() and path.exists() and args.output.samefile(path)):
            parser.error('output must not overwrite inputs')
    ma = _multi_actor()
    try:
        result = build_proposal(ma._load(args.detections, args.detections.suffix.lower() == '.jsonl'), ma._load(args.anchors))
        result['input_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for name, path in [('detections', args.detections), ('anchors', args.anchors)]}
        # Exclusive creation also protects previous evidence outputs.
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 2
    print(json.dumps({'status': result['status'], 'output': str(args.output), 'delivery_ready': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
