"""Local contour, source-frame, identity and pose regressions for edge extensions."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow import fullclip_people_partial as partial
from orderflow.fullclip_people_partial_pose import _sample


class PartialBodyTest(unittest.TestCase):
    def test_uniform_background_rejected(self):
        image = np.full((140, 220, 3), 89, np.uint8)
        self.assertIsNone(partial._foreground(image, np.array([70, 35, 123, 109])))

    def test_compact_contrast_recovers_visible_body(self):
        image = np.full((150, 220, 3), (45, 105, 173), np.uint8)
        cv2.ellipse(image, (97, 64), (21, 19), 0, 0, 360, (36, 40, 47), -1)
        cv2.rectangle(image, (83, 64), (106, 108), (36, 40, 47), -1)
        box, xy = partial._foreground(image, np.array([71, 40, 124, 114]))
        self.assertGreater(len(xy), 600)
        visible = partial._outline(xy)
        self.assertGreater(visible["visible_bbox"][1], 38)
        self.assertLess(box[2], 124)
        self.assertEqual(len(visible["outline_profile"]), 8)

    def test_furniture_color_cannot_replace_a_learned_coat(self):
        a = np.full((140, 230, 3), 150, np.uint8)
        b = a.copy()
        cv2.rectangle(a, (85, 35), (125, 101), (35, 31, 30), -1)
        cv2.rectangle(b, (85, 35), (125, 101), (12, 100, 205), -1)
        bbox = np.array([79, 30, 132, 105])
        _, c = partial._foreground(a, bbox)
        self.assertIsNone(partial._foreground(b, bbox, partial._clothing(a, c)))

    def test_existing_actor_and_overlap_block_duplicate(self):
        row = {"actors": [{"track_id": "one", "bbox": [10, 20, 80, 120]}]}
        self.assertFalse(partial._available(row, np.array([18, 25, 66, 100]), "two"))
        self.assertFalse(partial._available(row, np.array([140, 20, 180, 80]), "one"))

    def test_relocalization_must_reproduce_overlap(self):
        seed = {"track_id": "p"}

        def rec(root, f):
            x = 10 if root == 20 else 80
            return (
                f,
                {
                    "bbox": [x, 8, x + 15, 24],
                    "partial_body_track": {"state": "local_person_and_contour"},
                },
            )

        def march(images, rows, start, end, a, det, pose):
            return [rec(start, f) for f in range(start - 1, 10 if start == 20 else 3, -1)]

        with patch.object(partial, "_march", march):
            out = partial._extend_edge({}, [], 20, 4, seed, None, None)
        self.assertEqual(min(f for f, a in out), 11)

    def test_discovers_same_moving_person_from_confirmed_detector_endpoint(self):
        def draw(frame):
            image = np.full((145, 210, 3), (45, 105, 173), np.uint8)
            x = 83 + frame
            cv2.ellipse(image, (x + 10, 58), (17, 15), 0, 0, 360, (28, 36, 39), -1)
            cv2.rectangle(image, (x, 58), (x + 19, 101), (28, 36, 39), -1)
            cv2.line(image, (x, 65), (x + 16, 71), (54, 60, 63), 2)
            cv2.line(image, (x + 4, 85), (x + 16, 83), (65, 65, 69), 2)
            return image

        class LocalDetector:
            def detect(self, rgb, **kwargs):
                ys, xs = np.nonzero(np.max(rgb, axis=2) < 75)
                if len(xs) < 10:
                    return []
                x0, y0, x1, y1 = map(float, (xs.min(), ys.min(), xs.max(), ys.max()))
                return [
                    dict(class_id=0, score=0.82, bbox=[x0, y0, x1, y1]),
                    dict(class_id=1, score=0.74, bbox=[x0, y0, x1, y0 + (y1 - y0) * 0.45]),
                ]

        class Pose:
            def measure(self, rgb, box):
                return {str(k): dict(u=90.0, v=76.0, score=0.7) for k in range(17)}

        images = {i: draw(i) for i in range(8)}
        rows = [dict(shot_id="one", actors=[]) for _ in images]
        out = partial._march(
            images,
            rows,
            7,
            0,
            dict(track_id="stable", bbox=[78, 38, 124, 108]),
            LocalDetector(),
            Pose(),
        )
        self.assertEqual({f for f, a in out}, set(range(7)))
        self.assertEqual({a["track_id"] for f, a in out}, {"stable"})

    def test_chair_track_with_plausible_pose_but_no_human_detector_is_rejected(self):
        # The black seat has the same learned colour and a stable motion path.
        # The pose estimator alone must never turn it into an actor.
        image = np.full((134, 210, 3), (56, 100, 166), np.uint8)
        for x in (10, 72, 134):
            cv2.rectangle(image, (x, 44), (x + 45, 96), (28, 33, 37), -1)
            cv2.line(image, (x, 48), (x + 45, 48), (60, 63, 66), 2)

        class NoPerson:
            def detect(self, im, **kwargs):
                return []

        class OptimisticPose:
            def measure(self, im, b):
                return {str(i): dict(u=95, v=65, score=0.98) for i in range(17)}

        images = {i: image.copy() for i in range(10)}
        rows = [dict(shot_id="scene", actors=[]) for _ in images]
        seed = dict(track_id="known", bbox=[66, 37, 121, 103])
        self.assertEqual(
            partial._march(images, rows, 0, 9, seed, NoPerson(), OptimisticPose()), []
        )

    def test_two_simultaneously_observed_people_remain_separate_in_the_same_shot(self):
        people = [
            dict(track_id="upper", bbox=[18, 8, 68, 63]),
            dict(track_id="lower", bbox=[98, 77, 143, 129]),
        ]
        row = dict(shot_id="same", actors=people)
        self.assertFalse(partial._available(row, np.array([20, 9, 65, 60]), "lower"))
        self.assertFalse(partial._available(row, np.array([100, 79, 141, 126]), "upper"))
        row_without_lower = dict(row, actors=[people[0]])
        self.assertTrue(
            partial._available(row_without_lower, np.array([100, 79, 141, 126]), "lower")
        )
        self.assertEqual([p["track_id"] for p in row["actors"]], ["upper", "lower"])

    def test_cache_capacity_scales_with_image_bytes(self):
        rows = [{"pts": 0}]
        small = partial._Window(None, rows, 0, 0, 1, 640, 360)
        large = partial._Window(None, rows, 0, 0, -1, 4096, 2160)
        self.assertLessEqual(small.capacity, 20)
        self.assertLess(large.capacity, small.capacity)
        self.assertLessEqual(large.capacity * 4096 * 2160 * 3, 100 * 1024**2)

    def test_upright_face_is_not_bowed_pose(self):
        def joint(x, y):
            return dict(u=x, v=y, score=0.9)

        js = {
            "left_shoulder": joint(38, 45),
            "right_shoulder": joint(17, 45),
            "left_hip": joint(35, 75),
            "right_hip": joint(20, 75),
            "nose": joint(28, 17),
        }
        actor = dict(joints2d=js, partial_body_track={"visible_bbox": [8, 13, 50, 84]})
        self.assertIsNone(_sample(actor))
        js["nose"] = joint(31, 50)
        self.assertIsNotNone(_sample(actor))


if __name__ == "__main__":
    unittest.main()
