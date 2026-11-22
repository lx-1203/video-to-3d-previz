import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow import fullclip_lower_tier as lower


class Object(dict):
    def __init__(self, name, **properties):
        super().__init__(**properties)
        self.name = name
        self.matrix_world = np.eye(4)
        self.location = np.zeros(3)
        self.scale = np.ones(3)
        self.parent = None
        self.hide_render = False
        self.keys = []

    def keyframe_insert(self, *, data_path, frame):
        self.keys.append((data_path, frame))


class Scene:
    def __init__(self, objects):
        self.objects = objects
        self.frame_current = 31

    def frame_set(self, value):
        self.frame_current = value


class Matrix:
    @staticmethod
    def Translation(value):
        m = np.eye(4)
        m[:3, 3] = value
        return m

    @staticmethod
    def Scale(value, dimension):
        return np.diag([value] * (dimension - 1) + [1])


class TierDepthTest(unittest.TestCase):
    def setUp(self):
        self.lower = Object("lower", shot_id=0, track_id="L")
        self.upper = Object("upper", shot_id=0, track_id="U")
        self.chair = Object("chair", seat_row=0, source_frame=0)
        self.other = Object("upper-chair", seat_row=1, source_frame=0)
        self.scene = Scene([self.lower, self.upper, self.chair, self.other])
        self.binding = {"editable_tracks": {"L": "lower", "U": "upper"}}
        self.modules = {
            "bpy": SimpleNamespace(
                data=SimpleNamespace(objects={x.name: x for x in self.scene.objects})
            ),
            "mathutils": SimpleNamespace(Matrix=Matrix, Vector=np.asarray),
        }
        self.view = (np.zeros(3), np.eye(3), np.array([200, 200]), np.array([160, 90]))
        self.frames = [
            {
                "source_frame": f,
                "shot_id": 0,
                "actors": [{"track_id": "U"}, {"track_id": "L"}],
            }
            for f in range(9)
        ]
        self.people = {"width": 320, "height": 180, "frames": self.frames}

    def test_no_partial_evidence_preserves_scene(self):
        with (
            patch.dict(sys.modules, self.modules),
            patch.object(lower, "_context") as context,
        ):
            self.assertEqual(lower.fit_lower_tier(self.scene, None, self.people, self.binding), {})
            context.assert_not_called()
        for o in self.scene.objects:
            np.testing.assert_equal(o.matrix_world, np.eye(4))
            self.assertEqual(o.keys, [])
        self.assertEqual(self.scene.frame_current, 31)

    def test_registered_lower_track_only_receives_translation_keys(self):
        for f in self.frames:
            f["actors"][1]["partial_body_track"] = {
                "association": "shot_local_source_extension",
                "visible_bbox": [75, 90, 110, 140],
            }

        def measured(scene, camera, rig, actor, frame, width, height):
            return {
                "f": frame,
                "root": np.zeros(3),
                "loc": np.array([0, 0, 15]),
                "view": self.view,
                "upper": [9.0, 9.1],
                "lower": [8.6],
                "desired": None,
                "uv": np.array([95, 100]),
            }

        with (
            patch.dict(sys.modules, self.modules),
            patch.object(
                lower,
                "_context",
                return_value=({(0, 0): [np.array([0, 0, 9])]}, [self.view, self.view]),
            ),
            patch.object(lower, "_measure", side_effect=measured),
        ):
            data = lower.fit_lower_tier(self.scene, None, self.people, self.binding)
        self.assertEqual(set(data), {"L"})
        self.assertEqual(len(self.lower.keys), 9)
        self.assertTrue(all(key[0] == "location" for key in self.lower.keys))
        self.assertGreater(np.linalg.norm(self.lower.location), 1)
        np.testing.assert_equal(self.lower.scale, np.ones(3))
        for ob in [self.upper, self.other, self.chair]:
            self.assertEqual(ob.keys, [])
            np.testing.assert_equal(ob.matrix_world, np.eye(4))
        self.assertEqual(self.scene.frame_current, 31)

    def test_source_shoulder_must_remain_within_observed_outline(self):
        box = np.array([10, 20, 30, 60])
        good = {"partial_body_track": {"shoulder_uv": [16, 24]}}
        bad = {"partial_body_track": {"shoulder_uv": [700, 900]}}
        np.testing.assert_equal(lower._source_shoulder(good, box), [16, 24])
        np.testing.assert_almost_equal(lower._source_shoulder(bad, box), [19.6, 30.8])


if __name__ == "__main__":
    unittest.main()
