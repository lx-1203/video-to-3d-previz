import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orderflow"))
from fullclip_scene import world_from_image, camera_depth_hint
from fullclip_scene_measure import _observations, _stage
from fullclip_scene_occluder import _track
from fullclip_scene_aperture import boundaries, rear_support, clipped_fixture
from fullclip_scene_entry import _ranges
from fullclip_scene_entry_config import runtime
from fullclip_scene_recess import recover
from fullclip_scene_seating import (
    fitted_rows,
    permanent_room,
    fill_observed_row_gaps,
    repeated_rows,
    join_fascia_corners,
)


class EditableSetTests(unittest.TestCase):
    def test_wall_opening_needs_depth_return_in_multiple_views(self):
        depth = np.full((100, 180), 4.0, dtype=float)
        base = np.full((100, 180, 3), 112, np.uint8)
        base[22:66, 58:107] = 25
        frames = {f: base.copy() for f in range(25)}
        cams = {f: np.eye(4) for f in frames}
        intr = np.array([[1.0, 0, 0.5], [0, 1.8, 0.5], [0, 0, 1.0]])
        scopes = {0: 1.0, 12: 1.0, 24: 1.0}
        flat = {f: depth.copy() for f in scopes}
        self.assertEqual(recover(frames, flat, scopes, cams, intr, {}, (180, 100)), ([], []))
        flat[0][22:66, 58:107] = 4.6
        self.assertEqual(recover(frames, flat, scopes, cams, intr, {}, (180, 100)), ([], []))
        flat[12][22:66, 58:107] = 4.6
        objects, proof = recover(frames, flat, scopes, cams, intr, {}, (180, 100))
        self.assertEqual(len(proof), 1)
        self.assertEqual(proof[0]["depth_views"], [0, 12])
        self.assertTrue(any(o["kind"] == "Recess backing" for o in objects))
        self.assertTrue(all(len(o["faces"]) == 6 and len(o["vertices"]) == 8 for o in objects))

    def test_optical_axis_and_shot_scale(self):
        cam = np.eye(4)
        cam[:3, 3] = [1, 2, 3]
        shot = {
            "first": 11,
            "last": 11,
            "world_scale": 2,
            "intrinsics": np.array([[2, 0, 0.5], [0, 3, 0.5], [0, 0, 1]]).tolist(),
            "camera_to_world": {"11": cam.tolist()},
        }
        np.testing.assert_allclose(
            world_from_image({"shots": [shot]}, 11, 0.7, 0.8, 4), [2.4, 10, -4.4]
        )
        with self.assertRaises(ValueError):
            world_from_image({"shots": [shot]}, 12, 0.5, 0.5, 1)
        with self.assertRaises(ValueError):
            world_from_image({"shots": [shot]}, 11, 0.5, 0.5, -1)

    def test_hash_bound_depth_samples(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.npz"
            np.savez(
                p,
                frames=np.array([5, 8]),
                depth=np.array([np.full((4, 4), 2.0), np.full((4, 4), 7.0)]),
            )
            doc = {
                "shots": [
                    {
                        "first": 5,
                        "last": 8,
                        "world_scale": 2.0,
                        "depth_samples": str(p),
                        "depth_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    }
                ]
            }
            self.assertEqual(camera_depth_hint(doc, 7, 0.5, 0.5), 14.0)
            self.assertIsNone(camera_depth_hint(doc, 7, 3, 0))
            doc["shots"][0]["depth_sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                camera_depth_hint(doc, 7, 0.5, 0.5)

    def test_failed_stage_and_changed_scene_artifact_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            job = Path(d)
            (job / "stages").mkdir()
            artifact = job / "depth.npz"
            artifact.write_bytes(b"unchanged")
            stage = {
                "llm_mode": "off",
                "status": "failed",
                "outputs": [
                    {
                        "path": str(artifact),
                        "bytes": 9,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    }
                ],
            }
            receipt = job / "stages" / "geometry-a.json"
            receipt.write_text(json.dumps(stage))
            with self.assertRaisesRegex(ValueError, "missing completed"):
                _stage(job, "geometry", artifact.name)
            stage["status"] = "completed"
            receipt.write_text(json.dumps(stage))
            self.assertEqual(_stage(job, "geometry", artifact.name), artifact)
            artifact.write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "changed"):
                _stage(job, "geometry", artifact.name)

    def test_box_receipt_source_or_output_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "frames.jsonl"
            path.write_text('{"source_zero_based":1,"raw_detections":[]}')
            rec = {
                "inputs": {"source": {"sha256": "a" * 64}},
                "outputs": {"frames.jsonl": hashlib.sha256(path.read_bytes()).hexdigest()},
            }
            path.with_name("receipt.json").write_text(json.dumps(rec))
            self.assertEqual(_observations(path, "a" * 64), {1: []})
            with self.assertRaises(ValueError):
                _observations(path, "b" * 64)
            path.write_text("")
            with self.assertRaises(ValueError):
                _observations(path, "a" * 64)

    def test_moving_uniform_post_and_empty_scene_control(self):
        rng = np.random.default_rng(718)
        bg = rng.integers(15, 90, (75, 140, 3), dtype=np.uint8)
        images = {}
        blank = {}
        for f in range(51):
            im = bg.copy()
            x = 12 + f
            im[:, x : x + 16] = 9
            images[f] = im
            blank[f] = bg
        tracked = _track(images)
        self.assertIsNotNone(tracked)
        self.assertGreater(np.ptp(tracked[:, 1]), 40)
        self.assertIsNone(_track(blank))

    def test_row_fits_solid_chairs_and_a_single_continuous_tier(self):
        centers = np.array([[10 + i * 11, 22] for i in range(6)])
        observations = [(c, c + np.array([[-4, -4], [4, -4], [4, 4], [-4, 4]])) for c in centers]
        image = np.full((64, 96, 3), 100, np.uint8)
        k = np.diag([1.0, 1.5, 1.0])

        def project(uv, z):
            return (
                np.column_stack((uv[:, 0] / 96, uv[:, 1] / 64, np.ones(len(uv))))
                * np.asarray(z)[:, None]
            )

        objects, rows = fitted_rows(
            observations,
            image,
            np.full((64, 96), 3.0),
            np.ones((64, 96), bool),
            project,
            np.eye(4),
            k,
            0,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(sum(o["kind"] == "Seat back" for o in objects), 6)
        self.assertEqual(sum(o["kind"] == "Stepped seating platform" for o in objects), 1)
        for o in objects:
            vertices = np.array(o["vertices"])
            self.assertEqual(np.linalg.matrix_rank(vertices - vertices[0]), 3)
            self.assertEqual(len(o["faces"]), 6)

    def test_shared_room_keeps_one_physical_set_across_view_boundaries(self):
        im = np.full((64, 96, 3), 100, np.uint8)
        obs = []
        for x in [15, 25, 35, 55, 65, 75]:
            c = np.array([x, 24])
            obs.append((c, c + np.array([[-4, -4], [4, -4], [4, 4], [-4, 4]])))
        k = np.diag([1.0, 1.5, 1.0])
        k[:2, 2] = 0.5

        def project(uv, z):
            return (
                np.column_stack(
                    (uv[:, 0] / 96 - 0.5, (uv[:, 1] / 64 - 0.5) / 1.5, np.ones(len(uv)))
                )
                * np.asarray(z)[:, None]
            )

        items = []
        for f in (0, 12):
            one, _ = fitted_rows(
                obs,
                im,
                np.full((64, 96), 3.0),
                np.ones((64, 96), bool),
                project,
                np.eye(4),
                k,
                f,
            )
            for o in one:
                o["visible_first"] = f
                o["visible_last"] = f + 5
            items.extend(one)
        cams = {f: np.eye(4) for f in range(17)}
        physical, info = permanent_room(items, cams, k, 96, 64)
        self.assertGreater(info["persistent_chairs"], 0)
        self.assertLess(info["persistent_chairs"], info["observed_seat_assemblies"])
        self.assertTrue(all(o["visible_first"] == 0 and o["visible_last"] == 16 for o in physical))
        added, _ = fill_observed_row_gaps(physical, {f: im for f in cams}, cams, k, {}, (96, 64))
        self.assertEqual(added, [])

    def test_native_source_intervals(self):
        self.assertEqual(
            _ranges([{"source_zero_start": 3, "source_zero_end": 8}, (9, 10)]),
            [(3, 8), (9, 10)],
        )
        for ranges in ([], [(0, 4), (4, 6)], [(0, 2), (5, 8)], [(1, 0)], [(0, 1.2)]):
            with self.assertRaises(ValueError):
                _ranges(ranges)

    def test_portable_model_paths_resolve_from_runtime_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "runtime.json"
            local = {
                "geometry_python": "env/python",
                "cpu_python": "cv/python",
                "geometry_model": {"path": "models/weights.pt", "sha256": "a" * 64},
                "geometry_source_roots": ["vendor/model"],
                "object_model": {"path": "models/objects.onnx", "sha256": "b" * 64},
            }
            path.write_text(json.dumps(local))
            resolved = runtime(path)
            self.assertEqual(Path(resolved["depth_model"]["path"]), root / "models/weights.pt")
            self.assertEqual(Path(resolved["coco_model"]["path"]), root / "models/objects.onnx")
            self.assertEqual(Path(resolved["depth_sources"][0]["root"]), root / "vendor/model")
            self.assertEqual(Path(resolved["cpu_python"]), root / "cv/python")
            local["object_model"]["sha256"] = "invalid"
            path.write_text(json.dumps(local))
            with self.assertRaises(ValueError):
                runtime(path)

    def test_persistent_opening_and_diagonal_depth_layer(self):
        import cv2

        frames = {}
        for i in range(31):
            g = np.full((120, 220, 3), 83, np.uint8)
            cv2.line(g, (0, 100 - i), (150, 26 - i), (20, 20, 20), 14)
            g[:22] = 13
            g[99:] = 13
            g[:, 164 - i : 184 - i] = 12
            frames[i] = g
        track = np.array([(i, 174 - i, 20, 12.0) for i in range(0, 31, 3)])
        bounds = boundaries(frames, track)
        self.assertEqual({x["side"] for x in bounds}, {"upper", "lower"})
        cameras = {i: np.eye(4) for i in frames}
        intr = np.array([[1, 0, 0.5], [0, 220 / 120, 0.5], [0, 0, 1.0]], float)
        obj, proof = rear_support(frames, cameras, intr, 1.0, lambda *half: [], [])
        self.assertEqual(len(obj), 1)
        self.assertLess(proof["fitted_slope"], -0.30)
        self.assertGreater(len(proof["observed_frames"]), 5)
        blank = {i: np.zeros_like(frames[0]) for i in frames}
        self.assertEqual(boundaries(blank, track), [])
        self.assertEqual(rear_support(blank, cameras, intr, 1.0, lambda *half: [], []), ([], {}))

    def test_unfeatured_clipped_stripe_cannot_seed_a_turned_fixture(self):
        images = {}
        for i in range(31):
            g = np.zeros((120, 240, 3), np.uint8)
            g[:, 219:224] = 84
            images[i] = g
        intr = np.array([[1, 0, 0.5], [0, 2, 0.5], [0, 0, 1]], float)
        cams = {i: np.eye(4) for i in images}
        self.assertEqual(
            clipped_fixture(images, {0: np.ones((120, 240))}, {0: 1}, cams, intr),
            ([], {}),
        )

    def test_oblique_periodic_profiles_require_a_supported_source_edge(self):
        import cv2

        image = np.full((120, 180, 3), 25, np.uint8)
        pts = np.array([[22 + 9 * i, 100 - 10 * i] for i in range(8)])
        shape = np.array([[-3, -4], [3, -4], [3, 4], [-3, 4]])
        cv2.line(image, tuple(pts[0] + [0, 6]), tuple(pts[-1] + [0, 6]), (93, 93, 93), 2)
        for x, y in pts:
            cv2.rectangle(image, (x - 3, y - 4), (x + 3, y + 4), (160, 145, 120), -1)
            cv2.line(image, (x - 3, y - 4), (x - 3, y + 4), (215, 205, 192), 1)
        candidates = [(p, p + shape) for p in pts[::2]]
        valid = np.ones(image.shape[:2], bool)
        rows = repeated_rows(image, valid, candidates)
        self.assertTrue(any(len(q) >= 5 and np.ptp(q[:, 1]) > np.ptp(q[:, 0]) for q, _, _ in rows))
        self.assertEqual(repeated_rows(image * 0, valid, candidates), [])

    def test_adjacent_observed_fascias_share_the_same_corner(self):
        def member(u, z):
            points = [[x * d, v * d, d] for (x, v), d in zip(u, z)]
            return {
                "kind": "Balustrade panel",
                "vertices": points + [[x, y, d + 0.04] for x, y, d in points],
            }

        panels = [
            member([(-0.4, -0.1), (0.0, -0.16), (0.0, 0.09), (-0.4, 0.2)], [2, 4, 4, 2]),
            member(
                [(0.004, -0.165), (0.41, -0.14), (0.41, 0.11), (0.004, 0.12)],
                [4.2, 3.6, 3.6, 4.2],
            ),
        ]
        join_fascia_corners(panels, np.eye(4), np.eye(3), 480, 270)
        self.assertTrue(all(o["shared_corner"] for o in panels))
        np.testing.assert_equal(
            np.asarray(panels[0]["vertices"])[[1, 2, 5, 6]],
            np.asarray(panels[1]["vertices"])[[0, 3, 4, 7]],
        )


if __name__ == "__main__":
    unittest.main()
