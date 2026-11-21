"""Occluded background people may reappear after a continuous camera move."""

import unittest
import numpy as np
from orderflow.fullclip_people_fragments import (
    background_transform,
    match_fragments,
    intervening_track,
)


class FragmentTest(unittest.TestCase):
    def setUp(self):
        random = np.random.default_rng(12)
        source = random.integers(0, 255, (100, 210, 3), dtype=np.uint8)
        self.images = {i: np.roll(source, i * 3, axis=1) for i in range(9)}
        self.matrices = {i: np.array([[1, 0, 3], [0, 1, 0], [0, 0, 1]], float) for i in range(8)}
        self.left = (0, np.array([30.0, 20.0, 65.0, 76.0]))
        self.right = (8, np.array([54.0, 20.0, 89.0, 76.0]))

    def test_corresponding_track_survives_partial_occlusion(self):
        self.images[3][20:76, 39:74] = 255
        match = match_fragments(self.images, self.matrices, self.left, self.right)
        self.assertIsNotNone(match)
        self.assertEqual(sorted(match[1]), list(range(1, 8)))
        np.testing.assert_allclose(match[1][4][0], self.left[1] + [12, 0, 12, 0])

    def test_an_intermediate_detection_prevents_a_duplicate_inferred_person(self):
        path = {k: (self.left[1], 0.0) for k in (1, 2, 3)}
        actor = {"bbox_kind": "person_detection", "track_id": "middle", "bbox": self.left[1]}
        rows = [{"actors": []} for _ in range(4)]
        rows[1]["actors"] = [actor]
        self.assertFalse(intervening_track(path, rows, 1.0))
        rows[2]["actors"] = [actor]
        self.assertTrue(intervening_track(path, rows, 1.0))

    def test_rejects_unrelated_position_and_camera_failure(self):
        self.assertIsNone(
            match_fragments(
                self.images, self.matrices, self.left, (8, self.right[1] + [65, 0, 65, 0])
            )
        )
        self.matrices[4] = None
        self.assertIsNone(match_fragments(self.images, self.matrices, self.left, self.right))
        blank = np.zeros((100, 210, 3), np.uint8)
        self.assertIsNone(background_transform(blank, blank, []))
