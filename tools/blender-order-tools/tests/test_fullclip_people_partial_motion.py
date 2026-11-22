"""Temporal orientation checks on synthetic partial-body tracks."""

import copy
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_people_partial_pose import _sample
from orderflow.fullclip_people_partial_motion import stabilize_partial_motion


PROFILE = {"torso": 0.64, "shoulder_width": 0.36, "head_offset": 0.2}


def actor(angle, strength=0.12, track="moving"):
    theta = np.radians(angle)
    up = np.array([np.sin(theta), np.cos(theta), 0.35])
    up /= np.linalg.norm(up)
    lateral = np.array([np.cos(theta), -np.sin(theta), 0.0])
    hip = np.array([0.0, 0.0, 0.0])
    sh = hip + PROFILE["torso"] * up
    joints = {"hip": hip, "shoulder": sh, "head": sh + PROFILE["head_offset"] * up}
    for side, sign in (("left", 1), ("right", -1)):
        start = sh + sign * 0.5 * PROFILE["shoulder_width"] * lateral
        joints[side + "_shoulder"] = start
        joints[side + "_elbow"] = start + np.array([0.03, 0.2, -0.15])
        joints[side + "_wrist"] = joints[side + "_elbow"] + np.array([0.1, 0.06, -0.21])
    data = {
        "torso_state": "source_and_bend_inference",
        "outline_profile": [0.4, 0.55] * 4,
        "visible_bbox": [20.0, 10.0, 90.0, 60.0],
        "visible_centroid_uv": [55.0, 35.0],
        "shoulder_uv": [55.0 + 0.05 * angle, 30.0],
        "face_uv": [55.0, 36.0],
        "hip_uv": [55.0, 44.0],
    }
    uv = {
        "nose": [55.0, 36.0],
        "left_shoulder": [63.0, 30.0],
        "right_shoulder": [47.0, 31.0],
        "left_hip": [58.0, 44.0],
        "right_hip": [44.0, 44.0],
    }
    return {
        "track_id": track,
        "partial_body_track": data,
        "orientation": {"torso_up_xyz": up.tolist()},
        "joints3d": {k: v.tolist() for k, v in joints.items()},
        "joints2d": {k: dict(u=v[0], v=v[1], score=strength) for k, v in uv.items()},
    }


def stabilize(seq):
    stabilize_partial_motion(seq, PROFILE, _sample)


def angular_steps(seq):
    up = np.array([a["orientation"]["torso_up_xyz"] for _, a in seq])
    return np.degrees(np.arccos(np.clip((up[1:] * up[:-1]).sum(axis=1), -1, 1)))


class PartialMotionTest(unittest.TestCase):
    def test_flicker_stabilizes_preserving_bones_and_signed_shoulders(self):
        seq = [(i, actor(15 + 0.7 * i if i % 3 else -65 + 0.7 * i)) for i in range(40)]
        before = copy.deepcopy(seq)
        stabilize(seq)
        self.assertLess(np.max(angular_steps(seq)), 10.0)
        self.assertGreater(np.sum(angular_steps(seq)), 9.0)
        for (_, old), (_, new) in zip(before, seq):
            j, p = old["joints3d"], new["joints3d"]
            for side in ("left", "right"):
                for k, v in (("shoulder", "elbow"), ("elbow", "wrist")):
                    s, e = side + "_" + k, side + "_" + v
                    np.testing.assert_allclose(
                        np.subtract(p[s], p[e]), np.subtract(j[s], j[e]), atol=1.1e-5
                    )
            for a, b, length in (
                ("hip", "shoulder", 0.64),
                ("left_shoulder", "right_shoulder", 0.36),
                ("head", "shoulder", 0.2),
            ):
                self.assertAlmostEqual(np.linalg.norm(np.subtract(p[a], p[b])), length, places=4)
            self.assertGreater(p["left_shoulder"][0], p["right_shoulder"][0])

    def test_directly_observed_fast_turn_remains_at_the_same_frames(self):
        angles = [0, 2, 7, 65, 101, 110, 112, 114, 115]
        seq = [(i, actor(theta, 0.85 if 2 <= i <= 6 else 0.12)) for i, theta in enumerate(angles)]
        originals = copy.deepcopy(seq)
        stabilize(seq)
        self.assertEqual(seq[2:7], originals[2:7])
        self.assertGreater(angular_steps(seq)[2], 50.0)

    def test_only_single_matching_unobserved_posture_can_bridge(self):
        seq = [(i, actor(20 + i)) for i in range(10)]
        for i in (2, 5, 6, 8):
            del seq[i][1]["partial_body_track"]["torso_state"]
        seq[8][1]["partial_body_track"]["visible_centroid_uv"] = [300, 400]
        original = copy.deepcopy(seq)
        stabilize(seq)
        self.assertEqual(
            seq[2][1]["partial_body_track"]["torso_state"],
            "neighbor_contour_bridge_inference",
        )
        self.assertEqual(seq[5:7], original[5:7])
        self.assertEqual(seq[8:], original[8:])

    def test_gaps_identities_and_full_actors_do_not_share_candidates(self):
        seq = [
            (0, actor(3)),
            (1, actor(4)),
            (2, actor(5)),
            (5, actor(-65)),
            (6, actor(-65)),
            (7, actor(-65)),
        ]
        other = actor(82, track="other")
        seq.append((8, other))
        absent = actor(15)
        del absent["partial_body_track"]
        seq.append((9, absent))
        original = copy.deepcopy(seq)
        stabilize(seq)
        self.assertEqual(seq, original)


if __name__ == "__main__":
    unittest.main()
