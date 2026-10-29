#!/usr/bin/env python3
"""Validate a frozen review ledger, not the visual truth of its evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_object(path):
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError(str(path) + ': JSON object required')
    return value


def frame_set(value):
    if (not isinstance(value, list) or not value
            or any(type(frame) is not int or frame < 0 for frame in value)):
        raise ValueError('source_frames must be a nonempty list of zero-based integer frames')
    return set(value)


def validate(protocol_path, report_path, candidate, source_video):
    protocol = read_object(protocol_path)
    report = read_object(report_path)
    errors = []
    for key, actual in [('protocol_sha256', sha256(protocol_path)),
                        ('candidate_sha256', sha256(candidate)),
                        ('source_video_sha256', sha256(source_video))]:
        if report.get(key) != actual:
            errors.append(key + ': report does not match the selected input')
    if protocol.get('source_video_sha256') != report.get('source_video_sha256'):
        errors.append('Protocol and report refer to different source videos')
    expected, observed = protocol.get('checks'), report.get('checks')
    if not isinstance(expected, dict) or not expected:
        raise ValueError('Protocol must declare at least one required check')
    if not isinstance(observed, dict):
        raise ValueError('Report checks must be an object')
    if set(expected) != set(observed):
        errors.append('Check IDs differ: missing=' + str(sorted(set(expected) - set(observed)))
                      + ', unexpected=' + str(sorted(set(observed) - set(expected))))
    for check_id, spec in expected.items():
        if not check_id or not isinstance(spec, dict):
            raise ValueError('Each protocol check needs an ID and object')
        required_frames = frame_set(spec.get('source_frames'))
        result = observed.get(check_id)
        if not isinstance(result, dict):
            errors.append(check_id + ': a check result object is required')
            continue
        if result.get('executed') is not True or result.get('status') != 'passed':
            errors.append(check_id + ': must actually be executed and passed')
        if not required_frames.issubset(frame_set(result.get('source_frames'))):
            errors.append(check_id + ': required source frames were not all reviewed')
        evidence = result.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            errors.append(check_id + ': evidence is missing')
            continue
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not item['path']:
                raise ValueError(check_id + ': evidence needs a nonempty path')
            path = Path(item['path'])
            if not path.is_absolute():
                path = report_path.parent / path
            if not path.is_file() or item.get('sha256') != sha256(path):
                errors.append(check_id + ': missing or changed evidence: ' + item['path'])
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['protocol', 'report', 'candidate', 'source-video']:
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        errors = validate(args.protocol, args.report, args.candidate, args.source_video)
    except (OSError, ValueError, TypeError) as exc:
        errors = [str(exc)]
    print(json.dumps({'status': 'incomplete' if errors else 'coverage_complete',
                      'errors': errors,
                      'limitation': 'Ledger integrity only; does not inspect meshes, images or video.'},
                     ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
