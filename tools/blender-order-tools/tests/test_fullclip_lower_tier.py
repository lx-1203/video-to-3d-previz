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
        self.children = ()
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
            self.assertEqual(
                lower.fit_lower_tier(self.scene, None, self.people, self.binding), {}
            )
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

    def test_monocular_tier_scale_preserves_other_storey_and_measured_floor(self):
        protected = Object(
            "landing", reconstruction_kind="Room floor", source_observations=[0, 5]
        )
        foundation = Object("floor", reconstruction_kind="Room floor")
        self.scene.objects.extend([protected, foundation])
        self.modules["bpy"].context = SimpleNamespace(
            view_layer=SimpleNamespace(update=lambda: None)
        )
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
                "upper": [9.0],
                "lower": [8.8],
                "desired": 12.0,
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
            patch.object(lower, "_visible_seats", return_value={}),
            patch.object(lower, "_visibility_ratio", return_value=1.0),
        ):
            result = lower.fit_lower_tier(self.scene, None, self.people, self.binding)
        self.assertAlmostEqual(result["L"]["tier_scale"], 4 / 3, places=4)
        np.testing.assert_allclose(self.chair.matrix_world, foundation.matrix_world)
        self.assertAlmostEqual(self.chair.matrix_world[0, 0], 4 / 3)
        for ob in [protected, self.upper, self.other]:
            np.testing.assert_equal(ob.matrix_world, np.eye(4))
            self.assertEqual(ob.keys, [])
        np.testing.assert_equal(self.lower.scale, np.ones(3))

    def test_rejected_static_floor_is_removed_before_building_next_candidate(self):
        from importlib import import_module

        fullclip_tier_floor = import_module("orderflow.fullclip_tier_floor")
        fullclip_tier_parallax = import_module("orderflow.fullclip_tier_parallax")

        floor = Object("floor", reconstruction_kind="Room floor")
        self.chair["reconstruction_kind"] = "Seat back"
        self.scene.objects.append(floor)
        self.modules["bpy"].context = SimpleNamespace(
            view_layer=SimpleNamespace(update=lambda: None)
        )
        camera = SimpleNamespace(matrix_world=SimpleNamespace(translation=np.zeros(3)))
        for row in self.frames:
            row["actors"][1]["partial_body_track"] = {
                "association": "shot_local_source_extension",
                "visible_bbox": [75, 90, 110, 140],
            }
        active = []
        scales = []

        def build(scene, floors, members, center, factor, origins):
            self.assertFalse(active)
            np.testing.assert_array_equal(floor.matrix_world, np.eye(4))
            self.assertEqual(scene.frame_current, 1)
            self.assertEqual(len(origins), 9)
            active.append(factor)
            floor.matrix_world = np.eye(4) * factor
            scales.append(factor)
            return list(active), {"factor": factor}

        def discard(children):
            self.assertEqual(children, active)
            active.clear()

        def check(scene, refs, center, factor, names):
            self.assertEqual(active, [factor])
            self.assertEqual(names, {"chair"})
            return {0: (60 if len(scales) == 1 else 99, 100)}

        with (
            patch.dict(sys.modules, self.modules),
            patch.object(
                lower,
                "_context",
                return_value=({(0, 0): [np.array([0, 0, 9])]}, [self.view, self.view]),
            ),
            patch.object(
                lower,
                "_measure",
                return_value={"upper": [9.0], "lower": [8.8], "desired": 12.0},
            ),
            patch.object(lower, "_visible_seats", return_value={}),
            patch.object(lower, "_visibility_counts", side_effect=check),
            patch.object(lower, "_place_actor", return_value=[{"frame": 0}]),
            patch.object(
                fullclip_tier_parallax,
                "measure",
                return_value={"state": "source_supports_expansion"},
            ),
            patch.object(fullclip_tier_floor, "build", side_effect=build),
            patch.object(fullclip_tier_floor, "discard", side_effect=discard),
        ):
            result = lower.fit_lower_tier(
                self.scene,
                camera,
                self.people,
                self.binding,
                source_config={"source": "test"},
            )["L"]
        self.assertEqual(
            [q["accepted"] for q in result["static_scale_trials"]], [False, True]
        )
        self.assertEqual(
            result["static_scale_trials"][0]["visible_seat_rays_by_source_frame"][0],
            (60, 100),
        )
        self.assertEqual(result["static_floor_partition"], {"factor": scales[-1]})
        self.assertEqual(active, scales[-1:])
        self.assertAlmostEqual(scales[-1], 1 + (scales[0] - 1) * 0.65)

    def test_visibility_is_resolved_to_integer_witnesses_in_every_view(self):
        self.assertTrue(lower._retains_seat_witnesses({0: (130, 137), 14: (26, 27)}))
        self.assertFalse(lower._retains_seat_witnesses({0: (129, 137), 14: (27, 27)}))
        self.assertFalse(lower._retains_seat_witnesses({14: (4, 5)}))
        self.assertFalse(lower._retains_seat_witnesses({}))

    def test_source_shoulder_must_remain_within_observed_outline(self):
        box = np.array([10, 20, 30, 60])
        good = {"partial_body_track": {"shoulder_uv": [16, 24]}}
        bad = {"partial_body_track": {"shoulder_uv": [700, 900]}}
        np.testing.assert_equal(lower._source_shoulder(good, box), [16, 24])
        np.testing.assert_almost_equal(lower._source_shoulder(bad, box), [19.6, 30.8])


if __name__ == "__main__":
    unittest.main()
