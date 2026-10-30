#!/usr/bin/env python3
"""Check a frozen skeleton-overlay contract, evidence and actual media clocks.

No Blender, pickle, report-provided commands, projection or compositing is run.
Only the explicitly supplied media Python executes fixed PyAV decoder code.
"""
import argparse
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys


CHAINS = {'torso', 'head', 'left_arm', 'right_arm', 'left_leg', 'right_leg'}
LIMITATION = ('Input/evidence integrity, declared review coverage and decoded media structure only. '
              'Cannot prove protocol sufficiency, evaluated-data provenance, correct actor/projection, '
              'real composite content or visual truth; actual viewing is still required.')

# This is fixed application code, never a command read from the protocol/report.
MEDIA_PROBE = r'''
import json, sys
from fractions import Fraction
import av

source, overlay = sys.argv[1:3]
start, count, numerator, denominator = map(int, sys.argv[3:7])
fps = Fraction(numerator, denominator)

def decode(path, is_source):
    times, sizes = [], []
    with av.open(path, mode='r') as container:
        if not container.streams.video:
            raise ValueError('Missing video stream')
        stream = container.streams.video[0]
        if stream.average_rate is None or Fraction(stream.average_rate) != fps:
            raise ValueError('Video fps does not match the frame_map')
        for index, frame in enumerate(container.decode(stream)):
            if is_source and index < start:
                continue
            if frame.pts is None or frame.time_base is None:
                raise ValueError('Missing decoded video PTS/time_base')
            timestamp = Fraction(frame.pts) * frame.time_base
            if times and timestamp <= times[-1]:
                raise ValueError('Decoded video PTS are not strictly monotonic')
            offset = timestamp - times[0] if is_source and times else timestamp
            if is_source and not times:
                offset = Fraction(0)
            if not is_source and not times and timestamp != 0:
                raise ValueError('Overlay first decoded PTS must be zero')
            if abs(offset - Fraction(len(times), 1) / fps) > Fraction(frame.time_base) / 2:
                raise ValueError('Decoded video PTS do not match job frame order/fps')
            times.append(timestamp)
            sizes.append((frame.width, frame.height))
            if is_source and len(times) == count:
                break
            if not is_source and len(times) > count:
                raise ValueError('Overlay has more frames than frame_count')
    if len(times) != count:
        raise ValueError('Decoded video frame count does not cover the job exactly')
    if len(set(sizes)) != 1:
        raise ValueError('Decoded video dimensions change within the job')
    return sizes[0]

try:
    source_size = decode(source, True)
    overlay_size = decode(overlay, False)
    if source_size != overlay_size:
        raise ValueError('Overlay/source dimensions differ; stacked video is not same-frame overlay')
except Exception as error:
    print(json.dumps({'errors': [str(error)]}))
    raise SystemExit(1)
print(json.dumps({'errors': [], 'frames': count, 'width': source_size[0], 'height': source_size[1]}))
'''


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(value, label):
    _require(isinstance(value, dict), label + ': object required')
    return value


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, 'Duplicate JSON key: ' + key)
        value[key] = item
    return value


def _invalid_number(value):
    raise ValueError('Non-finite/non-JSON number: ' + value)


def _read(path):
    value = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_unique,
                       parse_constant=_invalid_number)
    return _object(value, str(path))


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _strings(value, label):
    _require(isinstance(value, list) and bool(value), label + ': nonempty string list required')
    _require(all(isinstance(item, str) and bool(item.strip()) for item in value),
             label + ': each ID must be a nonempty string')
    _require(len(value) == len(set(value)), label + ': duplicate IDs')
    return set(value)


def _frames(value, first, last, label, *, empty=False):
    _require(isinstance(value, list) and (bool(value) or empty), label + ': frame list required')
    _require(all(type(frame) is int and first <= frame <= last for frame in value),
             label + ': frames must be integers inside the job')
    _require(value == sorted(set(value)), label + ': frames must be unique and ascending')
    return set(value)


def _frame_map(value, expected, label):
    value = _object(value, label)
    _require(set(value) == set(expected) and all(type(item) is int for item in value.values())
             and value == expected, label + ': frame_map differs from selected job parameters')


def _identity(value, actors, label):
    _require(_strings(value, label) == actors, label + ': actor IDs differ from required_actors')


def _schema(value, label):
    _require(type(value.get('schema_version')) is int and value['schema_version'] == 1,
             label + ': schema_version must be integer 1')


def _hash_binding(value, key, expected, label):
    actual = value.get(key)
    _require(isinstance(actual, str) and re.fullmatch('[0-9a-f]{64}', actual)
             and actual == expected, label + ': ' + key + ' does not match the frozen input')


def _evidence(value, base, label, *, kind=None):
    value = _object(value, label)
    if kind is not None:
        _require(value.get('kind') == kind, label + ': invalid evidence kind')
    name = value.get('path')
    _require(isinstance(name, str) and bool(name.strip()), label + ': evidence path is missing')
    path = Path(name)
    if not path.is_absolute():
        path = base / path
    _require(path.is_file(), label + ': evidence does not exist: ' + name)
    _hash_binding(value, 'sha256', _sha(path), label)
    return path


def _review(value, required, first, last, base, label, *, empty=False):
    value = _object(value, label)
    _require(value.get('executed') is True and value.get('status') == 'passed',
             label + ': review must be actually executed and passed')
    covered = _frames(value.get('source_frames'), first, last, label, empty=empty)
    _require(required.issubset(covered), label + ': required source frames are missing')
    evidence = value.get('evidence')
    _require(isinstance(evidence, list) and bool(evidence), label + ': evidence list is missing')
    for index, item in enumerate(evidence):
        _evidence(item, base, label + '.evidence[' + str(index) + ']')
    return covered


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _points(value, minimum, label, *, exact=False):
    _require(isinstance(value, list) and (len(value) == minimum if exact else len(value) >= minimum),
             label + ': insufficient/wrong number of projected points')
    _require(all(isinstance(point, list) and len(point) == 2
                 and all(_number(coordinate) for coordinate in point) for point in value),
             label + ': projected coordinates must be finite two-dimensional numbers, not bool')


def _projection(value, expected_map, actors, candidate_sha, source_sha, all_frames):
    _schema(value, 'projection')
    _require(value.get('kind') == 'evaluated_bones_and_visible_components', 'projection: invalid kind')
    _hash_binding(value, 'candidate_sha256', candidate_sha, 'projection')
    _hash_binding(value, 'source_video_sha256', source_sha, 'projection')
    _frame_map(value.get('frame_map'), expected_map, 'projection')
    _identity(value.get('actor_ids'), actors, 'projection.actor_ids')
    _require(value.get('from_evaluated_mesh') is True and value.get('from_evaluated_bones') is True,
             'projection: evaluated mesh and bones are both required')
    first, last = min(all_frames), max(all_frames)
    _require(_frames(value.get('source_frames'), first, last, 'projection') == all_frames,
             'projection: every job frame must be covered')
    components = _object(value.get('component_ids'), 'projection.component_ids')
    _require(set(components) == actors, 'projection.component_ids: actors differ')
    for actor in actors:
        _strings(components[actor], 'projection.component_ids.' + actor)
    frames = value.get('frames')
    _require(isinstance(frames, list) and len(frames) == expected_map['frame_count'],
             'projection.frames: actual projected data is required for every frame')
    for index, frame in enumerate(frames):
        frame = _object(frame, 'projection frame')
        _require(type(frame.get('source_frame')) is int and type(frame.get('blender_frame')) is int
                 and frame['source_frame'] == expected_map['source_start'] + index
                 and frame['blender_frame'] == expected_map['blender_start'] + index,
                 'projection.frames: source/Blender frame mapping differs')
        people = _object(frame.get('actors'), 'projection frame actors')
        _require(set(people) == actors, 'projection.frames: actor IDs differ')
        for actor in actors:
            person = _object(people[actor], 'projection frame actor')
            segments = _object(person.get('bone_segments'), 'projection bone_segments')
            _require(bool(segments) and all(isinstance(key, str) and bool(key.strip()) for key in segments),
                     'projection.bone_segments: nonempty bone IDs are required')
            for points in segments.values():
                _points(points, 2, 'projection bone segment', exact=True)
            points = _object(person.get('component_points'), 'projection component_points')
            _require(set(points) == set(components[actor]), 'projection.component_points: component IDs differ')
            for polygon in points.values():
                _points(polygon, 3, 'projection visible component')


def _validate(protocol_path, report_path, candidate, source_video, *, media_python,
              blender_start, source_start, frame_count, fps_num, fps_den, required_actors):
    expected_map = dict(blender_start=blender_start, source_start=source_start, frame_count=frame_count,
                        fps_num=fps_num, fps_den=fps_den)
    _require(all(type(number) is int for number in expected_map.values()), 'Job mapping requires integers, not bool')
    _require(blender_start >= 0 and source_start >= 0 and frame_count > 0 and fps_num > 0 and fps_den > 0,
             'Invalid job frame mapping')
    _require(not isinstance(required_actors, (str, bytes)), 'required_actors must be an ID list')
    actors = _strings(list(required_actors), 'required_actors')
    first, last = source_start, source_start + frame_count - 1
    all_frames = set(range(first, last + 1))
    protocol, report = _read(protocol_path), _read(report_path)
    candidate_sha, source_sha = _sha(candidate), _sha(source_video)
    base = report_path.parent
    for label, value in [('protocol', protocol), ('report', report)]:
        _schema(value, label)
        _frame_map(value.get('frame_map'), expected_map, label)
        _identity(value.get('actor_ids'), actors, label + '.actor_ids')
        _hash_binding(value, 'candidate_sha256', candidate_sha, label)
        _hash_binding(value, 'source_video_sha256', source_sha, label)
    _require(protocol.get('protocol_type') == 'skeleton_overlay_v1', 'A skeleton_overlay_v1 protocol is required')
    _require(report.get('report_type') == 'skeleton_overlay_report_v1', 'Invalid skeleton overlay report type')
    _hash_binding(report, 'protocol_sha256', _sha(protocol_path), 'report')
    _require(report.get('executed') is True and report.get('status') == 'passed',
             'Report must be actually executed and passed')
    reviewer = _object(report.get('reviewer'), 'reviewer')
    _require(isinstance(reviewer.get('name'), str) and bool(reviewer['name'].strip()), 'Reviewer name is missing')
    day = reviewer.get('date')
    _require(isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day), 'Reviewer ISO date is missing')
    date.fromisoformat(day)
    expected_people = _object(protocol.get('actors'), 'protocol.actors')
    reviewed_people = _object(report.get('actors'), 'report.actors')
    _require(set(expected_people) == actors and set(reviewed_people) == actors, 'Protocol/report actors differ')
    for actor in actors:
        expected = _object(_object(expected_people[actor], 'protocol actor').get('chains'), 'protocol chains')
        reviewed = _object(_object(reviewed_people[actor], 'report actor').get('chains'), 'report chains')
        _require(bool(expected) and set(expected).issubset(CHAINS), 'Each actor needs observable enumerated chains')
        _require(set(reviewed) == set(expected), actor + ': report chain IDs differ')
        for chain, spec in expected.items():
            label = actor + '.' + chain
            required = _frames(_object(spec, label).get('source_frames'), first, last, label)
            _review(reviewed[chain], required, first, last, base, label)
    continuity = _object(protocol.get('continuity'), 'protocol.continuity')
    _require(_frames(continuity.get('source_frames'), first, last, 'protocol.continuity') == all_frames,
             'Protocol continuity must cover the entire job')
    _review(report.get('continuity'), all_frames, first, last, base, 'report.continuity')
    cuts = _frames(protocol.get('cuts'), first + 1, last, 'protocol.cuts', empty=True)
    cut_neighbors = {frame for cut in cuts for frame in (cut - 1, cut)}
    cut_review = _review(report.get('cuts'), cut_neighbors, first, last, base, 'report.cuts', empty=not cuts)
    _require(bool(cuts) or not cut_review, 'No-cut review must use empty source_frames')
    overlay = _evidence(report.get('overlay'), base, 'overlay', kind='same_frame_overlay')
    _require(_sha(overlay) != source_sha, 'Overlay cannot be the unchanged source video')
    weights = _object(report['overlay'].get('blend_weights'), 'overlay.blend_weights')
    _require(set(weights) == {'source', 'skeleton'} and all(_number(x) and 0 < x < 1 for x in weights.values())
             and math.isclose(sum(weights.values()), 1, rel_tol=0, abs_tol=1e-9),
             'Overlay blend weights must contain positive source and skeleton contributions summing to 1')
    projection = _evidence(report.get('projection'), base, 'projection', kind='evaluated_bones_and_visible_components')
    _projection(_read(projection), expected_map, actors, candidate_sha, source_sha, all_frames)
    _evidence(report.get('source_pose'), base, 'source_pose', kind='source_pose_reference')
    command = [str(media_python), '-B', '-c', MEDIA_PROBE, str(source_video), str(overlay),
               str(source_start), str(frame_count), str(fps_num), str(fps_den)]
    process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                             timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    _require(process.returncode == 0, 'Media decode validation failed: ' + (process.stdout + process.stderr)[-2000:])
    probe = _object(json.loads(process.stdout, object_pairs_hook=_unique, parse_constant=_invalid_number), 'media probe')
    _require(probe.get('errors') == [], 'Media probe did not confirm the frozen job')


def validate(protocol_path, report_path, candidate, source_video, *, media_python,
             blender_start, source_start, frame_count, fps_num, fps_den, required_actors):
    """Return contract/evidence/media errors; passing never certifies visual truth."""
    try:
        _validate(Path(protocol_path), Path(report_path), Path(candidate), Path(source_video),
                  media_python=media_python, blender_start=blender_start, source_start=source_start,
                  frame_count=frame_count, fps_num=fps_num, fps_den=fps_den, required_actors=required_actors)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        return [str(error)]
    return []


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = _Parser(description=__doc__)
    for name in ('protocol', 'report', 'candidate', 'source-video', 'media-python'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('blender-start', 'source-start', 'frame-count', 'fps-num', 'fps-den'):
        parser.add_argument('--' + name, required=True, type=int)
    parser.add_argument('--required-actor', action='append', required=True)
    try:
        args = parser.parse_args(argv)
        errors = validate(args.protocol, args.report, args.candidate, args.source_video,
                          media_python=args.media_python, blender_start=args.blender_start,
                          source_start=args.source_start, frame_count=args.frame_count,
                          fps_num=args.fps_num, fps_den=args.fps_den, required_actors=args.required_actor)
    except (OSError, ValueError, TypeError) as error:
        errors = [str(error)]
    print(json.dumps({'status': 'incomplete' if errors else 'passed', 'errors': errors,
                      'limitation': LIMITATION}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
