"""Shot-local backtracking must preserve a genuine entry and stop at the occluder."""

import unittest

import numpy as np

from orderflow.fullclip_people_reveals import trace_reveal


class ClothingRevealTest(unittest.TestCase):
    def setUp(self):
        self.donor = dict(
            track_id="back",
            bbox=[57, 30, 103, 92],
            joints2d={
                name: dict(u=u, v=v, score=0.8)
                for name, (u, v) in {
                    "left_shoulder": (66, 40),
                    "right_shoulder": (94, 40),
                    "left_hip": (71, 68),
                    "right_hip": (89, 68),
                }.items()
            },
        )
        front = dict(track_id="front", bbox_kind="person_detection", bbox=[5, 2, 95, 134])
        self.rows = [dict(shot_id="one", actors=[front]) for _ in range(10)]
        self.images = {i: np.full((140, 160, 3), 120, np.uint8) for i in range(10)}
        tile = np.random.default_rng(55).integers(5, 160, size=(36, 45, 3), dtype=np.uint8)
        for i in range(4, 10):
            x = 58 + 2 * (i - 9)
            self.images[i][35:71, x : x + 45] = tile

    def test_backtrack_to_last_visible_clothing(self):
        path = trace_reveal(self.images, self.rows, 9, self.donor, 0)
        self.assertEqual([k for k, _, _ in path], list(range(8, 3, -1)))
        for k, offset, _ in path:
            np.testing.assert_array_equal(offset, [2 * (k - 9), 0])

    def test_genuine_first_entry_and_other_identity(self):
        for i in range(9):
            self.images[i].fill(120)
        self.assertEqual(trace_reveal(self.images, self.rows, 9, self.donor, 0), [])
        self.setUp()
        self.rows[6]["actors"].append(
            dict(track_id="someone_else", bbox_kind="person_detection", bbox=[50, 30, 96, 92])
        )
        self.assertEqual(trace_reveal(self.images, self.rows, 9, self.donor, 0), [])

    def test_no_witness_before_new_shot(self):
        for row in self.rows[:8]:
            row["shot_id"] = "previous"
        self.assertEqual(trace_reveal(self.images, self.rows, 9, self.donor, 8), [])
        self.setUp()
        for row in self.rows:
            row["actors"] = []
        self.assertEqual(trace_reveal(self.images, self.rows, 9, self.donor, 0), [])
