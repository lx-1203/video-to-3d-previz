import copy
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orderflow"))
from fullclip_scene_seating import (
    constrain_observed_seat_visibility,
    refine_observed_upholstery,
)
from fullclip_seat_profiles import _edge_family, _row_direction, _witnesses, neighbors
import cv2


class UpholsteryTests(unittest.TestCase):
    def test_seat_depth_refit_includes_upholstery_frame(self):
        from fullclip_guardrail_actor_visibility import SEATING

        self.assertTrue(
            {"Seat back", "Seat back frame", "Seat cushion", "Seat armrest", "Seat pedestal"}
            <= SEATING
        )

    def test_background_without_chair_bank_is_unchanged(self):
        room = [{"kind": "Recess wall surround", "vertices": [[0, 0, 1]] * 8}]
        expected = copy.deepcopy(room)
        self.assertEqual(
            refine_observed_upholstery(room, {}, {}, {}, {}, np.eye(3), {}, (120, 80)),
            {},
        )
        self.assertEqual(room, expected)

    def test_rail_direction_excludes_diagonal_alias(self):
        image = np.zeros((90, 140, 3), np.uint8)
        cv2.line(image, (0, 24), (139, 24), (180, 180, 180), 2)
        cv2.line(image, (0, 36), (139, 36), (180, 180, 180), 2)
        lines = _edge_family(image)
        size = np.array([12.0, 10.0])
        self.assertAlmostEqual(_row_direction(lines, (45.0, 30.0), size), 0, places=2)
        rect = np.array([[-6, -5], [6, -5], [6, 5], [-6, 5]])
        centers = np.array([[30, 30], [46, 30], [62, 30], [78, 30]])
        self.assertTrue(neighbors([(c, c + rect) for c in centers], centers, lines))
        diagonal = centers.copy()
        diagonal[:, 1] += np.arange(len(diagonal)) * 8
        self.assertFalse(neighbors([(c, c + rect) for c in diagonal], diagonal, lines))

    def test_obstructed_image_never_witnesses_hidden_cushion(self):
        rng = np.random.default_rng(8)
        image = rng.integers(0, 256, (64, 80, 3), np.uint8)
        image_frames = {n: image.copy() for n in (0, 6, 12)}
        intr = np.array([[1.0, 0.0, 0.5], [0.0, 1.0, 0.5], [0.0, 0.0, 1.0]])
        cams = {n: np.eye(4) for n in image_frames}
        args = (
            0,
            [[40.0, 32.0]],
            np.array([[0.0, 0.0, 3.0]]),
            np.array([[10.0, 10.0]]),
            image_frames,
            cams,
            intr,
        )
        self.assertEqual(_witnesses(*args, {}, (80, 64)), [[6, 12]])
        self.assertEqual(
            _witnesses(*args, {6: [[32, 24, 48, 40]], 12: [[32, 24, 48, 40]]}, (80, 64)),
            [[]],
        )

    def test_keep_tier_top_and_measured_window(self):
        verts = [[x, y, z] for z in (1, 3) for y in (-0.5, 0.5) for x in (-1, 1)]
        tier = dict(kind="Stepped seating platform", vertices=verts, source_frame=0, seat_row=2)
        window = dict(
            kind="Recess wall surround",
            vertices=copy.deepcopy(verts),
            source_frame=0,
            observation_status="measured",
        )
        data = {"observations": [{"frame": 0, "views": [6, 12], "target_world": [0.5, 0.48, 2.4]}]}
        cams = {n: np.eye(4) for n in (0, 6, 12)}
        audit = constrain_observed_seat_visibility([tier, window], data, cams, np.eye(3), (80, 64))
        self.assertEqual(len(audit), 1)
        self.assertGreater(audit[0]["cleared_rays"], 0)
        np.testing.assert_allclose(np.array(tier["vertices"])[4:, 2], [3.0] * 4)
        self.assertEqual(window["vertices"], verts)
