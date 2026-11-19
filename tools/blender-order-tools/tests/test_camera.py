import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.camera import make_plan


class CameraPlanTests(unittest.TestCase):
    def setUp(self):
        self.shot = {"shot_id": 2, "frame_range": [30, 35], "f_px": 1280}
        self.document = {
            "schema": "camera_solve/v1",
            "meta": {"source_video": "test.mp4", "width_px": 1280, "height_px": 720,
                     "fps": 30, "frame_index_base": 0},
            "shots": [self.shot],
        }
        self.settings = dict(shot_id=2, reference_name="test.mp4", scene_start=101,
                             frame_range=(101, 200), fps=Fraction(24), source_start=1,
                             aspect=16 / 9, sensor=36, anchor=50)

    def plan(self, **overrides):
        return make_plan(self.document, **(self.settings | overrides))

    def test_maps_source_offset_fps_and_clipped_shot(self):
        plan = self.plan(frame_range=(102, 105))
        self.assertEqual(plan.keys, ((102, 36), (105, 36)))

    def test_blocks_wrong_source_and_aspect(self):
        with self.assertRaisesRegex(ValueError, "参考视频"):
            self.plan(reference_name="another.mp4")
        with self.assertRaisesRegex(ValueError, "画幅"):
            self.plan(aspect=1)

    def test_conflicting_measurement_is_visible_but_blocked(self):
        self.shot["regime_reason"] = "measured_conflict"
        plan = self.plan()
        self.assertTrue(plan.keys)
        self.assertIn("冲突", plan.blocker)

    def test_relative_zoom_needs_an_anchor(self):
        self.shot.update(f_px=None, zoom={"usable": True},
                         f_curve={"frames": [30, 35], "ratio": [1, 2]})
        with self.assertRaisesRegex(ValueError, "基准焦距"):
            self.plan(anchor=None)
        plan = self.plan(anchor=35)
        self.assertEqual(plan.keys[0], (101, 35))
        self.assertAlmostEqual(plan.keys[-1][1], 70)

    def test_rejects_malformed_curve_and_nonoverlapping_range(self):
        self.shot["f_curve"] = {"frames": [35, 30], "ratio": [1, 2]}
        with self.assertRaisesRegex(ValueError, "有序"):
            self.plan()
        with self.assertRaisesRegex(ValueError, "没有重叠"):
            self.plan(frame_range=(106, 110))


if __name__ == "__main__":
    unittest.main()
