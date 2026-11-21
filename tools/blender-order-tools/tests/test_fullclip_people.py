"""Metric and uncertainty contracts for full-clip local pose observations."""

import importlib.util
from pathlib import Path
import sys
import unittest

PKG = Path(__file__).resolve().parents[1] / "orderflow"


class PeopleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(
            importlib.util.find_spec(n)
            for n in ("numpy", "cv2", "av", "rtmlib", "onnxruntime", "scipy")
        ):
            raise unittest.SkipTest("Local pose runtime required")
        sys.path.insert(0, str(PKG))
        import numpy as np
        import fullclip_people_faces as faces
        import fullclip_people_solve as solve
        import fullclip_people_tracks as tracks
        import fullclip_people_chroma as chroma

        cls.np, cls.faces, cls.solve, cls.tracks, cls.chroma = np, faces, solve, tracks, chroma

    @classmethod
    def tearDownClass(cls):
        if str(PKG) in sys.path:
            sys.path.remove(str(PKG))

    def base(self, box=(40, 20, 90, 120)):
        return {
            "body_id": 2,
            "bbox_kind": "person_detection",
            "detector_score": 0.8,
            "bbox": list(box),
            "joints2d": self.tracks.empty_joints(),
        }

    def test_occluded_limb_holds_and_every_segment_has_template_length(self):
        profile = self.solve.read_profile(None)
        seq = [(n, self.base()) for n in range(3)]
        for n, a in seq[:1]:
            for k, uv in (
                ("left_shoulder", (50, 40)),
                ("left_elbow", (45, 50)),
                ("left_wrist", (35, 55)),
            ):
                a["joints2d"][k] = {
                    "u": uv[0],
                    "v": uv[1],
                    "score": 0.87,
                    "visibility": "model_estimate",
                }
        self.solve.solve_track(seq, profile)
        for n, a in seq:
            j = a["joints3d"]
            for side in ("left", "right"):
                for parent, child, dim in (
                    ("shoulder", "elbow", "arm"),
                    ("elbow", "wrist", "forearm"),
                    ("hip", "knee", "thigh"),
                    ("knee", "ankle", "shin"),
                ):
                    v = self.np.subtract(j[side + "_" + child], j[side + "_" + parent])
                    self.assertAlmostEqual(self.np.linalg.norm(v), profile[dim], places=5)
            if n:
                self.assertEqual(a["joint3d_state"]["left_elbow"], "held_direction")
                self.assertEqual(a["joints2d"]["left_elbow"]["visibility"], "unknown")

    def test_head_translation_reprojects_even_for_cropped_skeleton(self):
        actor = self.base()
        actor["head_detection"] = {"bbox": [200, 300, 530, 590], "score": 0.77}
        self.solve.solve_track([(0, actor)], self.solve.read_profile(None))
        x, _, z = actor["joints3d"]["head"]
        uv = self.np.array(actor["root_uv"]) + self.np.array([x, -z]) * actor["pixels_per_meter"]
        self.np.testing.assert_allclose(uv, [365, 445], atol=0.01)
        self.assertEqual(actor["anchors"]["shoulder"]["score"], 0)

    def test_rotated_crop_corners_go_back_to_source(self):
        a = self.np.arange(7 * 9).reshape(7, 9)
        for k in range(4):
            img = self.np.rot90(a, k)
            for y, x in ((0, 0), (img.shape[0] - 1, img.shape[1] - 1)):
                u, v = self.faces.undo_rotation([[x, y]], k, 9, 7)[0].astype(int)
                self.assertEqual(img[y, x], a[v, u])

    def test_tracker_restarts_at_cut_and_never_certifies_identity(self):
        rows = [{"shot_id": s, "actors": [self.base()]} for s in ("S01", "S01", "S02")]
        self.tracks.assign(rows)
        ids = [r["actors"][0]["track_id"] for r in rows]
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], ids[2])

    def test_transient_inverted_face_roll_keeps_the_measured_shot_axis(self):
        rows = []
        for i in range(11):
            a = self.base((0, 0, 90, 120))
            a["bbox_kind"] = "face_detection"
            eyes = 50 if i != 6 else 75
            mouth = 80 if i != 6 else 45
            a["face"] = {
                "bbox": [0, 0, 90, 120],
                "score": 0.66 if i == 6 else 0.92,
                "landmarks": [[25, eyes], [65, eyes], [45, 60], [32, mouth], [55, mouth]],
            }
            rows.append({"source_frame": i, "shot_id": "S01", "actors": [a]})
        self.solve.stabilize_close_roll(rows, 150)
        self.solve.solve_track(
            [(i, r["actors"][0]) for i, r in enumerate(rows)], self.solve.read_profile(None)
        )
        self.assertLess(rows[6]["actors"][0]["orientation"]["face_up_uv"][1], -0.9)

    def test_back_or_down_keeps_observed_torso_side_and_marks_depth_inference(self):
        a = self.base()
        a["head_detection"] = {"bbox": [150, 30, 250, 170], "score": 0.7}
        a["shoulder_support"] = {
            "up_uv": [0.95, -0.312],
            "evidence": "sequence of head and shoulder image regions",
        }
        self.solve.solve_track([(0, a)], self.solve.read_profile(None))
        self.assertGreater(a["orientation"]["torso_up_xyz"][0], 0.92)
        self.assertGreater(a["orientation"]["head_forward_xyz"][1], 0.6)
        self.assertEqual(a["orientation"]["facing_state"], "back_or_down_inferred")
        self.assertEqual(a["orientation"]["face_score"], 0.0)

    def test_unseen_limbs_of_bent_actor_follow_gravity_and_shirt_cue(self):
        a = self.base()
        a["head_detection"] = {"bbox": [150, 30, 250, 170], "score": 0.7}
        a["shoulder_support"] = {
            "up_uv": [0.985, 0.173],
            "evidence": "head and torso image regions",
        }
        a["posture_priors"] = {
            "gravity_xyz": [0, 0, -1],
            "sleeves": {"right_wrist": {"uv": [0.81, 0.59], "frames": [0, 1, 2]}},
        }
        self.solve.solve_track([(0, a)], self.solve.read_profile(None))
        j = a["joints3d"]
        upper = self.np.subtract(j["left_elbow"], j["left_shoulder"])
        fore = self.np.subtract(j["right_wrist"], j["right_elbow"])
        thigh = self.np.subtract(j["left_knee"], j["left_hip"])
        self.assertLess(upper[2] / self.np.linalg.norm(upper), -0.9)
        self.assertLess(thigh[2] / self.np.linalg.norm(thigh), -0.9)
        self.assertGreater(fore[0] / self.np.linalg.norm(fore), 0.45)
        self.assertEqual(a["joint3d_state"]["left_elbow"], "gravity_direction_inferred")
        self.assertEqual(a["joint3d_state"]["right_wrist"], "nearby_sleeve_direction_inferred")

    def test_occluded_mode_switch_vs_image_supported_rapid_motion(self):
        import fullclip_people_temporal as motion

        n = 33
        weak = self.np.zeros(n, dtype=bool)
        weak[[0, -1]] = True  # Direct observations bracket the ambiguous gap.
        raw = self.np.zeros((n, 2))
        raw[16:, 0] = 190
        no_motion = self.np.zeros_like(raw)
        sizes = self.np.full(n, 250.0)
        corrected, intervals = motion.regularize_occluded_branches(
            raw, no_motion, sizes, weak, self.np.full(n, 0.7)
        )
        self.assertTrue(intervals)
        self.assertLess(max(abs(self.np.diff(corrected[:, 0]))), 25)
        actual = no_motion.copy()
        actual[16, 0] = 190
        unmodified, ranges = motion.regularize_occluded_branches(
            raw, actual, sizes, weak, self.np.full(n, 0.9)
        )
        self.assertFalse(ranges)
        self.np.testing.assert_allclose(unmodified, raw)
        a, first = motion.regularize_occluded_branches(
            raw[:16], no_motion[:16], sizes[:16], weak[:16], self.np.ones(16)
        )
        b, second = motion.regularize_occluded_branches(
            raw[16:], no_motion[16:], sizes[16:], weak[16:], self.np.ones(17)
        )
        self.assertFalse(first or second)
        self.assertEqual(b[0, 0] - a[-1, 0], 190)

    def test_gap_requires_subject_to_stay_visible_between_observations(self):
        import fullclip_people_gaps as gaps

        rng = self.np.random.default_rng(8)
        sprite = rng.integers(40, 225, (96, 48, 3), dtype=self.np.uint8)
        frames = []
        for x in (24, 26, 28, 30):
            frame = self.np.zeros((130, 126, 3), dtype=self.np.uint8)
            frame[18:114, x : x + 48] = sprite
            frames.append(frame)
        start = [24, 18, 72, 114]
        end = [30, 18, 78, 114]
        evidence = gaps.verify_gap(frames, start, end)
        self.assertEqual(len(evidence), 2)
        gone = frames.copy()
        gone[1] = self.np.zeros_like(frames[1])
        self.assertIsNone(gaps.verify_gap(gone, start, end))
        self.assertIsNone(gaps.verify_gap(frames, start, [104, 18, 152, 114]))

    def test_foreground_colour_rejects_blue_distractor(self):
        bg = [95, 60, 30]
        images = []
        for x in (55, 72, 90):
            image = self.np.full((80, 160, 3), bg, self.np.uint8)
            image[24:56, x - 18 : x + 18] = [43, 58, 83]
            images.append(image)
        seq = [
            (n, {"face": {"bbox": [x - 18, 24, x + 18, 56], "score": 0.9}})
            for n, x in ((0, 55), (2, 90))
        ]
        lut = self.chroma.learn(images, seq, 1.0)
        estimate = self.chroma.localization(images, 1, lut, [51, 24, 87, 56], 1.0)
        self.assertIsNotNone(estimate)
        self.assertAlmostEqual((estimate[0][0] + estimate[0][2]) / 2, 72, delta=4)


if __name__ == "__main__":
    unittest.main()
