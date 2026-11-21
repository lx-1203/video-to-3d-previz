"""Local depth ordering requires observed obstruction plus an independent size anomaly."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_guardrail_actor_visibility import (
    constrained_depth,
    direct_head,
    torso_colour_witnesses,
)


class ActorVisibilityTests(unittest.TestCase):
    def test_smooth_recovery_from_false_occlusion(self):
        true = np.linspace(8.2, 7.0, 40)
        depth = true.copy()
        depth[12:25] += 1.8
        ppm = 670 / true
        limit = np.full(40, np.inf)
        limit[12:25] = true[12:25] + 0.18
        clean = ~np.isfinite(limit)
        solved, ranges = constrained_depth(depth, ppm, limit, clean)
        self.assertEqual(len(ranges), 1)
        self.assertTrue(np.all(solved[12:25] < limit[12:25]))
        self.assertLess(np.max(abs(np.diff(solved))), 0.13)
        self.assertLess(np.max(abs(solved - true)), 0.14)
        np.testing.assert_array_equal(solved[:7], depth[:7])

    def test_correct_size_or_real_recession_keeps_occluder(self):
        limit = np.full(36, np.inf)
        limit[10:24] = 6.0
        clean = ~np.isfinite(limit)
        for depth in (
            np.full(36, 7.0),
            np.r_[np.full(10, 7.0), np.full(14, 8.6), np.full(12, 7.0)],
        ):
            solved, events = constrained_depth(depth, 640 / depth, limit, clean)
            np.testing.assert_array_equal(solved, depth)
            self.assertEqual(events, [])
        bad = np.r_[np.full(10, 7.0), np.full(14, 8.6), np.full(12, 7.0)]
        solved, events = constrained_depth(bad, np.full(36, 90.0), limit, np.zeros(36, bool))
        np.testing.assert_array_equal(solved, bad)
        self.assertEqual(events, [])

    def test_opening_only_uses_consistent_visible_future_scale(self):
        depth = np.full(32, 7.0)
        depth[:8] = 9.1
        ppm = np.full(32, 80.0)
        ceiling = np.full(32, np.inf)
        ceiling[:8] = 7.7
        fit, ranges = constrained_depth(depth, ppm, ceiling, ~np.isfinite(ceiling))
        self.assertEqual(len(ranges), 1)
        self.assertLess(max(fit[:8]), 7.3)
        self.assertLess(np.max(abs(np.diff(fit))), 0.16)
        # Same size change explained by the real recession must remain.
        preserved, events = constrained_depth(depth, 560 / depth, ceiling, ~np.isfinite(ceiling))
        self.assertFalse(events)
        np.testing.assert_array_equal(preserved, depth)

    def test_eye_midpoint_requires_three_real_consistent_landmarks(self):
        def point(u, v):
            return dict(u=u, v=v, score=0.79, source="rtmpose", visibility="model_estimate")

        actor = {
            "anchors": {"head": dict(state="eye_midpoint", u=50, v=30, score=0.8)},
            "joints2d": {
                "left_eye": point(45, 30),
                "right_eye": point(55, 30),
                "nose": point(50, 37),
            },
        }
        self.assertTrue(direct_head(actor))
        actor["joints2d"]["left_eye"]["source"] = "held_direction"
        self.assertFalse(direct_head(actor))

    def test_foreground_occluder_breaks_torso_colour_witnesses(self):
        im = np.full((180, 220, 3), (155, 155, 155), np.uint8)
        im[30:149, 77:144] = (22, 26, 35)
        actor = {
            "track_id": "subject",
            "anchors": {
                "head": {"state": "face_landmarks"},
                "shoulder": {"u": 111, "v": 49, "score": 0.88},
                "hip": {"u": 110, "v": 143, "score": 0.85},
            },
            "joints2d": {
                "left_shoulder": {"u": 87, "score": 0.9},
                "right_shoulder": {"u": 133, "score": 0.9},
            },
        }
        good = torso_colour_witnesses(im, [actor])["subject"]
        self.assertTrue(any(fraction >= 0.9 for _, _, fraction in good))
        im[109:] = (95, 181, 155)
        blocked = torso_colour_witnesses(im, [actor])["subject"]
        self.assertTrue(all(fraction < 0.7 for _, _, fraction in blocked))


if __name__ == "__main__":
    unittest.main()
