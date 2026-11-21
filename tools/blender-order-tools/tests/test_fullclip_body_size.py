"""Regression tests for source body spans and source-constrained two-arm contact."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow import fullclip_body_size as fit


def camera():
    p = np.zeros((4, 4))
    p[0, 0] = p[1, 1] = 1.6
    p[3, 2] = -1.0
    return p


def chain(sign):
    shoulder = np.array([sign * 0.33, 0.17, -2.0])
    elbow = np.array([sign * 0.22, -0.04, -2.14])
    wrist = np.array([sign * 0.04, -0.04, -2.02])
    ray = wrist / -wrist[2]
    lengths = [np.linalg.norm(shoulder - elbow), np.linalg.norm(elbow - wrist)]
    return dict(
        shoulder=shoulder,
        elbow=elbow,
        wrist=wrist,
        ray=ray,
        lengths=lengths,
        interval=fit._reach(ray, shoulder, sum(lengths)),
        source_elbow=fit._screen(elbow, camera(), 640, 360),
        tolerance=7.0,
    )


class SourceBodyFit(unittest.TestCase):
    def test_occluded_hip_cannot_measure_torso(self):
        joints = {}
        for name, y in (("hip", 165), ("shoulder", 68)):
            for side, x in (("right", 90), ("left", 135)):
                joints[side + "_" + name] = dict(u=x, v=y, visibility="observed", score=0.9)
        self.assertEqual(fit._torso(dict(joints2d=joints), 240, 200)["torso_px"], 97.0)
        joints["right_hip"]["visibility"] = "inferred"
        self.assertEqual(fit._torso(dict(joints2d=joints), 240, 200), {})

    def test_head_box_requires_matching_actor(self):
        actors = [
            dict(
                bbox=[80, 55, 170, 195],
                anchors={"head": {"u": 132, "v": 64}},
                joints2d={},
            )
        ]
        items = [
            dict(class_id=1, score=0.99, bbox=[5, 10, 33, 41]),
            dict(class_id=1, score=0.8, bbox=[118, 52, 144, 82]),
        ]
        self.assertEqual(fit._head_matches(actors, items)[0][0].tolist(), items[1]["bbox"])

    def test_head_face_seed_needs_image_contours(self):
        image = np.full((200, 240, 3), 100, np.uint8)
        self.assertEqual(
            fit._head_outline(image, np.array([80.0, 60.0, 100.0, 90.0]), 0.9, True), {}
        )

    def test_depth_changes_only_translation_and_preserves_shoulder_ray(self):
        lm = dict(
            left_shoulder=np.array([-0.22, 0.0, 0.6]),
            right_shoulder=np.array([0.22, 0.0, 0.6]),
            left_hip=np.array([-0.14, 0.0, 0.0]),
            right_hip=np.array([0.14, 0.0, 0.0]),
        )
        loc = np.array([0.0, 4.0, 0.0])
        obs = dict(torso_px=58.0, shoulder_px=75.0, confidence=0.9, shoulder_uv=[320, 136.8])
        new, meta = fit.adjust_body_depth({}, obs, lm, [], loc, camera(), 640, 360)
        self.assertLess(new[1], loc[1])
        self.assertLess(abs(meta["after"]["torso_px"] - 58), abs(meta["before"]["torso_px"] - 58))
        self.assertLess(meta["anchor_errors_px"]["shoulder_center"]["after"], 0.02)
        np.testing.assert_array_equal(lm["left_hip"], [-0.14, 0, 0])
        np.testing.assert_array_equal(loc, [0, 4, 0])

    def test_unobserved_body_stays_at_original_depth(self):
        loc = [0, 3, 0]
        new, r = fit.adjust_body_depth({}, None, {}, None, loc, camera(), 640, 360)
        np.testing.assert_array_equal(new, loc)
        self.assertFalse(r["applied"])

    def test_two_arms_preserve_lengths_and_elbow_projection(self):
        arms = [chain(-1), chain(1)]
        goals, report = fit._choose_common_depth(arms, camera(), 640, 360)
        self.assertEqual(report["state"], "inferred_monocular_contact")
        for arm, goal in zip(arms, goals):
            lengths = [
                np.linalg.norm(arm["shoulder"] - goal["elbow"]),
                np.linalg.norm(goal["elbow"] - goal["wrist"]),
            ]
            np.testing.assert_allclose(lengths, arm["lengths"], atol=1e-6)
            self.assertLessEqual(goal["error"], arm["tolerance"])
        self.assertAlmostEqual(goals[0]["wrist"][2], goals[1]["wrist"][2])

    def test_reject_depth_gap_and_bad_elbow(self):
        arms = [chain(-1), chain(1)]
        arms[1]["interval"] = (4, 5)
        goals, r = fit._choose_common_depth(arms, camera(), 640, 360)
        self.assertIsNone(goals)
        self.assertEqual(r["reason"], "disjoint_reach")
        arms = [chain(-1), chain(1)]
        arms[0]["source_elbow"] += np.array([240, 160])
        goals, r = fit._choose_common_depth(arms, camera(), 640, 360)
        self.assertIsNone(goals)
        self.assertEqual(r["reason"], "source_elbow_outside_interval")

    def test_ortho_cannot_recover_depth_from_body_span(self):
        _, r = fit.adjust_body_depth(
            {},
            dict(torso_px=50.0, confidence=0.8),
            {},
            [],
            [0, 3, 0],
            np.eye(4),
            640,
            360,
        )
        self.assertEqual(r["state"], "no_perspective_size_cue")

    def test_fade_preserves_wrist_ray_and_lengths(self):
        arms = [chain(-1), chain(1)]
        goals, _ = fit._choose_common_depth(arms, camera(), 640, 360)
        c, g = arms[0], goals[0]
        factor = 0.4
        q = fit._fade_goal(c, g, factor, camera(), 640, 360)
        np.testing.assert_allclose(
            q["wrist"], (1 - factor) * c["wrist"] + factor * g["wrist"], atol=1e-6
        )
        self.assertAlmostEqual(np.linalg.norm(q["elbow"] - c["shoulder"]), c["lengths"][0])
        self.assertAlmostEqual(np.linalg.norm(q["elbow"] - q["wrist"]), c["lengths"][1])

    def test_concave_grip_does_not_fill_cutout(self):
        poly = [[0, 0], [20, 0], [20, 5], [5, 5], [5, 20], [0, 20]]
        self.assertEqual(fit._distance_to_polygon([2, 12], poly), 0.0)
        self.assertAlmostEqual(fit._distance_to_polygon([8, 12], poly), 3.0)


if __name__ == "__main__":
    unittest.main()
