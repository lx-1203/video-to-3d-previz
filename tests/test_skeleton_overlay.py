"""Real short-media contract tests; no client data, Blender, inference or GPU."""
import copy
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import av
import numpy as np


PROJECT = Path(__file__).resolve().parents[1]
CHECKER = PROJECT / "scripts/validate_skeleton_overlay.py"
TEST_ROOT = Path("E:/CodexRenderCache/order_toolkit_20261030/skeleton_overlay_tests")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf8")


def video(path, count=6, *, fps=30, height=64, overlay=False, points=None):
    """Real MPEG-4 fixtures with changing source pixels and visible projected lines."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 96, height, "yuv420p"
        stream.bit_rate = 1_000_000
        for index in range(count):
            image = np.zeros((height, 96, 3), dtype=np.uint8)
            image[:] = [35 + index * 8, 90, 130]
            image[:, 7 + index:27 + index] = [180, 75, 50]
            if overlay:
                # Composite source context with a synthetic visible torso/arm trace.
                image[14:52, 47:50] = [245, 230, 40]
                image[26:29, 25:72] = [50, 240, 60]
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.time_base = Fraction(1, fps)
            frame.pts = points[index] if points is not None else index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class SkeletonOverlayContractTests(unittest.TestCase):
    """Projection claims are synthetic declarations, never real Blender approval."""

    @classmethod
    def setUpClass(cls):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        cls.shared = tempfile.TemporaryDirectory(prefix="media-", dir=TEST_ROOT)
        cls.addClassCleanup(cls.shared.cleanup)
        cls.media = Path(cls.shared.name)
        cls.candidate = cls.media / "synthetic_frozen.blend"
        cls.candidate.write_bytes(b"SYNTHETIC BLEND HASH FIXTURE; DO NOT OPEN IN BLENDER")
        for name, options in {
            "source.mp4": {"count": 8},
            "overlay.mp4": {"overlay": True},
            "stack.mp4": {"height": 128, "overlay": True},
            "short.mp4": {"count": 5, "overlay": True},
            "wrong_fps.mp4": {"fps": 24, "overlay": True},
            "irregular_pts.mp4": {"points": [0, 1, 2, 4, 5, 6], "overlay": True},
            "nonzero_pts.mp4": {"points": [4, 5, 6, 7, 8, 9], "overlay": True},
            "short_source.mp4": {"count": 7},
            "source_wrong_fps.mp4": {"count": 8, "fps": 24},
            "source_irregular_pts.mp4": {"count": 8, "points": [0, 1, 2, 4, 5, 6, 7, 8]},
        }.items():
            video(cls.media / name, **options)
        write_json(cls.media / "source_pose.json", {
            "kind": "synthetic_manual_pose_trace", "source_frames": list(range(2, 8)),
            "note": "Synthetic coordinates; not a client or evaluated Blender result.",
        })

    def setUp(self):
        self.assertTrue(CHECKER.is_file(), "Skeleton overlay checker entrypoint is missing")
        spec = importlib.util.spec_from_file_location("isolated_overlay_contract", CHECKER)
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)
        self.temp = tempfile.TemporaryDirectory(prefix="case-", dir=TEST_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.protocol_path = self.root / "protocol.json"
        self.report_path = self.root / "report.json"
        self.projection_path = self.root / "projection.json"
        self.frames = list(range(2, 8))
        self.frame_map = dict(source_start=2, blender_start=11, frame_count=6, fps_num=30, fps_den=1)
        self.options = dict(media_python=sys.executable, **self.frame_map,
                            required_actors=["actorA", "actorB"])
        self.protocol = {
            "schema_version": 1, "protocol_type": "skeleton_overlay_v1",
            "candidate_sha256": digest(self.candidate),
            "source_video_sha256": digest(self.media / "source.mp4"),
            "frame_map": dict(self.frame_map), "actor_ids": ["actorA", "actorB"],
            "actors": {actor: {"chains": {chain: {"source_frames": list(self.frames)}
                       for chain in ("torso", "right_arm")}} for actor in ("actorA", "actorB")},
            "continuity": {"source_frames": list(self.frames)}, "cuts": [5],
        }
        self.projection = {
            "schema_version": 1, "kind": "evaluated_bones_and_visible_components",
            "candidate_sha256": digest(self.candidate),
            "source_video_sha256": digest(self.media / "source.mp4"),
            "frame_map": dict(self.frame_map), "actor_ids": ["actorA", "actorB"],
            "component_ids": {actor: [actor + "_visible_torso", actor + "_visible_arm"]
                              for actor in ("actorA", "actorB")},
            "from_evaluated_mesh": True, "from_evaluated_bones": True,
            "source_frames": list(self.frames),
        }
        self.projection["frames"] = self.projection_frames()
        evidence = self.artifact(self.media / "overlay.mp4")
        result = dict(executed=True, status="passed", source_frames=list(self.frames),
                      evidence=[evidence])
        self.report = {
            "schema_version": 1, "report_type": "skeleton_overlay_report_v1",
            "candidate_sha256": digest(self.candidate),
            "source_video_sha256": digest(self.media / "source.mp4"),
            "frame_map": dict(self.frame_map), "actor_ids": ["actorA", "actorB"],
            "reviewer": {"name": "Synthetic test reviewer", "date": "2026-10-30"},
            "executed": True, "status": "passed",
            "actors": {actor: {"chains": {chain: copy.deepcopy(result)
                       for chain in ("torso", "right_arm")}} for actor in ("actorA", "actorB")},
            "continuity": copy.deepcopy(result),
            "cuts": dict(copy.deepcopy(result), source_frames=[4, 5]),
            "overlay": dict(evidence, kind="same_frame_overlay", blend_weights={"source": .5, "skeleton": .5}),
            "projection": {"kind": "evaluated_bones_and_visible_components", "path": "projection.json"},
            "source_pose": dict(self.artifact(self.media / "source_pose.json"), kind="source_pose_reference"),
        }
        self.rewrite()

    def artifact(self, path):
        return {"path": str(path), "sha256": digest(path)}

    def projection_frames(self):
        return [{
            "source_frame": frame, "blender_frame": self.frame_map["blender_start"] + offset,
            "actors": {actor: {
                "bone_segments": {"torso": [[48, 15], [48, 50]], "right_arm": [[48, 27], [70, 28]]},
                "component_points": {actor + "_visible_torso": [[42, 14], [54, 14], [54, 52]],
                                     actor + "_visible_arm": [[48, 24], [74, 25], [74, 31]]},
            } for actor in ("actorA", "actorB")},
        } for offset, frame in enumerate(self.frames)]

    def rewrite(self, *, rebind=True):
        write_json(self.protocol_path, self.protocol)
        write_json(self.projection_path, self.projection)
        if rebind:
            self.report["protocol_sha256"] = digest(self.protocol_path)
            self.report["projection"]["sha256"] = digest(self.projection_path)
        write_json(self.report_path, self.report)

    def snapshot(self):
        return {str(p): digest(p) for folder in (self.root, self.media)
                for p in folder.rglob("*") if p.is_file()}

    def validate(self, **changes):
        before = self.snapshot()
        options = dict(self.options, **changes)
        errors = self.checker.validate(self.protocol_path, self.report_path, self.candidate,
                                       self.media / "source.mp4", **options)
        self.assertIsInstance(errors, list)
        self.assertTrue(all(isinstance(error, str) for error in errors))
        self.assertEqual(self.snapshot(), before, "Validation must not alter frozen inputs/evidence")
        return errors

    def reject(self, *, rewrite=True, **changes):
        if rewrite:
            self.rewrite()
        self.assertTrue(self.validate(**changes), "Invalid contract/media was accepted")

    def replace_overlay(self, name):
        self.report["overlay"].update(self.artifact(self.media / name))

    def test_valid_actual_cli_decodes_same_frame_overlay_without_mutating_inputs(self):
        self.assertEqual(self.validate(), [])
        command = [sys.executable, "-B", str(CHECKER), "--protocol", str(self.protocol_path),
                   "--report", str(self.report_path), "--candidate", str(self.candidate),
                   "--source-video", str(self.media / "source.mp4"), "--media-python", sys.executable]
        for key, value in self.frame_map.items():
            command.extend(["--" + key.replace("_", "-"), str(value)])
        for actor in self.options["required_actors"]:
            command.extend(["--required-actor", actor])
        before = self.snapshot()
        process = subprocess.run(command, cwd=self.root, capture_output=True, text=True, encoding="utf8")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result.get("limitation"))
        self.assertEqual(self.snapshot(), before)

    def test_cli_missing_arguments_is_incomplete_instead_of_traceback(self):
        process = subprocess.run([sys.executable, "-B", str(CHECKER)], cwd=self.root,
                                 capture_output=True, text=True, encoding="utf8")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(process.stdout)["status"], "incomplete")

    def test_wrong_protocol_type_and_technical_only_are_rejected(self):
        self.protocol["protocol_type"] = "technical_only"
        self.reject()
        self.protocol["protocol_type"] = "skeleton_overlay_v1"
        self.protocol.pop("actors")
        self.protocol["checks"] = [{"name": "4K/FPS/CRC", "passed": True}]
        self.reject()

    def test_unknown_failed_skipped_and_unexecuted_results_are_rejected(self):
        for entry in (self.report, self.report["continuity"], self.report["cuts"],
                      self.report["actors"]["actorB"]["chains"]["right_arm"]):
            for status in ("unknown", "failed", "skipped", "N/A", ""):
                with self.subTest(section=id(entry), status=status):
                    entry["status"] = status
                    self.reject()
            entry["status"] = "passed"
            for executed in (False, None, 1):
                with self.subTest(section=id(entry), executed=executed):
                    entry["executed"] = executed
                    self.reject()
            entry["executed"] = True

    def test_candidate_and_source_hash_mismatch_at_every_binding_is_rejected(self):
        for document in (self.protocol, self.report, self.projection):
            for field in ("candidate_sha256", "source_video_sha256"):
                with self.subTest(document=id(document), field=field):
                    old = document[field]
                    document[field] = "0" * 64
                    self.reject()
                    document[field] = old

    def test_protocol_projection_overlay_and_source_pose_artifact_sha_are_enforced(self):
        for field in ("overlay", "source_pose"):
            original = self.report[field]["sha256"]
            self.report[field]["sha256"] = "0" * 64
            self.reject()
            self.report[field]["sha256"] = original
        for field in ("protocol_sha256", "projection"):
            self.rewrite()
            if field == "projection":
                self.report[field]["sha256"] = "0" * 64
            else:
                self.report[field] = "0" * 64
            self.rewrite(rebind=False)
            self.reject(rewrite=False)
        self.report["continuity"]["evidence"][0]["sha256"] = "0" * 64
        self.reject()

    def test_empty_required_actors_duplicate_and_actor_coverage_are_rejected(self):
        for actors in ([], [""], ["actorA", "actorA"], ["actorA"], ["actorA", "missing"]):
            with self.subTest(required=actors):
                self.assertTrue(self.validate(required_actors=actors))
        for document in (self.protocol, self.report, self.projection):
            old = document["actor_ids"]
            document["actor_ids"] = []
            self.reject()
            document["actor_ids"] = old
        self.report["actors"].pop("actorB")
        self.reject()

    def test_each_actor_needs_a_real_chain_and_all_declared_chains_must_be_checked(self):
        old = self.protocol["actors"]["actorB"]["chains"]
        self.protocol["actors"]["actorB"]["chains"] = {}
        self.reject()
        self.protocol["actors"]["actorB"]["chains"] = old
        self.report["actors"]["actorB"]["chains"].pop("right_arm")
        self.reject()

    def test_chain_frames_cannot_be_empty_duplicate_unsorted_or_outside_job(self):
        chain = self.protocol["actors"]["actorA"]["chains"]["torso"]
        for frames in ([], [2, 2], [3, 2], [1], [8], [True]):
            with self.subTest(frames=frames):
                chain["source_frames"] = frames
                self.reject()

    def test_report_chain_must_cover_frozen_protocol_frames(self):
        self.report["actors"]["actorB"]["chains"]["right_arm"]["source_frames"] = [2, 3, 4, 5, 7]
        self.reject()

    def test_continuity_must_cover_every_frame_in_protocol_and_report(self):
        for document in (self.protocol, self.report):
            document["continuity"]["source_frames"] = [2, 3, 5, 6, 7]
            self.reject()
            document["continuity"]["source_frames"] = list(self.frames)

    def test_cut_check_requires_both_adjacent_frames(self):
        self.report["cuts"]["source_frames"] = [5]
        self.reject()
        self.report["cuts"]["source_frames"] = [4, 5]
        for cuts in ([2], [8], [5, 5], [6, 5]):
            with self.subTest(cuts=cuts):
                self.protocol["cuts"] = cuts
                self.reject()

    def test_no_cut_job_still_requires_executed_cut_review_and_evidence(self):
        self.protocol["cuts"] = []
        self.report["cuts"]["source_frames"] = []
        self.rewrite()
        self.assertEqual(self.validate(), [])
        self.report["cuts"]["evidence"] = []
        self.reject()

    def test_projection_requires_visible_components_for_each_actor(self):
        for components in ({}, {"actorA": ["torso"]}, {"actorA": ["torso"], "actorB": []},
                           {"actorA": ["torso"], "actorB": ["x", "x"]}):
            with self.subTest(components=components):
                self.projection["component_ids"] = components
                self.reject()

    def test_projection_requires_evaluated_mesh_bones_and_complete_frame_coverage(self):
        for field in ("from_evaluated_mesh", "from_evaluated_bones"):
            for value in (False, 1, None):
                with self.subTest(field=field, value=value):
                    self.projection[field] = value
                    self.reject()
            self.projection[field] = True
        self.projection["source_frames"] = [2, 4, 5, 6, 7]
        self.reject()

    def test_metadata_only_projection_is_rejected_even_with_valid_sha(self):
        self.projection.pop("frames")
        self.reject()

    def test_projection_coordinates_bind_every_source_and_blender_frame(self):
        valid = copy.deepcopy(self.projection["frames"])
        for frames in (valid[:-1], list(reversed(valid)), valid + [valid[-1]]):
            with self.subTest(frame_count=len(frames)):
                self.projection["frames"] = frames
                self.reject()
        self.projection["frames"] = copy.deepcopy(valid)
        for key, value in (("source_frame", 3), ("blender_frame", 12), ("source_frame", True)):
            self.projection["frames"][0][key] = value
            self.reject()
            self.projection["frames"] = copy.deepcopy(valid)

    def test_projection_data_requires_bones_and_all_visible_component_points(self):
        valid = copy.deepcopy(self.projection["frames"])
        actor = self.projection["frames"][0]["actors"]["actorB"]
        for field, value in (("bone_segments", {}), ("bone_segments", {"torso": [[1, 2]]}),
                             ("component_points", {}),
                             ("component_points", {"actorB_visible_torso": [[0, 0], [1, 0], [1, 1]]}),
                             ("component_points", {"actorB_visible_torso": [[0, 0], [1, 0]],
                                                   "actorB_visible_arm": [[0, 0], [1, 0], [1, 1]]})):
            with self.subTest(field=field, value=value):
                actor[field] = value
                self.reject()
                self.projection["frames"] = copy.deepcopy(valid)
                actor = self.projection["frames"][0]["actors"]["actorB"]
        self.projection["frames"][0]["actors"].pop("actorB")
        self.reject()

    def test_projection_numeric_coordinates_are_finite_nonboolean_and_allow_offscreen(self):
        self.projection["frames"][0]["actors"]["actorB"]["bone_segments"]["torso"][0] = [-20.5, 150]
        self.rewrite()
        self.assertEqual(self.validate(), [], "Offscreen finite coordinates are legitimate")
        for value in (True, None, "1", float("nan"), float("inf")):
            with self.subTest(coordinate=value):
                self.projection["frames"][0]["actors"]["actorB"]["bone_segments"]["torso"][0][0] = value
                self.reject()

    def test_extreme_projection_coordinate_returns_rejection_without_overflow(self):
        self.projection["frames"][0]["actors"]["actorB"]["bone_segments"]["torso"][0][0] = 10 ** 1000
        try:
            self.reject()
        except OverflowError as error:
            self.fail("Huge coordinate must return validation errors, not leak OverflowError: " + str(error))

    def test_extreme_blend_weight_returns_rejection_without_overflow(self):
        self.report["overlay"]["blend_weights"]["source"] = 10 ** 1000
        try:
            self.reject()
        except OverflowError as error:
            self.fail("Huge blend weight must return validation errors, not leak OverflowError: " + str(error))

    def test_overlay_cannot_be_source_file_or_same_bytes_copy(self):
        # Use the whole eight-frame source so a matching SHA is the only defect;
        # otherwise short overlay checks would reject this for unrelated reasons.
        self.frames = list(range(8))
        self.frame_map.update(source_start=0, frame_count=8)
        self.options.update(self.frame_map)
        for document in (self.protocol, self.report, self.projection):
            document["frame_map"] = dict(self.frame_map)
        for document in (self.protocol, self.report):
            document["continuity"]["source_frames"] = list(self.frames)
            for actor in document["actors"].values():
                for chain in actor["chains"].values():
                    chain["source_frames"] = list(self.frames)
        self.projection["source_frames"] = list(self.frames)
        self.projection["frames"] = self.projection_frames()
        eight_overlay = self.root / "eight_overlay.mp4"
        video(eight_overlay, count=8, overlay=True)
        self.report["overlay"].update(self.artifact(eight_overlay))
        self.rewrite()
        self.assertEqual(self.validate(), [])
        self.replace_overlay("source.mp4")
        self.reject()
        duplicate = self.root / "recopied_source.mp4"
        duplicate.write_bytes((self.media / "source.mp4").read_bytes())
        self.report["overlay"].update(self.artifact(duplicate))
        self.reject()

    def test_empty_overlay_with_rebound_sha_is_rejected_by_actual_probe(self):
        empty = self.root / "empty.mp4"
        empty.write_bytes(b"")
        self.report["overlay"].update(self.artifact(empty))
        self.reject()

    def test_source_only_white_only_and_invalid_blend_weights_are_rejected(self):
        for weights in ({"source": 1, "skeleton": 0}, {"source": 0, "skeleton": 1},
                        {"source": .5, "skeleton": .6}, {"source": True, "skeleton": .5},
                        {"source": float("nan"), "skeleton": .5}, {},
                        {"source": .5, "skeleton": .5, "extra": 0}):
            with self.subTest(weights=weights):
                self.report["overlay"]["blend_weights"] = weights
                self.reject()

    def test_real_media_rejects_stacked_doubled_height_and_short_overlay(self):
        for name in ("stack.mp4", "short.mp4"):
            with self.subTest(media=name):
                self.replace_overlay(name)
                self.reject()

    def test_real_media_rejects_wrong_fps_irregular_pts_and_nonzero_overlay_origin(self):
        for name in ("wrong_fps.mp4", "irregular_pts.mp4", "nonzero_pts.mp4"):
            with self.subTest(media=name):
                self.replace_overlay(name)
                self.reject()

    def test_source_selected_interval_requires_enough_frames_correct_fps_and_pts(self):
        for name in ("short_source.mp4", "source_wrong_fps.mp4", "source_irregular_pts.mp4"):
            with self.subTest(source=name):
                source = self.media / name
                for document in (self.protocol, self.report, self.projection):
                    document["source_video_sha256"] = digest(source)
                self.rewrite()
                errors = self.checker.validate(self.protocol_path, self.report_path, self.candidate,
                                                source, **self.options)
                self.assertTrue(errors)

    def test_frame_map_and_call_arguments_reject_bool_empty_and_wrong_ranges(self):
        for field, value in (("source_start", -1), ("blender_start", -1), ("frame_count", 0),
                             ("fps_num", 0), ("fps_den", 0), ("frame_count", True)):
            with self.subTest(argument=field, value=value):
                self.assertTrue(self.validate(**{field: value}))
        for document in (self.protocol, self.report, self.projection):
            original = document["frame_map"]
            for mapping in ({}, dict(self.frame_map, frame_count=True), dict(self.frame_map, source_start=3)):
                document["frame_map"] = mapping
                self.reject()
            document["frame_map"] = original

    def test_empty_nonfinite_and_duplicate_json_documents_are_rejected(self):
        for path in (self.protocol_path, self.report_path, self.projection_path):
            self.rewrite()
            valid = path.read_bytes()
            duplicate = valid.replace(b'"schema_version": 1',
                                      b'"schema_version": 1, "schema_version": 1', 1)
            self.assertNotEqual(duplicate, valid)
            nan_extra = b'{"unexpected_nan": NaN,' + valid[1:]
            infinity_extra = b'{"unexpected_infinity": Infinity,' + valid[1:]
            for data in (b"", b'[]', nan_extra, infinity_extra, duplicate):
                with self.subTest(document=path.name, data=data):
                    self.rewrite()
                    path.write_bytes(data)
                    if path == self.protocol_path:
                        self.report["protocol_sha256"] = digest(path)
                        write_json(self.report_path, self.report)
                    elif path == self.projection_path:
                        self.report["projection"]["sha256"] = digest(path)
                        write_json(self.report_path, self.report)
                    self.reject(rewrite=False)

    def test_absent_or_empty_evidence_and_reviewer_are_rejected(self):
        for value in ([], [{}], [{"path": "missing.png", "sha256": "0" * 64}]):
            self.report["actors"]["actorA"]["chains"]["torso"]["evidence"] = value
            self.reject()
        self.report["actors"]["actorA"]["chains"]["torso"]["evidence"] = [self.artifact(self.media / "overlay.mp4")]
        for reviewer in ({}, {"name": "", "date": "2026-10-30"}, {"name": "reviewer", "date": "2026-02-30"}):
            self.report["reviewer"] = reviewer
            self.reject()


class SkeletonOverlayTests(unittest.TestCase):
    def test_checker_exposes_validate(self):
        self.assertTrue(CHECKER.is_file(), "Skeleton overlay checker entrypoint is missing")
        spec = importlib.util.spec_from_file_location("isolated_skeleton_overlay", CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, "validate", None)), "validate API is missing")


if __name__ == "__main__":
    unittest.main()
