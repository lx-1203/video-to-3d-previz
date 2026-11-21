import unittest
from unittest.mock import patch

import numpy as np

from orderflow import fullclip_torso_depth as torso


class DepthTimestampTest(unittest.TestCase):
    def test_use_same_time_actor_and_bracket_depth_anchors(self):
        frames = [0, 10, 14]
        shots = {"depth_samples": "depth", "depth_sha256": "sha", "intrinsics": np.eye(3)}
        actors = [
            (
                i,
                {
                    "orientation": {"facing_state": "back_or_down_inferred"},
                    "anchors": {
                        "head": {"u": 63 + i, "v": 10},
                        "shoulder": {"u": 22 + i, "v": 38},
                    },
                },
            )
            for i in range(15)
        ]

        def axes(depth, k, shoulder, head, aspect):
            self.assertAlmostEqual(shoulder[0], (22 + depth) / 100)
            return torso.unit(np.array([1.0, depth / 14.0, 0.0])), np.array([0.0, 0.0, 1.0])

        with (
            patch.object(torso, "_depth_samples", return_value=(frames, frames)),
            patch.object(torso, "measured_axes", side_effect=axes),
        ):
            result = torso.torso_track(shots, actors, 100, 100)
        self.assertEqual(len(result), 15)
        angles = [np.arctan2(result[i][0][1], result[i][0][0]) for i in range(15)]
        self.assertTrue((np.diff(angles) > 0).all())
        self.assertLess(np.max(np.diff(angles)), 0.08)

    def test_front_facing_does_not_request_back_surface(self):
        self.assertEqual(
            torso.torso_track({}, [(0, {"orientation": {"facing_state": "front"}})], 100, 100), {}
        )
