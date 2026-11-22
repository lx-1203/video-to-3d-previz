import unittest

import numpy as np

from orderflow import fullclip_contact_plant as plant


class PlantTest(unittest.TestCase):
    def test_finite_plane_rejects_an_overhanging_footprint(self):
        axes = np.vstack([np.eye(3), -np.eye(3)])
        solid = ("tread", np.full(3, -1.0), np.ones(3), axes, np.ones(6))
        self.assertIsNone(plant.project_top([1.08, 0.0], solid))
        self.assertIsNone(plant.project_top([0.99, 0.0], solid, 0.05))
        point, normal = plant.project_top([0.55, 0.4], solid, 0.07)
        np.testing.assert_allclose(point, [0.55, 0.4, 1.0])
        np.testing.assert_allclose(normal, [0.0, 0.0, 1.0])

    def test_fixed_lengths_and_stationary_soles_on_a_sloping_tread(self):
        n = 10
        hip = np.column_stack(
            [np.linspace(-0.06, 0.06, n), np.zeros(n), np.full(n, 0.81)]
        )
        knee = hip + [0.0, 0.0, -0.45]
        tip = knee + [0.0, 0.0, -0.45]
        corners = np.array(
            [[x, y, 0.0] for x in [-0.055, 0.055] for y in [-0.06, 0.06]]
        )
        high = np.concatenate([hip[:, None] + corners, knee[:, None] + corners], axis=1)
        low = np.concatenate([knee[:, None] + corners, tip[:, None] + corners], axis=1)
        points = tip[:, None] + corners
        normal = plant.unit(np.array([0.04, -0.025, 1.0]))
        data = (hip, knee, tip, high, low, points)
        valid, hi, lo, u, v = plant.solve_leg(
            data,
            np.zeros_like(hip),
            np.zeros_like(hip),
            normal,
            np.array([0.0, 1.0, 0.0]),
        )
        self.assertTrue(valid.all())
        np.testing.assert_allclose(np.linalg.norm(u, axis=1), 0.45, atol=0.00001)
        np.testing.assert_allclose(np.linalg.norm(v, axis=1), 0.45, atol=0.00001)
        np.testing.assert_allclose(lo[:, 4:].mean(1)[:, :2], 0.0, atol=0.00001)
        np.testing.assert_allclose(
            (lo[:, 4:] @ normal).min(1) / normal[2],
            plant.SOLE_CLEARANCE_M,
            atol=0.00002,
        )

    def test_antiparallel_and_nearly_antiparallel_swing(self):
        source = np.array([[0.0, 0.0, -1.0]] * 2)
        target = plant.unit(np.array([[0.0, 0.0, 1.0], [0.00001, 0.0, 1.0]]))
        cloud = np.broadcast_to([[0.0, 0.0, -1.0], [0.1, 0.2, 0.3]], (2, 2, 3))
        result = plant.swing(cloud, source, target)
        np.testing.assert_allclose(result[:, 0], target, atol=1e-9)
        np.testing.assert_allclose(
            np.linalg.norm(result[:, 0] - result[:, 1], axis=1),
            np.linalg.norm(cloud[:, 0] - cloud[:, 1], axis=1),
        )

    def test_footprint_spans_a_shared_seam_but_detects_an_open_edge(self):
        def tile(label, x0, x1):
            lo = np.array([x0, -1, -1.0])
            hi = np.array([x1, 1, 0.0])
            return label, lo, hi, np.vstack((np.eye(3), -np.eye(3))), np.r_[hi, -lo]

        toe = np.array(
            [
                [-0.1, -0.1, 0.002],
                [0.1, -0.1, 0.001],
                [0.1, 0.1, 0.003],
                [-0.1, 0.1, 0.004],
            ]
        )
        plane = np.zeros(3)
        normal = np.array([0.0, 0.0, 1.0])
        area, lowest, _ = plant.footprint(
            toe, plane, normal, [tile("A", -1, 0), tile("B", 0, 1)]
        )
        self.assertAlmostEqual(area, 1.0)
        self.assertTrue(lowest)
        area, lowest, _ = plant.footprint(toe, plane, normal, [tile("A", -1, 0)])
        self.assertAlmostEqual(area, 0.5)
        self.assertFalse(lowest)

    def test_unreachable_surface_has_no_valid_pose(self):
        hip = np.array([[0.0, 0.0, 2.0]])
        knee = hip - [0, 0, 0.4]
        tip = knee - [0, 0, 0.4]
        data = hip, knee, tip, hip[:, None], tip[:, None], tip[:, None]
        valid, *_ = plant.solve_leg(
            data,
            np.zeros_like(hip),
            np.zeros_like(hip),
            np.array([0, 0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
        )
        self.assertFalse(valid.any())
