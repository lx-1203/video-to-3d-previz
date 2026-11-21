"""Deterministic source-only regression fixtures for the editable hand-prop pass."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow.fullclip_props import measure_props


class PropEvidenceTest(unittest.TestCase):
    def fixture(self, mode):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source = Path(tmp.name) / "scene.avi"
        out = Path(tmp.name) / "result.json"
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (320, 200))
        self.assertTrue(writer.isOpened())
        rows = []
        rng = np.random.default_rng(12)
        floor = rng.integers(12, 43, (200, 460), dtype=np.uint8)
        for i in range(32):
            pan = 2 * i if mode == "pan" else 0
            image = cv2.cvtColor(floor[:, pan : pan + 320], cv2.COLOR_GRAY2BGR)
            x = 95 + 2 * i if mode != "pan" else 175 - pan
            ox = 105 if mode == "static" else x
            polygon = cv2.boxPoints(((ox + 7, 96), (24, 12), -25.0)).astype(int)
            if not 14 <= i <= 16:
                color = (220, 216, 200) if mode != "skin" else (59, 113, 182)
                cv2.fillConvexPoly(image, polygon, color)
            writer.write(image)
            joint = lambda u, v: dict(u=u, v=v, score=0.9, visibility="model_estimate")
            actors = []
            for k, (u, elbow) in enumerate(((x - 4, x - 38), (x + 18, x + 53))):
                actors.append(
                    dict(
                        track_id=k,
                        bbox=[u - 39, 68, u + 39, 174],
                        joints2d={"left_wrist": joint(u, 96), "left_elbow": joint(elbow, 103)},
                    )
                )
            rows.append(dict(source_frame=i, shot_id="S1", actors=actors))
        writer.release()
        data = measure_props(source, dict(width=320, height=200, frames=rows), out)
        self.assertEqual(data, json.loads(out.read_text()))
        return data

    def test_motion_shared_contact_and_occlusion(self):
        data = self.fixture("held")
        self.assertEqual(1, len(data["tracks"]))
        frames = {}
        for track in data["tracks"]:
            for s in track["samples"]:
                self.assertNotIn(s["frame"], frames)
                frames[s["frame"]] = s
        self.assertGreaterEqual(len(frames), 20)
        self.assertTrue({14, 15, 16}.isdisjoint(frames))
        self.assertTrue(any(len(s["contacts"]) == 2 for s in frames.values()))
        self.assertTrue(all(145 < s["angle_deg"] < 165 for s in frames.values()))

    def test_unmoving_and_camera_pan(self):
        for mode in ("static", "pan", "skin"):
            with self.subTest(mode=mode):
                self.assertEqual([], self.fixture(mode)["tracks"])


if __name__ == "__main__":
    unittest.main()
