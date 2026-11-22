"""Source-only regression fixtures for the fixed handover mesh and its visibility gates."""

import sys
import unittest
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_handover import fit_handover_solids, shaded_outline, solid
from orderflow.fullclip_handover_pose import continuous_depths, fit_pose, rotations


class HandoverGeometryTest(unittest.TestCase):
    def test_shadow_stops_at_warm_hand(self):
        image = np.full((80, 100, 3), 10, np.uint8)
        image[25:37, 20:76] = [175, 190, 163]
        image[37:42, 20:76] = [83, 95, 82]
        image[42:48, 20:76] = [54, 70, 118]
        image[48:56, 20:76] = [83, 95, 82]
        sample = dict(
            polygon_uv=[[20, 25], [75, 25], [75, 36], [20, 36]],
            axes_px=[55, 11],
            contacts=[dict(track_id="a")],
        )
        polygon = shaded_outline(image, sample)
        self.assertGreater(cv2.pointPolygonTest(polygon.astype(np.float32), (50, 38), True), 0)
        self.assertLess(cv2.pointPolygonTest(polygon.astype(np.float32), (50, 44), True), 0)
        self.assertLess(cv2.pointPolygonTest(polygon.astype(np.float32), (50, 52), True), 0)

    def test_unsupported_sleeve_and_glare_do_not_invoke_a_video_pass(self):
        for actors, color in ((["a"], [171, 183, 173]), (["a", "b"], [237, 237, 237])):
            samples = [dict(contacts=[dict(track_id=a) for a in actors], rgb=color)] * 20
            track = dict(samples=samples, id="p")
            fit_handover_solids("unused-file", [track], {})
            self.assertNotIn("rigid_solid", track)

    def test_inconsistent_hand_depth_is_resolved_over_contiguous_frames_only(self):
        depth = np.r_[np.full(25, 3.6), np.full(25, 2.25), np.full(3, 6.0)]
        depth[8] = 2.2
        frames = np.r_[np.arange(50), np.arange(60, 63)]
        rays = np.tile([0, 0, -1.0], (len(depth), 1))
        correction = continuous_depths(rays * depth[:, None], rays, frames, 0.25, 30)
        final = depth + correction
        self.assertLessEqual(abs(np.diff(final[:50])).max(), 8 * 0.25 / 30 + 1e-6)
        self.assertGreater(final[8], 3.2)
        np.testing.assert_allclose(final[-3:], 6.0)

    def test_an_ambiguous_rotation_cannot_flip_the_solid_between_adjacent_frames(self):
        vertices = np.array(
            [
                [-0.5, -0.1, -0.05],
                [-0.5, 0.1, 0.05],
                [0.3, -0.22, -0.03],
                [0.5, 0.14, 0.03],
                [0.5, -0.09, 0.04],
            ]
        )
        previous = np.array([1.1, 0.9, -0.7])
        original = rotations([previous])[0]
        reversed_view = vertices @ rotations([[-1.8, -0.8, 0.8]])[0].T
        raster = cv2.convexHull(np.float32(reversed_view[:, :2] * [150, -150]))[:, 0]
        pose, _ = fit_pose(
            vertices,
            raster,
            1 / 150,
            np.zeros(2),
            3.0,
            1.0,
            previous,
            False,
            max_turn=0.18,
        )
        turn = np.arccos(np.clip((np.sum(rotations([pose])[0] * original) - 1) * 0.5, -1.0, 1.0))
        self.assertLessEqual(turn, 0.180001)

    def test_one_closed_solid_and_projection_at_different_angles(self):
        outline = np.array([[0, 2], [6, 0], [21, 4], [22, 8], [6, 12], [0, 11]], float)
        rows = [dict(contacts=[dict(track_id="a")])] * 10
        vertices, faces = solid([outline] * len(rows), rows)
        edges = Counter(
            tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1])
        )
        self.assertTrue(all(n == 2 for n in edges.values()))
        self.assertGreater(np.ptp(vertices[:, 2]), 0)
        old = vertices.copy()
        previous = None
        for angle in (0.1, 0.2, 0.35):
            rotated = vertices @ rotations([[0.15, 0.1, angle]])[0].T
            shape = cv2.convexHull(np.float32(rotated[:, :2] * [150, -150]))[:, 0]
            pose, shift = fit_pose(
                vertices, shape, 1 / 150, np.zeros(2), 3.0, 1.0, previous, False
            )
            fit = (vertices @ rotations([pose])[0].T)[:, :2] + shift
            self.assertLess(abs(np.ptp(fit[:, 0]) - np.ptp(shape[:, 0]) / 150), 0.035)
            previous = pose
        np.testing.assert_array_equal(old, vertices)


if __name__ == "__main__":
    unittest.main()
