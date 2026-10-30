"""Synthetic quality/provenance boundaries; never run a model or Blender."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / 'scripts/motion' / (name + '.py')
    spec = importlib.util.spec_from_file_location('edges_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MediaPipeOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.motion = load_script('mediapipe_motion')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.video, self.model = self.root / 'video.bin', self.root / 'model.bin'
        self.video.write_bytes(b'not a real video')
        self.model.write_bytes(b'not a real model')
        # Only import boundaries are doubled. Output checks and filesystem
        # behavior use the real infer() code; decoding is never performed.
        self.capture = Mock(return_value=SimpleNamespace(isOpened=lambda: False))
        cv2 = SimpleNamespace(VideoCapture=self.capture)
        media = ModuleType('mediapipe')
        tasks = ModuleType('mediapipe.tasks')
        python = ModuleType('mediapipe.tasks.python')
        vision = ModuleType('mediapipe.tasks.python.vision')
        media.tasks, tasks.python, python.vision = tasks, python, vision
        self.modules = {'cv2': cv2, 'mediapipe': media, 'mediapipe.tasks': tasks,
                        'mediapipe.tasks.python': python,
                        'mediapipe.tasks.python.vision': vision}

    def infer(self, output):
        with patch.dict(sys.modules, self.modules):
            return self.motion.infer(self.video, 0, .1, output, self.model)

    def test_every_prior_attempt_artifact_blocks_before_decoding_and_is_preserved(self):
        for name in ('motion.bvh', 'motion.bvh.json', 'motion.landmarks.npz',
                     'motion.overlay_0000.jpg', 'motion.overlay_0001.jpg',
                     'motion.overlay_9999.jpg', 'motion.bvh.failure.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                folder = Path(tmp)
                previous = folder / name
                previous.write_bytes(b'prior evidence must survive')
                self.capture.reset_mock()
                with self.assertRaisesRegex(ValueError, 'Output already exists'):
                    self.infer(folder / 'motion.bvh')
                self.capture.assert_not_called()
                self.assertEqual(previous.read_bytes(), b'prior evidence must survive')
                self.assertEqual(list(folder.iterdir()), [previous])

    def test_uppercase_bvh_suffix_still_protects_its_failure_record(self):
        previous = self.root / 'draft.BVH.failure.json'
        previous.write_bytes(b'failure')
        with self.assertRaisesRegex(ValueError, 'Output already exists'):
            self.infer(self.root / 'draft.BVH')
        self.capture.assert_not_called()
        self.assertEqual(previous.read_bytes(), b'failure')

    def test_overlay_names_are_literal_even_when_stem_contains_glob_characters(self):
        previous = self.root / '[draft].overlay_0042.jpg'
        previous.write_bytes(b'overlay')
        with self.assertRaisesRegex(ValueError, 'Output already exists'):
            self.infer(self.root / '[draft].bvh')
        self.capture.assert_not_called()
        self.assertEqual(previous.read_bytes(), b'overlay')

    def test_overlay_stem_and_extension_are_protected_without_case_sensitivity(self):
        for prior_name, output_name in (('draft.overlay_0000.jpg', 'DRAFT.bvh'),
                                        ('draft.overlay_0000.JPG', 'draft.bvh'),
                                        ('draft.OVERLAY_0000.JpG', 'DRAFT.bvh')):
            with self.subTest(prior=prior_name, output=output_name), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                folder = Path(tmp)
                previous = folder / prior_name
                previous.write_bytes(b'prior overlay must survive')
                self.capture.reset_mock()
                with self.assertRaisesRegex(ValueError, 'Output already exists'):
                    self.infer(folder / output_name)
                self.capture.assert_not_called()
                self.assertEqual(previous.read_bytes(), b'prior overlay must survive')
                self.assertEqual(list(folder.iterdir()), [previous])

    def test_fresh_output_can_reach_decode_without_touching_another_draft(self):
        other = self.root / 'other.overlay_0000.jpg'
        other.write_bytes(b'unrelated')
        with self.assertRaisesRegex(ValueError, 'Video cannot be opened'):
            self.infer(self.root / 'fresh.bvh')
        self.capture.assert_called_once_with(str(self.video.resolve()))
        self.assertEqual(other.read_bytes(), b'unrelated')


class InterpolationGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.motion = load_script('mediapipe_motion')

    def poses(self, missing):
        raw = np.tile(np.arange(99, dtype=float).reshape(33, 3), (10, 1, 1))
        raw[missing] = np.nan
        return raw, np.ones((10, 33))

    def test_single_missing_frame_over_point_two_seconds_is_rejected_at_low_fps(self):
        raw, confidence = self.poses([4])
        for fps in (1, 2, 4):
            with self.subTest(fps=fps), self.assertRaisesRegex(ValueError, 'gap exceeds 0.2 seconds'):
                self.motion.prepare_landmarks(raw, confidence, fps)

    def test_gap_at_point_two_seconds_is_accepted_and_recorded(self):
        for fps, missing in ((5, [4]), (10, [4, 5])):
            with self.subTest(fps=fps):
                raw, confidence = self.poses(missing)
                filled, info = self.motion.prepare_landmarks(raw, confidence, fps)
                self.assertTrue(np.isfinite(filled).all())
                self.assertEqual(info['interpolated_frame_indices'], missing)
                self.assertEqual(info['max_interpolation_seconds'], .2)


class SidecarRotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = load_script('blender_bridge')

    def convention(self, rotation):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'motion.bvh'
            path.write_bytes(b'synthetic metadata validation input')
            metadata = {'format': 'motion_workbench_soma_bvh_v1',
                        'bvh_sha256': self.bridge.sha256(path),
                        'position_channel_convention': 'absolute_parent_local_blender_bvh',
                        'handedness': 'right', 'unit': 'centimeters', 'unit_scale_m': .01,
                        'up_axis': 'Y', 'forward_axis': 'Z',
                        'source_rest_world_rotations': {'Hips': rotation}}
            sidecar = path.with_suffix('.bvh.json')
            sidecar.write_text(json.dumps(metadata), encoding='utf8')
            registered = {'path': str(sidecar), 'bytes': sidecar.stat().st_size,
                          'sha256': self.bridge.sha256(sidecar)}
            return self.bridge.motion_convention(path, {}, registered)

    def test_orthonormal_reflections_are_not_accepted_as_right_handed_rotations(self):
        rotations = [[[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     [[1, 0, 0], [0, -1, 0], [0, 0, 1]],
                     [[1, 0, 0], [0, 1, 0], [0, 0, -1]],
                     [[0, 1, 0], [1, 0, 0], [0, 0, 1]]]
        for rotation in rotations:
            with self.subTest(rotation=rotation):
                # Independent geometric oracle: a right-handed frame's first
                # two basis columns cross to its third, never its negative.
                basis = np.asarray(rotation).T
                np.testing.assert_array_equal(np.cross(basis[0], basis[1]), -basis[2])
                with self.assertRaisesRegex(ValueError, 'right-handed|reflection|determinant'):
                    self.convention(rotation)

    def test_proper_axis_rotations_remain_accepted_with_registered_sidecars(self):
        rotations = [[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
                     [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
                     [[0, 0, 1], [1, 0, 0], [0, 1, 0]]]
        for rotation in rotations:
            with self.subTest(rotation=rotation):
                basis = np.asarray(rotation).T
                np.testing.assert_array_equal(np.cross(basis[0], basis[1]), basis[2])
                convention = self.convention(rotation)
                self.assertEqual(convention['source_rest_world_rotations']['Hips'], rotation)


if __name__ == '__main__':
    unittest.main()
