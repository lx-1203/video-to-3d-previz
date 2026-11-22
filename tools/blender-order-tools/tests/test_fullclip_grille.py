import unittest
import numpy as np
import cv2
from orderflow.fullclip_grille import measure


def sequence(*, vertical=True, horizontal=True, dark=False, moving=False):
    frames = {}
    for i in range(11):
        value = 180 if dark else 48
        image = np.full((120, 210), value, np.uint8)
        cv2.circle(image, (70 + 3 * i, 64), 24, 145 if not dark else 92, -1)
        ink = 48 if dark else 175
        shift = i * 6 if moving else 0
        if vertical:
            for x in (19, 51, 83, 115, 147, 179):
                cv2.rectangle(image, (x - 3 + shift, 0), (x + 3 + shift, 119), ink, -1)
        if horizontal:
            for y in (18, 46, 74, 102):
                cv2.rectangle(image, (0, y - 2 + shift), (209, y + 2 + shift), ink, -1)
        frames[i] = cv2.cvtColor(cv2.GaussianBlur(image, (5, 5), 0.85), cv2.COLOR_GRAY2BGR)
    return frames


class GrilleTests(unittest.TestCase):
    def test_measures_both_edges_once_for_each_member(self):
        for dark in (False, True):
            with self.subTest(dark=dark):
                columns, rows = measure(sequence(dark=dark))
                np.testing.assert_allclose(
                    [r["center"] for r in columns], [19, 51, 83, 115, 147, 179], atol=1.1
                )
                np.testing.assert_allclose(
                    [r["center"] for r in rows], [18, 46, 74, 102], atol=1.1
                )
                np.testing.assert_allclose([r["width"] for r in columns], 7, atol=2)
                np.testing.assert_allclose([r["width"] for r in rows], 5, atol=2)

    def test_isolated_parallel_lines_and_transient_grids_do_not_create_a_grille(self):
        for options in (
            {"vertical": False},
            {"horizontal": False},
            {"vertical": False, "horizontal": False},
            {"moving": True},
        ):
            self.assertIsNone(measure(sequence(**options)), options)

    def test_closed_cross_members_touch_uprights_with_independent_widths(self):
        from orderflow.fullclip_scene_surfaces import _grille_solids, _projector

        frames = sequence()
        families = measure(frames)
        families[0][0]["width"] *= 1.5
        image = frames[0]
        height, width = image.shape[:2]
        intr = np.array([[1.3, 0, 0.5], [0, 1.8, 0.5], [0, 0, 1]])
        project = _projector(np.eye(4), intr, width, height)
        parts = _grille_solids(families, [{"axial_depth": 3}], None, project, intr, image, 0)
        self.assertEqual(len(parts), 10)
        for part in parts:
            self.assertEqual(len(part["vertices"]), 8)
            self.assertEqual(len(part["faces"]), 6)
        back_depths = [np.array(p["vertices"])[4, 2] for p in parts if p["axis"] == "vertical"]
        front_depths = [np.array(p["vertices"])[0, 2] for p in parts if p["axis"] == "horizontal"]
        np.testing.assert_allclose(back_depths, front_depths[0], atol=1e-5)
        np.testing.assert_allclose(front_depths, back_depths[0], atol=1e-5)
