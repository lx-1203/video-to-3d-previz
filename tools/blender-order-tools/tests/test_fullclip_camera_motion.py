"""Source-motion regressions for independently processed camera shots."""

import importlib.util
from pathlib import Path
import sys
import unittest


@unittest.skipUnless(
    all(importlib.util.find_spec(k) for k in ("cv2", "numpy", "scipy")),
    "requires local scene runtime",
)
class CameraMotionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import cv2
        from scipy.spatial.transform import Rotation

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orderflow"))
        import fullclip_camera_motion

        cls.np, cls.cv, cls.R, cls.motion = np, cv2, Rotation, fullclip_camera_motion

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(Path(__file__).resolve().parents[1] / "orderflow"))

    def source(self, n=22, seed=24):
        np, cv = self.np, self.cv
        w, h = 240, 150
        rng = np.random.default_rng(seed)
        tx = cv.GaussianBlur(rng.integers(0, 255, (800, 800), np.uint8), (3, 3), 0.7)
        yy, xx = np.mgrid[:h, :w]
        rays = np.stack(((xx - w / 2) / 210, (yy - h / 2) / 210, np.ones_like(xx)), axis=2)
        frames, truth, prior, deps = {}, {}, {}, {}
        for i in range(n):
            # Real simultaneous pan and forward movement. A brief extra push is
            # visible in the source and must survive the stabilization.
            dx = i * 0.013 + max(0, min(i - 8, 3)) * 0.010
            r = self.R.from_rotvec([0, 0.003 * i, 0]).as_matrix()
            pos = np.array([dx, 0, 0.014 * i])
            world = rays @ r.T
            d_near = (3 - pos[2]) / world[:, :, 2]
            near = world[:, :, 0] * d_near + pos[0] < -0.10
            dep = (np.where(near, 3.0, 7.0) - pos[2]) / world[:, :, 2]
            pts = world * dep[:, :, None] + pos
            u = (400 + pts[:, :, 0] * 73).astype("float32")
            v = (400 + pts[:, :, 1] * 73).astype("float32")
            gray = cv.remap(tx, u, v, cv.INTER_LINEAR, borderMode=cv.BORDER_REFLECT)
            frames[i] = cv.cvtColor(gray, cv.COLOR_GRAY2BGR)
            pose = np.eye(4)
            pose[:3, :3] = r
            pose[:3, 3] = pos
            truth[i] = pose
            noisy = pose.copy()
            if i:
                noisy[:3, 3] += [0, (-1) ** i * 0.072, 0]
                noisy[:3, :3] = r @ self.R.from_rotvec([(-1) ** i * 0.012, 0, 0]).as_matrix()
            prior[i] = noisy
            if i in (0, n // 2, n - 1):
                deps[i] = dep
        return (
            frames,
            truth,
            prior,
            deps,
            np.array([[210 / w, 0, 0.5], [0, 210 / h, 0.5], [0, 0, 1.0]]),
        )

    def test_real_pan_and_dolly_survive_jitter_repair(self):
        np = self.np
        frames, truth, prior, deps, K = self.source()
        got, details = self.motion.stabilize(prior, frames, deps, {}, (240, 150), K, {})
        n = max(prior)

        def error(poses):
            return np.mean([abs(poses[i][1, 3] - truth[i][1, 3]) for i in range(1, n)])

        self.assertLess(error(got), 0.32 * error(prior))
        self.assertGreater(got[n][0, 3], truth[n][0, 3] * 0.65)
        self.assertGreater(got[n][2, 3], truth[n][2, 3] * 0.65)
        pan = self.R.from_matrix(got[n][:3, :3]).as_rotvec()[1]
        self.assertAlmostEqual(pan, 0.003 * n, delta=0.018)
        np.testing.assert_allclose(got[0], truth[0], atol=1.0e-10)
        self.assertTrue(all(v["stabilization"]["inliers"] >= 10 for v in details.values()))

    def test_masked_or_textureless_frames_make_no_camera_claim(self):
        np = self.np
        frames, truth, prior, deps, K = self.source(9)
        for masked in (False, True):
            clip = frames if masked else {i: np.zeros_like(im) for i, im in frames.items()}
            boxes = {i: [[0, 0, 240, 150]] for i in frames} if masked else {}
            got, diagnostics = self.motion.stabilize(prior, clip, deps, boxes, (240, 150), K, {})
            for i, p in prior.items():
                np.testing.assert_array_equal(got[i], p)
            self.assertTrue(
                all(
                    d["stabilization"]["status"] == "insufficient_static_evidence"
                    for d in diagnostics.values()
                )
            )

    def test_separate_ranges_keep_new_camera_gauge(self):
        np = self.np
        frames, truth, prior, deps, K = self.source(8)
        views = deps.copy()
        seq = frames.copy()
        cams = prior.copy()
        for i, im in frames.items():
            key = i + 20
            seq[key] = im
            cams[key] = prior[i].copy()
            cams[key][:3, 3] += [8.0, 0, 4.0]
            if i in deps:
                views[key] = deps[i]
        got, _ = self.motion.stabilize(cams, seq, views, {}, (240, 150), K, {})
        np.testing.assert_allclose(got[20], cams[20], atol=1e-9)

    def test_offscreen_person_does_not_erase_background(self):
        mask = self.motion._mask((150, 240), [[-80, -90, -10, -20]], (240, 150))
        self.assertTrue((mask == 255).all())

    def test_visible_scene_cut_breaks_consecutive_frame_indices(self):
        frames, _, prior, deps, K = self.source(8)
        second, _, poses, depths, _ = self.source(8, seed=9125)
        for i, image in second.items():
            j = i + 8
            frames[j] = 255 - image
            prior[j] = poses[i].copy()
            prior[j][:3, 3] += [1.9, 5.0, 0.0]
            if i in depths:
                deps[j] = depths[i]
        got, details = self.motion.stabilize(prior, frames, deps, {}, (240, 150), K, {})
        self.np.testing.assert_allclose(got[8], prior[8], atol=1.0e-9)
        self.assertEqual(details[8]["stabilization"]["status"], "insufficient_static_evidence")

    def test_rotation_longer_than_half_turn_keeps_continuous_log(self):
        np = self.np
        last = np.zeros(3)
        for theta in np.linspace(0, 2.15 * np.pi, 100):
            principal = self.R.from_rotvec([0, theta, 0]).as_rotvec()
            cont = self.motion._continuous_log(principal, last)
            self.assertLess(np.linalg.norm(cont - last), 0.1)
            self.assertAlmostEqual(cont[1], theta, places=6)
            last = cont


if __name__ == "__main__":
    unittest.main()
