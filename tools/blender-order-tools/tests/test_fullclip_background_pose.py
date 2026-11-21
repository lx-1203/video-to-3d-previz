"""Synthetic coverage for a seated/standing negative and a crouched stationary fragment."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_background_pose import background_observations, _depth_track


def sequence(*, marked=True, upright=False):
    rows = []
    for i in range(17):
        x = i * 2

        def joint(u, v):
            return dict(u=u + x, v=v, score=0.75, visibility="model_estimate")

        joints = {
            "left_shoulder": joint(65, 40),
            "right_shoulder": joint(70, 41),
            "left_hip": joint(40, 82),
            "right_hip": joint(43, 80),
            "nose": joint(83, 55),
            "left_eye": joint(80, 51),
            "left_wrist": joint(80, 88),
        }
        if upright:
            joints.update(nose=joint(67, 16), left_hip=joint(62, 82), right_hip=joint(70, 83))
        a = {
            "joints2d": joints,
            "bbox": [14 + x, 3, 103 + x, 119],
            "bbox_kind": "person_detection",
        }
        if 6 <= i <= 10:
            a["bbox_kind"] = "bounded_person_flow"
            a["joints2d"] = {}
            if marked:
                a["temporal_support"] = {
                    "state": "inferred_from_background_motion",
                    "source_frames": [5, 11],
                }
        rows.append((i, a))
    return rows


class BackgroundPoseTest(unittest.TestCase):
    def test_low_confidence_one_hip_retains_bend(self):
        seq = sequence()
        seq[12][1]["joints2d"]["left_hip"]["score"] = 0.1
        o = background_observations(seq)
        self.assertEqual(len(o), len(seq))
        a = o[12]["uv"]
        self.assertGreater(a["shoulder"][0] - a["hip"][0], 12)
        self.assertGreater(a["head"][1], a["shoulder"][1])

    def test_camera_motion_gap_uses_shifted_box(self):
        out = background_observations(sequence())
        self.assertEqual(out[8]["source_state"], "bounded_neighbor_inference")
        np.testing.assert_allclose(out[9]["uv"]["head"] - out[7]["uv"]["head"], [4, 0], atol=1e-6)

    def test_foreground_and_standing_negatives(self):
        self.assertEqual(background_observations(sequence(marked=False)), {})
        self.assertEqual(background_observations(sequence(upright=True)), {})

    def test_missing_face_and_foreign_wrist_samples_rejected(self):
        seq = sequence()
        for _, a in seq:
            for k in ("nose", "left_eye"):
                a["joints2d"].pop(k, None)
        self.assertEqual(background_observations(seq), {})
        seq = sequence()
        for _, a in seq:
            if "left_wrist" in a["joints2d"]:
                a["joints2d"]["left_wrist"]["v"] = 400
        self.assertEqual(background_observations(seq), {})

    def test_depth_rejects_lone_row_and_extreme_frame(self):
        f = list(range(20))
        a = {i: 8.0 for i in f}
        a[8] = 3.0
        a[9] = 22.0
        rows = {i: {"rear": 8.1} for i in f}
        rows[8]["outlier"] = 9.0
        d, source_row = _depth_track(f, a, rows, 0.5)
        self.assertEqual(source_row, "rear")
        self.assertLess(np.ptp(list(d.values())), 0.05)
        self.assertTrue(8.0 < d[8] < 8.4)

    def test_without_supported_rows_uses_apparent_span(self):
        f = list(range(12))
        s = {i: 7.5 for i in f}
        result, row = _depth_track(f, s, {i: {} for i in f}, 0.5)
        self.assertIsNone(row)
        self.assertAlmostEqual(result[0], 7.5)


if __name__ == "__main__":
    unittest.main()
