"""Meaningful contracts for the reusable sequence preview worker."""
import importlib.util
from fractions import Fraction
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/motion/motion_preview.py'


class MotionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'The shipped sequence preview engine is missing')
        spec = importlib.util.spec_from_file_location('motion_preview_tests_module', SCRIPT)
        self.engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.engine)

    def test_registered_artifact_rejects_equal_length_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'candidate.blend'
            path.write_bytes(b'original')
            registered = self.engine.artifact(path)
            self.assertEqual(self.engine.validate_artifact(registered), path.resolve())
            path.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError, 'SHA|changed|mismatch'):
                self.engine.validate_artifact(registered)

    def test_missing_tracking_stays_unknown_without_invented_boxes(self):
        quality, entries = self.engine.tracking_for_frames(None, 3, Fraction(30))
        self.assertFalse(quality['available'])
        self.assertIsNone(quality['observed_count'])
        self.assertTrue(all(row['box_xyxy'] is None and row['observed_primary'] is None for row in entries))

    def test_filled_ranges_are_distinct_from_observed_and_resampled_by_time(self):
        import numpy as np
        cache = {'tracking': {'boxes_xyxy': np.tile([1, 2, 30, 40], (4, 1)),
                             'observed_primary': np.array([True, False, False, True]),
                             'track_ids': np.zeros(4, dtype=np.int32)}}
        quality, entries = self.engine.tracking_for_frames(cache, 8, Fraction(60))
        self.assertEqual(quality['filled_ranges_frame0'], [[2, 5]])
        self.assertEqual(quality['observed_count'], 4)
        self.assertEqual([row['tracking_frame0'] for row in entries], [0, 0, 1, 1, 2, 2, 3, 3])

    def test_fraction_clock_keeps_nonzero_start_without_second_motion_offset(self):
        request = {'source_fps': {'num': 60000, 'den': 1001},
                   'interval': {'start_seconds': 5.1, 'duration': 1}}
        fps, count, times = self.engine.source_clock(request)
        self.assertEqual(fps, Fraction(60000, 1001))
        self.assertEqual(count, 60)
        self.assertEqual(times[0], Fraction(51, 10))
        self.assertEqual(times[1] - times[0], Fraction(1001, 60000))

    def test_reference_panel_preserves_aspect_ratio_without_cropping(self):
        from PIL import Image
        self.assertTrue(callable(getattr(self.engine, 'fit_panel', None)), 'Reference preview needs aspect-preserving letterboxing')
        fitted = self.engine.fit_panel(Image.new('RGB', (100, 100), (255, 0, 0)))
        self.assertEqual(fitted.size, (960, 540))
        self.assertEqual(fitted.getpixel((0, 270)), (0, 0, 0))
        self.assertEqual(fitted.getpixel((210, 270)), (255, 0, 0))
        self.assertEqual(fitted.getpixel((749, 270)), (255, 0, 0))
        self.assertEqual(fitted.getpixel((750, 270)), (0, 0, 0))

    def test_blender_float32_rate_rounding_is_accepted_but_wrong_clock_rejected(self):
        self.assertTrue(callable(getattr(self.engine, 'validate_candidate_rate', None)), 'Candidate rate comparison must allow Blender fps_base float32 precision')
        self.engine.validate_candidate_rate(240, 1.0010000467300415, Fraction(240000, 1001))
        with self.assertRaisesRegex(ValueError, 'frame rate'):
            self.engine.validate_candidate_rate(60, 1, Fraction(60000, 1001))

    def test_audio_command_seeks_source_once_and_keeps_fractional_rate(self):
        cmd = self.engine.encoding_command('ffmpeg.exe', 'source.mp4', 'out.mp4',
                                           Fraction(60000, 1001), Fraction(51, 10), 1, 60)
        self.assertEqual(cmd.count('-ss'), 1)
        self.assertEqual(cmd[cmd.index('-ss') + 1], '5.1')
        self.assertEqual(cmd[cmd.index('-r') + 1], '60000/1001')
        self.assertIn('1:a:0?', cmd)

    def test_zero_start_does_not_seek_and_discard_aac_preroll(self):
        cmd = self.engine.encoding_command('ffmpeg.exe', 'source.mp4', 'out.mp4',
                                           Fraction(30), Fraction(0), 5, 150)
        self.assertNotIn('-ss', cmd, 'A zero input seek changes initial AAC decoder samples')

    def test_zero_start_preserves_first100ms_of_actual_aac_source(self):
        import av
        import numpy as np
        ffmpeg = Path(os.environ.get('FFMPEG_EXE', str(ROOT/'runtime/bin/ffmpeg.exe')))
        if not ffmpeg.is_file():
            self.skipTest('Set FFMPEG_EXE for the actual AAC boundary regression')

        def audio(path):
            samples = []
            with av.open(str(path)) as container:
                for frame in container.decode(audio=0):
                    samples.append(frame.to_ndarray().astype(float).mean(axis=0))
            return np.concatenate(samples)

        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'source.m4a', Path(tmp)/'comparison.mp4'
            fixture = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-n',
                                      '-f', 'lavfi', '-i', 'sine=frequency=997:sample_rate=44100:duration=1',
                                      '-c:a', 'aac', '-b:a', '192k', str(source)], capture_output=True)
            self.assertEqual(fixture.returncode, 0, fixture.stderr.decode(errors='replace'))
            cmd = self.engine.encoding_command(ffmpeg, source, target, Fraction(10), Fraction(0), .1, 1)
            encoded = subprocess.run(cmd, input=bytes([128])*(960*1080*3), capture_output=True)
            self.assertEqual(encoded.returncode, 0, encoded.stderr.decode(errors='replace'))
            reference, result = audio(source)[:4410], audio(target)[:4410]
            self.assertEqual(len(result), 4410)
            self.assertGreater(float(np.corrcoef(reference, result)[0, 1]), .99)

    def test_actual_video_decoder_selects_nonzero_original_pts(self):
        import av
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.mp4'
            with av.open(str(path), 'w') as container:
                stream = container.add_stream('mpeg4', rate=10)
                stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
                for index in range(10):
                    frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index * 20, dtype=np.uint8), format='rgb24')
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            samples = list(self.engine.video_samples(path, [Fraction(3, 10), Fraction(4, 10), Fraction(5, 10)], Fraction(1, 10)))
            self.assertEqual([round(time, 6) for image, time in samples], [.3, .4, .5])
            self.assertLess(abs(float(np.asarray(samples[0][0]).mean()) - 60), 6)

    def test_nonframe_aligned_start_matches_gem_first_available_frame(self):
        import av
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.mp4'
            with av.open(str(path), 'w') as container:
                stream = container.add_stream('mpeg4', rate=10)
                stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
                for index in range(10):
                    frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index * 20, dtype=np.uint8), format='rgb24')
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            times = [Fraction(35, 100) + Fraction(i, 10) for i in range(4)]
            samples = list(self.engine.video_samples(path, times, Fraction(1, 10)))
            self.assertEqual([round(time, 6) for image, time in samples], [.4, .5, .6, .7])
            self.assertLess(abs(float(np.asarray(samples[0][0]).mean()) - 80), 6)

    def test_restricted_cache_loader_rejects_executable_pickle_and_preserves_globals(self):
        import pickle
        import torch
        with tempfile.TemporaryDirectory() as tmp:
            path, marker = Path(tmp)/'cache.pt', Path(tmp)/'must-not-exist'

            class Payload:
                def __reduce__(self):
                    return eval, (f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')",)

            torch.save({'tracking': Payload()}, path)
            before = torch.serialization.get_safe_globals().copy()
            with self.assertRaises(pickle.UnpicklingError):
                self.engine.load_parameters(self.engine.artifact(path))
            self.assertFalse(marker.exists())
            self.assertEqual(set(torch.serialization.get_safe_globals()), set(before))

    def test_numeric_cache_tracking_loads_but_mutated_registration_is_rejected(self):
        import numpy as np
        import torch
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cache.pt'
            torch.save({'tracking': {'observed_primary': np.array([True, False]),
                                     'boxes_xyxy': np.ones((2, 4), dtype=np.float32),
                                     'track_ids': np.zeros(2, dtype=np.int32)}}, path)
            record = self.engine.artifact(path)
            self.assertEqual(self.engine.load_parameters(record)['tracking']['observed_primary'].tolist(), [True, False])
            path.write_bytes(b'bad' + path.read_bytes()[3:])
            with self.assertRaisesRegex(ValueError, 'SHA|changed|mismatch'):
                self.engine.load_parameters(record)


if __name__ == '__main__':
    unittest.main()
