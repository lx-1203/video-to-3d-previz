#!/usr/bin/env python3
"""Decode content, flag near-black regions, and export every final-second frame.

This is a review aid. Exit 0 means the scan and evidence export ran; it never
certifies visual quality. Inputs must share an unchanged, common-start timeline.
"""
import argparse
from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path
import sys

try:
    import av
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError as exc:
    print(json.dumps({'status': 'missing_dependency', 'message': str(exc),
                      'setup': 'python -m pip install av numpy Pillow'}), file=sys.stderr)
    sys.exit(2)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def artifact(path, root):
    return {'path': path.relative_to(root).as_posix(), 'sha256': digest(path)}


def split_regions(array, comparison):
    result = {'full': array}
    if comparison:
        if array.shape[0] % 2:
            raise ValueError('Comparison height must split into equal top and bottom panels')
        middle = array.shape[0] // 2
        result.update(top=array[:middle], bottom=array[middle:])
    return result


def scan(path, comparison, luma_threshold, dark_fraction):
    rows, previous, previous_time = [], {}, None
    with av.open(str(path)) as container:
        if len(container.streams.video) != 1:
            raise ValueError(str(path) + ': exactly one video stream is required')
        stream = container.streams.video[0]
        for index, frame in enumerate(container.decode(stream)):
            if frame.pts is None or frame.time_base is None:
                raise ValueError(str(path) + ': decoded frame has no PTS/time base')
            absolute_time = float(frame.pts * frame.time_base)
            if previous_time is not None and absolute_time <= previous_time:
                raise ValueError(str(path) + ': nonincreasing decoded PTS')
            if not rows:
                first_time = absolute_time
                dimensions = [frame.width, frame.height]
            if dimensions != [frame.width, frame.height]:
                raise ValueError(str(path) + ': frame dimensions changed during decoding')
            duration = float(frame.duration * frame.time_base) if frame.duration else None
            if duration is not None and (not math.isfinite(duration) or duration <= 0):
                duration = None
            gray = frame.to_ndarray(format='gray')
            regions = {}
            for name, array in split_regions(gray, comparison).items():
                pixel_hash = hashlib.sha256(array.tobytes()).hexdigest()
                fraction = float(np.mean(array <= luma_threshold))
                regions[name] = {'mean_luma': float(np.mean(array)), 'dark_fraction': fraction,
                                 'near_black_suspect': fraction >= dark_fraction,
                                 'decoded_luma_sha256': pixel_hash,
                                 'repeat_of_previous': previous.get(name) == pixel_hash}
                previous[name] = pixel_hash
            rows.append({'decoded_frame': index, 'pts': frame.pts,
                         'time_base': str(frame.time_base), 'absolute_seconds': absolute_time,
                         'timeline_seconds': absolute_time - first_time,
                         'decoded_duration_seconds': duration,
                         'duration_seconds': duration,
                         'duration_basis': 'decoded_frame' if duration is not None else 'unknown',
                         'regions': regions})
            previous_time = absolute_time
    if not rows:
        raise ValueError(str(path) + ': no decoded video frames')
    # Packet duration can stay nominal in VFR containers. The next PTS marks
    # when a displayed frame is replaced, including a long held frame.
    for row, following in zip(rows, rows[1:]):
        row['duration_seconds'] = following['timeline_seconds'] - row['timeline_seconds']
        row['duration_basis'] = 'next_pts'
    last = rows[-1]
    timing_known = last['duration_seconds'] is not None
    end = last['timeline_seconds'] + last['duration_seconds'] if timing_known else None
    # An unknown final duration must not shift this boundary forward by an
    # invented nominal frame length. Export a conservative superset instead.
    start = max(0.0, (end if timing_known else last['timeline_seconds']) - 1.0)
    # A VFR frame can begin before the boundary but remain visible within it.
    tail = [row['decoded_frame'] for row in rows
            if row['duration_seconds'] is None
            or row['timeline_seconds'] + row['duration_seconds'] > start + 1e-8]
    return {'path': str(path.resolve()), 'dimensions': dimensions, 'decoded_frames': len(rows),
            'first_absolute_seconds': first_time, 'duration_seconds': end,
            'tail_timing_status': 'known' if timing_known else 'uncertain_final_duration',
            'tail_start_seconds': start, 'tail_frames': tail, 'frames': rows}


def align_reference(clip, reference):
    """Pair only sufficiently near PTS; do not silently shift or retime media."""
    times = [row['timeline_seconds'] for row in reference['frames']]
    for row in clip['frames']:
        pos = bisect_left(times, row['timeline_seconds'])
        candidates = [i for i in (pos - 1, pos) if 0 <= i < len(times)]
        nearest = min(candidates, key=lambda i: abs(times[i] - row['timeline_seconds']))
        original = reference['frames'][nearest]
        delta = row['timeline_seconds'] - times[nearest]
        # Unknown duration permits only the same PTS, never a guessed window.
        tolerance = min(row['duration_seconds'] or 0, original['duration_seconds'] or 0) / 2 + 1e-8
        matched = abs(delta) <= tolerance
        row.update(reference_frame=nearest if matched else None,
                   reference_time_delta_seconds=delta,
                   reference_near_black=(original['regions']['full']['near_black_suspect']
                                         if matched else None))


def near_black_items(videos):
    items = []
    for name, clip in videos.items():
        for region in clip['frames'][0]['regions']:
            current = None
            for row in clip['frames']:
                if not row['regions'][region]['near_black_suspect']:
                    current = None
                    continue
                if name == 'reference':
                    context = 'reference_observation'
                elif row['reference_frame'] is None:
                    context = 'reference_unmatched'
                elif row['reference_near_black']:
                    context = 'reference_also_near_black'
                else:
                    context = 'candidate_only_near_black'
                if current is None or current['context'] != context:
                    current = {'video': name, 'region': region, 'context': context,
                               'decoded_frames': [], 'source_frames': [],
                               'timeline_start_seconds': row['timeline_seconds'],
                               'requires_human_review': True}
                    items.append(current)
                current['decoded_frames'].append(row['decoded_frame'])
                source_frame = row['decoded_frame'] if name == 'reference' else row['reference_frame']
                current['source_frames'].append(source_frame)
                current['timeline_end_seconds'] = (row['timeline_seconds'] + row['duration_seconds']
                                                   if row['duration_seconds'] is not None else None)
    return items


def export_tail(name, clip, out, comparison):
    """Seek backward, then match original PTS; no frame-count approximation."""
    selected = {clip['frames'][i]['pts']: clip['frames'][i] for i in clip['tail_frames']}
    tiles = {region: [] for region in clip['frames'][0]['regions']}
    with av.open(clip['path']) as container:
        stream = container.streams.video[0]
        first = clip['frames'][clip['tail_frames'][0]]
        seek_pts = math.floor(first['absolute_seconds'] / float(stream.time_base))
        container.seek(seek_pts, stream=stream, backward=True, any_frame=False)
        for frame in container.decode(stream):
            row = selected.pop(frame.pts, None)
            if row is None:
                continue
            row['tail_images'] = {}
            rgb = frame.to_ndarray(format='rgb24')
            for region, pixels in split_regions(rgb, comparison).items():
                image = Image.fromarray(pixels)
                directory = out / name / region
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / ('frame_%06d_pts_%s.jpg' % (row['decoded_frame'], row['pts']))
                image.save(path, quality=95, subsampling=0)
                row['tail_images'][region] = artifact(path, out)
                preview = image.copy()
                preview.thumbnail((320, 180))
                tile = Image.new('RGB', (320, 210), 'white')
                tile.paste(preview, ((320 - preview.width) // 2, 0))
                ImageDraw.Draw(tile).text((4, 183), 'f%d pts=%s t=%.6f' %
                                         (row['decoded_frame'], row['pts'], row['timeline_seconds']),
                                         fill='black')
                tiles[region].append(tile)
    if selected:
        raise ValueError(name + ': tail seek/export missed PTS ' + str(sorted(selected)))
    sheets = {}
    for region, images in tiles.items():
        sheet = Image.new('RGB', (320 * min(5, len(images)), 210 * math.ceil(len(images) / 5)), '#bbbbbb')
        for i, tile in enumerate(images):
            sheet.paste(tile, ((i % 5) * 320, (i // 5) * 210))
        path = out / name / ('tail_' + region + '_contact.jpg')
        sheet.save(path, quality=95, subsampling=0)
        sheets[region] = artifact(path, out)
    clip.update(contact_sheets=sheets,
                tail_extraction_status=('complete' if clip['tail_timing_status'] == 'known'
                                        else 'conservative_unverified'))


def audit(reference, white, comparison, out, luma_threshold=10, dark_fraction=.98):
    paths = {'reference': reference, 'white': white, 'comparison': comparison}
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing a nonempty output directory; use a new audit directory')
    if not 0 <= luma_threshold <= 255 or not 0 < dark_fraction <= 1:
        raise ValueError('Invalid near-black suspicion thresholds')
    fingerprints = {name: digest(path) for name, path in paths.items()}
    out.mkdir(parents=True, exist_ok=True)
    videos = {}
    for name, path in paths.items():
        videos[name] = scan(path, name == 'comparison', luma_threshold, dark_fraction)
        videos[name]['sha256'] = fingerprints[name]
    for name, clip in videos.items():
        if name != 'reference':
            align_reference(clip, videos['reference'])
        export_tail(name, clip, out, name == 'comparison')
    for name, path in paths.items():
        if digest(path) != fingerprints[name]:
            raise ValueError(name + ': input changed during audit; discard this evidence')
    items = near_black_items(videos)
    unmatched = {name: [row['decoded_frame'] for row in clip['frames']
                        if row.get('reference_frame') is None]
                 for name, clip in videos.items() if name != 'reference'}
    timing_items = [{'video': name, 'decoded_frame': clip['frames'][-1]['decoded_frame'],
                     'reason': 'Unknown final display duration; tail coverage expanded from last PTS minus one second.',
                     'requires_human_review': True}
                    for name, clip in videos.items() if clip['tail_timing_status'] != 'known']
    report = {'schema_version': 1,
              'scan_status': 'review_required' if items or any(unmatched.values()) or timing_items else 'content_scan_complete',
              'visual_acceptance': 'unverified',
              'scope': 'Full video decode/luma scan; tail frames exported at decoded resolution, with conservative expansion and explicit uncertainty when final duration is unknown.',
              'tool_sha256': digest(Path(__file__)),
              'timeline_assumption': 'Common start and unchanged timing; no inferred edit, shift or speed mapping.',
              'thresholds': {'gray_pixel_max': luma_threshold, 'dark_fraction_min': dark_fraction},
              'videos': videos, 'near_black_review_items': items, 'reference_unmatched_frames': unmatched,
              'timing_review_items': timing_items,
              'limitations': [
                  'Near-black thresholds nominate review items; darkness can be intentional or normal source content.',
                  'Exact repeated luma is recorded only; static shots and identical source frames are not failed.',
                  'These metrics do not validate action, contact, flicker, silhouettes, lighting, audio, or provenance.',
                  'Full-resolution tail JPEGs are high-quality decoded previews; contact sheets are thumbnails.',
                  'Exit 0 confirms completed operation, never a visual pass. Human inspection remains unverified.'
              ]}
    (out / 'content_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                            encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference', 'white', 'comparison', 'out'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--luma-threshold', type=int, default=10)
    parser.add_argument('--dark-fraction', type=float, default=.98)
    args = parser.parse_args()
    try:
        report = audit(args.reference, args.white, args.comparison, args.out,
                       args.luma_threshold, args.dark_fraction)
    except Exception as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({'status': report['scan_status'], 'visual_acceptance': 'unverified',
                      'near_black_review_groups': len(report['near_black_review_items']),
                      'report': str((args.out / 'content_report.json').resolve())}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
