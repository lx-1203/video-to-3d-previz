"""Synthetic foreground/background ordering and rigid multiview guardrail regressions."""

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_guardrail import measure_image
from orderflow.fullclip_guardrail_fit import fit
from orderflow.fullclip_guardrail_gauge import gauge_for_samples
from orderflow.fullclip_guardrail_panel import observe_profile, fit_edge
from orderflow.fullclip_guardrail_clearance import capsule_bounds


def fixture(mode):
    img = np.full((280, 480, 3), (48, 56, 62), np.uint8)
    slope = -0.08

    def y(x):
        return round(181 + x * slope)

    def rail(lo, hi):
        cv2.line(img, (lo, y(lo)), (hi, y(hi)), (155, 159, 157), 4, cv2.LINE_AA)

    if mode in ("behind", "crease"):
        rail(14, 467)
    cv2.rectangle(img, (150, 64), (262, 240), (23, 20, 19), -1)
    if mode == "front":
        rail(14, 467)
    if mode == "crease":
        # A differently shaded short fold inside the silhouette does not connect the flanks.
        cv2.line(img, (180, y(180) + 9), (224, y(224) + 10), (105, 105, 105), 2, cv2.LINE_AA)

    def point(u, v):
        return dict(u=u, v=v, score=0.9, visibility="visible")

    js = {
        side + "_" + n: point(u, v)
        for side, u in (("left", 179), ("right", 233))
        for n, v in (("shoulder", 88), ("hip", 174))
    }
    return img, [dict(track_id="actor", joints2d=js)]


class GuardrailTest(unittest.TestCase):
    def test_front_versus_interrupted_and_crease(self):
        cv2.setNumThreads(1)
        detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        for mode in ("front", "behind", "crease"):
            with self.subTest(mode=mode):
                im, people = fixture(mode)
                lines = measure_image(im, people, detector)
                self.assertEqual(bool(lines), mode == "front")
                if lines:
                    line = lines[0]
                    self.assertLess(abs(line["slope"] + 0.08), 0.025)
                    self.assertLess(abs(line["intercept"] - 181), 4.0)
                    self.assertGreater(line["x_max"] - line["x_min"], 400)
                    # A real rail crossing an actor above a featureless floor
                    # provides no source evidence for a connected solid fascia.
                    people[0]["bbox"] = [150, 64, 262, 240]
                    self.assertIsNone(observe_profile(im, line, people, detector))

    def test_finite_segment_bounds(self):
        ends = np.array([[1.0, 1.0, 0.0], [5.0, 1.0, 0.0]])
        query = np.array([[0.0, 1.0, 0.0], [3.0, 1.0, 0.25], [6.0, 2.0, 0.0]])
        np.testing.assert_allclose(capsule_bounds(ends, query), [1, 0.25, 2**0.5])

    def test_rigid_multiview_and_depth(self):
        w, h = 640, 360
        k = np.array([[500.0, 0, w / 2], [0, 500.0, h / 2], [0, 0, 1]])
        a = np.array([-2.0, -0.20, 3.2, 1.0])
        b = np.array([2.0, 0.30, 5.3, 1.0])
        views = []
        for i in range(20):
            center = np.array([-0.4 + i * 0.045, 0.10 * np.sin(i * 0.2), -0.3 + i * 0.017])
            proj = k @ np.column_stack([np.eye(3), -center])
            aa = proj @ a
            bb = proj @ b
            aa /= aa[2]
            bb /= bb[2]
            line = np.cross(aa, bb)
            line /= line[1]
            plane = line @ proj
            plane /= np.linalg.norm(plane[:3])
            u = (aa[0] + bb[0]) * 0.5
            row = dict(
                x_min=float(aa[0]),
                x_max=float(bb[0]),
                slope=-line[0],
                intercept=-line[2],
                thickness=4,
                crossings=[dict(u=u)],
            )
            views.append(dict(row=row, plane=plane, projection=proj, front=[(u, 7.0)], fx=500))
        result = fit(views, w, h)
        self.assertIsNotNone(result)
        self.assertLess(result["p90_px"], 0.01)
        self.assertEqual(result["front_fraction"], 1)
        self.assertGreater(result["radius"], 0)
        direction = b[:3] - a[:3]
        for endpoint in result["ends"]:
            self.assertLess(
                np.linalg.norm(np.cross(endpoint - a[:3], direction)) / np.linalg.norm(direction),
                0.002,
            )
        self.assertIsNone(fit([dict(s, front=[]) for s in views], w, h))
        calibrated = []
        conflict = []
        for q in views:
            u, depth = q["front"][0]
            calibrated.append(dict(q, front=[(u - 4, depth), (u + 4, depth)]))
            conflict.append(dict(q, front=[(u - 4, 1.9), (u + 4, 1.9)]))
        self.assertIsNone(gauge_for_samples(calibrated, w, h))
        factor = gauge_for_samples(conflict, w, h)
        self.assertIsNotNone(factor)
        self.assertLess(factor["factor"], 0.65)
        self.assertGreater(factor["front_fraction_after"], 0.95)
        self.assertIsNone(gauge_for_samples(conflict[:6], w, h))
        self.assertIsNone(fit_edge(calibrated, result["ends"], "top", h))


if __name__ == "__main__":
    unittest.main()
