"""Standard-library tests; no video/model inference and no numerical imports."""
import importlib.util
import io
import json
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/motion/multi_actor_detect.py'


class FakeFrame:
    def __init__(self, n):
        self.pts = [13, 31, 62, 100, 151][n]
        self.time_base = Fraction(1, 1000)
        self.n = n

    def to_ndarray(self, format):
        if format != 'bgr24':
            raise AssertionError(format)
        return self.n


class FakeDetector:
    execution_providers = ['TestProvider']
    def detect(self, image):
        return ([[0, 0, 10, 20], [4, 4, 2, 8], [1, 1, 4, 5]], [.9, .8, .05])


class FakeTracker:
    def __init__(self):
        self.tracks = []
        self.calls = []
        self.step = 0

    def update(self, boxes, scores):
        self.calls.append((boxes, scores))
        self.step += 1
        if self.step == 1:
            self.tracks = [SimpleNamespace(id=0, time_since_update=0, hits=1, score=.9)]
            return [(boxes[0], 0, .9)]
        if self.step == 2:
            self.tracks[0].time_since_update = 1
            return []
        if self.step == 3:
            self.tracks[0].time_since_update = 0
            self.tracks[0].hits += 1
            return [(boxes[0], 0, .9)]
        self.tracks = []
        return []


class DetectTests(unittest.TestCase):
    def setUp(self):
        # Synthetic detector tests model a clean standalone entry process.
        # Other test modules import Torch during unittest discovery.
        saved_modules = patch.dict(sys.modules)
        saved_modules.start()
        self.addCleanup(saved_modules.stop)
        sys.modules.pop('torch', None)
        self.assertTrue(SCRIPT.exists(), 'detection entry point is missing')
        spec = importlib.util.spec_from_file_location('detect_entry', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.shots = [{'shot_id': 'a', 'source_zero_start': 0, 'source_zero_end': 3},
                      {'shot_id': 'b', 'source_zero_start': 4, 'source_zero_end': 4}]
        self.trackers = []

    def factory(self):
        tracker = FakeTracker()
        self.trackers.append(tracker)
        return tracker

    def test_boundaries_pts_raw_and_reset(self):
        rows = list(self.m.process_frames(self.shots, map(FakeFrame, range(5)), FakeDetector(), self.factory))
        self.assertEqual([r['source_zero_based'] for r in rows], list(range(5)))
        self.assertEqual([r['pts'] for r in rows], [13, 31, 62, 100, 151])
        self.assertEqual(rows[0]['time_base'], '1/1000')
        self.assertEqual(rows[-1]['window'], 'b')
        self.assertEqual(len(self.trackers), 2)
        self.assertEqual(rows[-1]['lifecycle']['new'], [0])
        self.assertEqual(len(rows[0]['raw_detections']), 3)
        self.assertEqual([d['fed_to_tracker'] for d in rows[0]['raw_detections']], [True, False, True])
        self.assertEqual(len(self.trackers[0].calls[0][0]), 2)
        self.assertTrue(all(d['actor_id'] is None for d in rows[0]['raw_detections']))
        self.assertEqual(rows[1]['observed_tracks'], [])
        self.assertEqual(rows[1]['lost_buffer'][0]['source'], 'unknown')
        self.assertEqual(rows[1]['lifecycle']['became_lost'], [0])
        self.assertEqual(rows[2]['lifecycle']['recovered'], [0])
        self.assertEqual(rows[3]['lifecycle']['expired'], [0])

    def test_gap_not_processed_and_eof_fails(self):
        shots = [{'shot_id': 'gap', 'source_zero_start': 2, 'source_zero_end': 3}]
        rows = list(self.m.process_frames(shots, map(FakeFrame, range(5)), FakeDetector(), self.factory))
        self.assertEqual([r['source_zero_based'] for r in rows], [2, 3])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            list(self.m.process_frames(self.shots, map(FakeFrame, range(3)), FakeDetector(), self.factory))

    def test_bad_shots_and_missing_pts_fail(self):
        for shots in ([], [self.shots[0], self.shots[0]],
                      [{'shot_id': '../a', 'source_zero_start': 0, 'source_zero_end': -1}],
                      [{'shot_id': 'x', 'source_zero_start': True, 'source_zero_end': 2}]):
            with self.subTest(shots=shots), self.assertRaises(ValueError):
                self.m.validate_shots(shots)
        frame = FakeFrame(0)
        frame.pts = None
        with self.assertRaisesRegex(ValueError, 'PTS'):
            list(self.m.process_frames(self.shots, [frame], FakeDetector(), self.factory))

    def test_nonfinite_raw_is_retained_but_not_fed(self):
        detector = SimpleNamespace(detect=lambda _: ([[0, 0, 5, 5], [float('nan'), 0, 5, 5]], [.9, float('inf')]))
        shot = [dict(self.shots[0], source_zero_end=0)]
        rows = list(self.m.process_frames(shot, [FakeFrame(0)], detector, self.factory))
        raw = rows[0]['raw_detections']
        self.assertEqual(len(raw), 2)
        self.assertFalse(raw[1]['fed_to_tracker'])
        self.assertEqual(raw[1]['box_xyxy'][0], 'NaN')
        json.dumps(rows, allow_nan=False)

    def test_plan_is_stdlib_only_and_no_output_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shots = base/'shots.json'
            shots.write_text(json.dumps(self.shots), encoding='utf-8')
            args = ['--source', str(base/'video.mp4'), '--source-sha256', 'a'*64,
                    '--repo', str(base/'repo'), '--model', str(base/'model.onnx'), '--model-sha256', 'b'*64,
                    '--shots', str(shots), '--output-new-dir', str(base/'out'), '--threads2', '--plan-only']
            run = subprocess.run([sys.executable, '-B', '-S', str(SCRIPT), *args], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            plan = json.loads(run.stdout)
            self.assertEqual(plan['resources']['device'], 'cpu')
            self.assertIn('scipy', plan['dependencies'])
            self.assertNotIn('--plan-only', plan['argv'])
            self.assertFalse((base/'out').exists())

    def test_receipt_binds_hashes_and_failure_partial_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base/'repo'
            code = repo/'gem/common/detectors'
            code.mkdir(parents=True)
            for name in ('yolox.py', 'bytetrack.py'):
                (code/name).write_text('# fake code', encoding='utf-8')
            source, model, shots = base/'source', base/'model', base/'shots.json'
            source.write_bytes(b'fake source')
            model.write_bytes(b'fake model')
            shots.write_text(json.dumps(self.shots), encoding='utf-8')
            args = self.m.make_parser().parse_args(['--source', str(source), '--source-sha256', self.m.sha256(source),
                '--repo', str(repo), '--model', str(model), '--model-sha256', self.m.sha256(model),
                '--shots', str(shots), '--output-new-dir', str(base/'out'), '--threads2'])
            def runtime(_):
                return FakeDetector(), self.factory, lambda: iter(map(FakeFrame, range(3)))
            with patch.object(self.m, 'load_runtime', runtime), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(self.m.execute(args), 2)
            receipt = json.loads((base/'out/receipt.json').read_text())
            self.assertEqual(receipt['actual_frames'], 3)
            self.assertFalse(receipt['requested_ranges_complete'])
            self.assertFalse(receipt['delivery_ready'])
            self.assertEqual(receipt['outputs']['frames.jsonl'], self.m.sha256(base/'out/frames.jsonl'))
            original = (base/'out/receipt.json').read_bytes()
            with patch.object(self.m, 'load_runtime', side_effect=AssertionError('must not run')), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(self.m.execute(args), 2)
            self.assertEqual((base/'out/receipt.json').read_bytes(), original)

    def test_success_and_hash_mismatch_receipts_without_real_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code = base/'repo/gem/common/detectors'
            code.mkdir(parents=True)
            for name in ('yolox.py', 'bytetrack.py'):
                (code/name).write_text('# fake', encoding='utf-8')
            for name in ('source', 'model'):
                (base/name).write_bytes(name.encode())
            (base/'shots.json').write_text(json.dumps(self.shots), encoding='utf-8')
            args = self.m.make_parser().parse_args(['--source', str(base/'source'), '--source-sha256', self.m.sha256(base/'source'),
                '--repo', str(base/'repo'), '--model', str(base/'model'), '--model-sha256', self.m.sha256(base/'model'),
                '--shots', str(base/'shots.json'), '--output-new-dir', str(base/'ok')])
            runtime = lambda _: (FakeDetector(), self.factory, lambda: iter(map(FakeFrame, range(5))))
            with patch.object(self.m, 'load_runtime', runtime), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(self.m.execute(args), 0)
            receipt = json.loads((base/'ok/receipt.json').read_text())
            self.assertTrue(receipt['requested_ranges_complete'])
            self.assertEqual(receipt['actual_interpreter'], sys.executable)
            self.assertEqual(receipt['actual_execution_providers'], ['TestProvider'])
            self.assertFalse(receipt['full_video_coverage_verified'])
            self.assertEqual(receipt['actual_frames'], 5)
            self.assertEqual(receipt['per_shot_actual_frames'], {'a': 4, 'b': 1})
            self.assertEqual(receipt['inputs']['model']['sha256'], args.model_sha256)
            self.assertEqual(receipt['inputs']['entry_code']['sha256'], self.m.sha256(SCRIPT))
            self.assertEqual(receipt['outputs']['frames.jsonl'], self.m.sha256(base/'ok/frames.jsonl'))
            args.output_new_dir = base/'torch_loaded'
            with patch.dict(sys.modules, {'torch': object()}), patch.object(self.m, 'load_runtime', runtime), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(self.m.execute(args), 2)
            self.assertFalse(json.loads((base/'torch_loaded/receipt.json').read_text())['requested_ranges_complete'])
            args.output_new_dir = base/'bad_hash'
            args.model_sha256 = '0'*64
            with patch.object(self.m, 'load_runtime', side_effect=AssertionError('must not run')), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(self.m.execute(args), 2)
            receipt = json.loads((base/'bad_hash/receipt.json').read_text())
            self.assertFalse(receipt['requested_ranges_complete'])
            self.assertEqual(receipt['actual_frames'], 0)
            self.assertIn('SHA256 mismatch', receipt['errors'][0])

    def test_close_failure_cannot_hide_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code = base/'repo/gem/common/detectors'
            code.mkdir(parents=True)
            for name in ('yolox.py', 'bytetrack.py'):
                (code/name).write_text('# fake', encoding='utf-8')
            for name in ('source', 'model'):
                (base/name).write_bytes(name.encode())
            (base/'shots.json').write_text(json.dumps(self.shots), encoding='utf-8')
            args = self.m.make_parser().parse_args(['--source', str(base/'source'), '--source-sha256', self.m.sha256(base/'source'),
                '--repo', str(base/'repo'), '--model', str(base/'model'), '--model-sha256', self.m.sha256(base/'model'),
                '--shots', str(base/'shots.json'), '--output-new-dir', str(base/'out')])
            class BadClose:
                def __iter__(inner):
                    return iter(map(FakeFrame, range(5)))
                def close(inner):
                    raise OSError('decoder close failed')
            with patch.object(self.m, 'load_runtime', lambda _: (FakeDetector(), self.factory, BadClose)), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(self.m.execute(args), 2)
            self.assertEqual(json.loads((base/'out/receipt.json').read_text())['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
