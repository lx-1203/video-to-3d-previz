"""Real encoded media regressions, plus explicit missing-duration fault injection."""
import hashlib
import json
import runpy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from fractions import Fraction

import av
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/content_audit.py'


def video(path, values, bottom=None, fps=30, points=None):
    """Write 64px wide MPEG-4, including real black and split-panel cases."""
    height = 72 if bottom is not None else 36
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('mpeg4', rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, height, 'yuv420p'
        stream.bit_rate = 2000000
        for index, value in enumerate(values):
            image = np.full((height, 64, 3), value, dtype=np.uint8)
            if bottom is not None:
                image[height // 2:] = bottom[index]
            frame = av.VideoFrame.from_ndarray(image, format='rgb24')
            if points is not None:
                frame.time_base = Fraction(1, fps)
                frame.pts = points[index]
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class ContentAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_case(self, reference, white, top=None, bottom=None, fps=30, points=None, extension='mp4'):
        self.assertTrue(SCRIPT.is_file(), 'missing per-panel tail-content audit tool')
        source = self.directory / ('reference.' + extension)
        rendered = self.directory / ('white.' + extension)
        comparison = self.directory / ('comparison.' + extension)
        video(source, reference, fps=fps, points=points)
        video(rendered, white, fps=fps, points=points)
        video(comparison, reference if top is None else top,
              bottom=white if bottom is None else bottom, fps=fps, points=points)
        out = self.directory / 'audit'
        result = subprocess.run([sys.executable, str(SCRIPT), '--reference', str(source),
                                 '--white', str(rendered), '--comparison', str(comparison),
                                 '--out', str(out)], capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((out / 'content_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['visual_acceptance'], 'unverified')
        return report, out

    def test_healthy_and_static_video_gets_complete_tail_without_bad_frame_claim(self):
        report, out = self.run_case([120] * 60, [180] * 60)
        self.assertEqual(report['near_black_review_items'], [])
        self.assertEqual(report['scan_status'], 'content_scan_complete')
        for name in ['reference', 'white', 'comparison']:
            clip = report['videos'][name]
            self.assertEqual(clip['decoded_frames'], 60)
            self.assertEqual(clip['tail_frames'], list(range(30, 60)))
            self.assertEqual(clip['tail_extraction_status'], 'complete')
            self.assertTrue(any(row['regions']['full']['repeat_of_previous']
                                for row in clip['frames'][1:]))
            for sheet in clip['contact_sheets'].values():
                self.assertTrue((out / sheet['path']).is_file())
                self.assertEqual(hashlib.sha256((out / sheet['path']).read_bytes()).hexdigest(),
                                 sheet['sha256'])

    def test_tail_black_point_four_seconds_is_found_despite_normal_decoding(self):
        white = [180] * 48 + [0] * 12
        report, _ = self.run_case([120] * 60, white)
        items = [x for x in report['near_black_review_items']
                 if x['video'] == 'white' and x['region'] == 'full']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['source_frames'], list(range(48, 60)))
        self.assertEqual(items[0]['context'], 'candidate_only_near_black')
        self.assertEqual(report['scan_status'], 'review_required')

    def test_single_middle_black_frame_outside_sparse_samples_is_found(self):
        # Synthetic one-based frame 487: outside the final second and a
        # 73-frame uniform sample. Full decoding alone does not flag its content.
        count, defect = 600, 486
        sparse = {round(i * (count - 1) / 72) for i in range(73)}
        self.assertNotIn(defect, sparse)
        white = [180] * count
        white[defect] = 0
        report, _ = self.run_case([120] * count, white)
        self.assertEqual(report['scan_status'], 'review_required')
        for name, region in [('white', 'full'), ('comparison', 'bottom')]:
            clip = report['videos'][name]
            self.assertEqual(clip['decoded_frames'], count)
            self.assertEqual(len(clip['frames']), count)
            self.assertNotIn(defect, clip['tail_frames'])
            self.assertEqual(clip['frames'][defect]['reference_frame'], defect)
            self.assertFalse(clip['frames'][defect]['reference_near_black'])
            items = [x for x in report['near_black_review_items']
                     if x['video'] == name and x['region'] == region]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['decoded_frames'], [defect])
            self.assertEqual(items[0]['source_frames'], [defect])
            self.assertEqual(items[0]['context'], 'candidate_only_near_black')
        self.assertFalse(any(x['video'] == 'comparison' and x['region'] == 'top'
                             for x in report['near_black_review_items']))

    def test_bottom_black_is_found_when_bright_top_hides_whole_frame_blackness(self):
        bottom = [180] * 48 + [0] * 12
        report, _ = self.run_case([120] * 60, [180] * 60, bottom=bottom)
        items = report['near_black_review_items']
        self.assertTrue(any(x['video'] == 'comparison' and x['region'] == 'bottom'
                            and x['source_frames'] == list(range(48, 60)) for x in items))
        self.assertFalse(any(x['video'] == 'comparison' and x['region'] == 'full' for x in items))

    def test_reference_darkness_stays_review_context_not_automatic_failure_or_pass(self):
        dark_tail = [120] * 48 + [0] * 12
        report, _ = self.run_case(dark_tail, dark_tail)
        items = [x for x in report['near_black_review_items'] if x['video'] == 'white']
        self.assertEqual(items[0]['context'], 'reference_also_near_black')
        self.assertNotIn('passed', report)
        self.assertNotIn('failed', report)

    def test_frames_past_reference_end_stay_unmatched(self):
        report, _ = self.run_case([120] * 60, [180] * 66, top=[120] * 66)
        self.assertEqual(report['scan_status'], 'review_required')
        for name in ['white', 'comparison']:
            self.assertEqual(report['reference_unmatched_frames'][name], list(range(60, 66)))
            self.assertEqual(report['videos'][name]['frames'][59]['reference_frame'], 59)
            self.assertIsNone(report['videos'][name]['frames'][60]['reference_frame'])

    def test_missing_dependencies_give_actionable_nonzero_status(self):
        # -S hides site-packages, without changing the user's environment.
        result = subprocess.run([sys.executable, '-S', str(SCRIPT), '--help'],
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        status = json.loads(result.stderr)
        self.assertEqual(status['status'], 'missing_dependency')
        self.assertIn('pip install av numpy Pillow', status['setup'])

    def test_tail_uses_timestamps_for_twenty_four_fps_and_keeps_pts(self):
        report, out = self.run_case([120] * 36, [180] * 36, fps=24)
        for clip in report['videos'].values():
            self.assertEqual(clip['tail_frames'], list(range(12, 36)))
            tail = [clip['frames'][i] for i in clip['tail_frames']]
            self.assertTrue(all(x['pts'] is not None for x in tail))
            self.assertAlmostEqual(tail[0]['timeline_seconds'], .5, places=6)
            self.assertTrue(all((out / x['tail_images']['full']['path']).is_file() for x in tail))

    def test_vfr_frame_spanning_tail_boundary_is_included(self):
        # f3 displays from 0.8 to 1.2 seconds, across the last-second start.
        report, _ = self.run_case([120] * 7, [180] * 7,
                                  points=[0, 3, 12, 24, 36, 54, 60])
        for clip in report['videos'].values():
            self.assertEqual(clip['tail_frames'][0], 3)

    def test_vfr_mkv_nominal_packet_duration_does_not_hide_spanning_frame(self):
        # Matroska reports 33ms packet durations although f3 is displayed
        # from 0.8 until the next presentation timestamp at 1.2 seconds.
        report, out = self.run_case([120] * 7, [180] * 7,
                                   points=[0, 3, 12, 24, 36, 54, 60], extension='mkv')
        for clip in report['videos'].values():
            self.assertEqual(clip['tail_frames'], [3, 4, 5, 6])
            self.assertAlmostEqual(clip['frames'][3]['duration_seconds'], .4)
            self.assertTrue((out / clip['frames'][3]['tail_images']['full']['path']).is_file())

    def test_missing_final_duration_expands_tail_and_requires_review(self):
        paths = [self.directory / (name + '.mp4') for name in ['reference', 'white', 'comparison']]
        # f3 ends at 1.01s: a nominal 30fps final duration would wrongly
        # exclude it, while an unknown final duration must retain it.
        for i, path in enumerate(paths):
            video(path, [120] * 7, bottom=[180] * 7 if i == 2 else None,
                  fps=100, points=[0, 10, 40, 80, 101, 180, 200])
        module = runpy.run_path(str(SCRIPT))
        real_open = av.open

        class MissingDuration:
            def __init__(self, *args, **kwargs):
                self.container = real_open(*args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.container.close()

            def __getattr__(self, name):
                return getattr(self.container, name)

            def decode(self, *args, **kwargs):
                for frame in self.container.decode(*args, **kwargs):
                    frame.duration = 0
                    yield frame

        with patch.object(av, 'open', side_effect=MissingDuration):
            report = module['audit'](*paths, self.directory / 'uncertain-audit')
        self.assertEqual(report['scan_status'], 'review_required')
        self.assertEqual(report['visual_acceptance'], 'unverified')
        self.assertEqual(len(report['timing_review_items']), 3)
        for clip in report['videos'].values():
            self.assertEqual(clip['tail_frames'], [3, 4, 5, 6])
            self.assertEqual(clip['tail_timing_status'], 'uncertain_final_duration')
            self.assertEqual(clip['tail_extraction_status'], 'conservative_unverified')
            self.assertIsNone(clip['duration_seconds'])
            self.assertAlmostEqual(clip['tail_start_seconds'], 1.0)

    def test_short_clip_has_all_frames_and_cli_rejects_empty_or_reused_output(self):
        report, out = self.run_case([120] * 9, [180] * 9)
        self.assertEqual(report['videos']['white']['tail_frames'], list(range(9)))
        result = subprocess.run([sys.executable, str(SCRIPT), '--reference', str(out.parent / 'reference.mp4'),
                                 '--white', str(out.parent / 'white.mp4'), '--comparison',
                                 str(out.parent / 'comparison.mp4'), '--out', str(out)],
                                capture_output=True, encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nonempty', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
