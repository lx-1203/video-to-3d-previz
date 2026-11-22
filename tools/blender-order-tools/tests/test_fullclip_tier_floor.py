import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_tier_floor import area, cutouts, hull, merge_neighbors, subtract


class RowRecessTests(unittest.TestCase):
    def test_convex_partition_and_area(self):
        room = hull([[0, 0], [0, 6], [8, 6], [8, 0]])
        seats = hull([[3, 2], [3, 4], [5, 4], [5, 2]])
        pieces = merge_neighbors(subtract(room, seats))
        self.assertAlmostEqual(sum(map(area, pieces)), area(room) - area(seats))
        for a in pieces:
            self.assertAlmostEqual(area(hull(a)), area(a))
            # Subtraction must be idempotent: no retained cell may cross the opening.
            self.assertAlmostEqual(sum(map(area, subtract(a, seats))), area(a))
            sample = np.array([4.0, 3.0])
            edges = np.roll(a, -1, axis=0) - a
            delta = sample - a
            self.assertFalse(
                np.all(edges[:, 0] * delta[:, 1] - edges[:, 1] * delta[:, 0] >= -1.0e-6)
            )

    def test_pure_camera_rotation_has_one_static_envelope(self):
        v = np.array(
            [
                [2, 2, -3],
                [3, 2, -3],
                [2, 3, -3],
                [3, 3, -3],
                [2, 2, -1],
                [3, 2, -1],
                [2, 3, -1],
                [3, 3, -1],
            ],
            float,
        )
        parts = [(0, 1, "Seat cushion", v), (0, 1, "Stepped seating platform", v)]
        stationary = np.zeros((2, 3))
        polys = cutouts(parts, stationary, -2.0)
        self.assertEqual(len(polys), 2)
        self.assertAlmostEqual(area(polys[0]), 1.0)
        self.assertGreater(area(polys[1]), area(polys[0]))
        self.assertFalse(cutouts(parts, stationary, -4.0))

    def test_rows_keep_separate_footprints(self):
        a = hull([[0, 0], [1, 0], [1, 1], [0, 1]])
        b = a + [0, 3]
        parts = [
            (0, i, "Stepped seating platform", np.c_[q, np.full(len(q), -1.0)])
            for i, q in enumerate([a, b])
        ]
        polys = cutouts(parts, [np.array([0.0, 0.0, 3.0])], 0.0)
        self.assertEqual(len(polys), 2)
        self.assertAlmostEqual(sum(map(area, polys)), 2.0)


if __name__ == "__main__":
    unittest.main()
