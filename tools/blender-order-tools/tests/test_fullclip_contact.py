import unittest

import numpy as np

from orderflow import fullclip_contact_path as solver


def cube(x0=-1, x1=1):
    return np.array([[x, y, z] for x in (x0, x1) for y in (-1, 1) for z in (-1, 1)])


class ContactPathTest(unittest.TestCase):
    def test_end_uses_finite_tread(self):
        normals = np.vstack((np.eye(3), -np.eye(3)))
        solid = (np.full(3, -1), np.full(3, 1), normals, np.ones(6))
        shape = cube() * 0.05
        positions = np.stack(
            [shape + [0, 0, 0.6], shape + [0, 0, 1.06], shape + [2, 0, 1.06]]
        )
        penetration, _, gap = solver.support_cost(
            positions, positions, positions, [solid]
        )
        self.assertGreater(penetration[0], 0)
        self.assertEqual(penetration[1], 0)
        self.assertAlmostEqual(gap[1], 0.01)
        self.assertGreater(gap[2], 0.8)

    def test_full_path_limits_reversals(self):
        n = len(solver.ANGLES)
        costs = np.full((20, n), 1.0)
        costs[:10, 0] = 0
        costs[10:, -1] = 0
        indices = solver._path(costs)
        coords = np.array(np.unravel_index(indices, solver.SHAPE))
        self.assertLessEqual(abs(np.diff(coords, axis=1)).max(), 1)

    def test_no_observed_collision_preserves_pose(self):
        rows = [
            (np.zeros(len(solver.ANGLES)), np.zeros((len(solver.ANGLES), 3)), False)
        ] * 10
        np.testing.assert_array_equal(solver.fit_sequence(rows), np.zeros((10, 3)))

    def test_world_rotation_preserves_rigid_distances(self):
        rotation = solver.rotations(np.array([0, 0, 1]), np.array([0.37]))[0]
        points = cube()
        before = np.linalg.norm(points[:, None] - points, axis=-1)
        after = points @ rotation.T
        np.testing.assert_allclose(
            np.linalg.norm(after[:, None] - after, axis=-1), before
        )

    def test_bilateral_guard_avoids_new_collision_through_blocked_band(self):
        from orderflow.fullclip_contact_guard import GAINS, _select

        count = len(GAINS)
        cost = np.ones((24, count, count)) * 2
        cost[:, -1, -1] = 0
        cost[6:17, 1:, :] = np.inf
        states = _select(cost)
        self.assertTrue(
            np.isfinite(cost[np.arange(24), states[:, 0], states[:, 1]]).all()
        )
        self.assertTrue((states[6:17, 0] == 0).all())
        self.assertLessEqual(abs(np.diff(states, axis=0)).max(), 1)

    def test_credible_source_pose_anchors_both_sides_of_interpolation(self):
        count = len(solver.ANGLES)
        costs = np.ones(count) * 5
        costs[0] = 0
        corrections = np.zeros((count, 3))
        rows = [(costs, corrections, True, f == 9) for f in range(19)]
        track = solver.fit_sequence(rows)
        np.testing.assert_array_equal(track[9], [0, 0, 0])
        self.assertLess(np.linalg.norm(track[8]), np.linalg.norm(track[6]))
        self.assertLess(np.linalg.norm(track[10]), np.linalg.norm(track[12]))
