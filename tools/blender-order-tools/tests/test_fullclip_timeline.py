"""Regressions for a full timeline, editable key density and prepared-measurement integrity."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

try:
    import av
    import cv2
except ImportError:
    av = None

from orderflow.fullclip_keyframes import retained_samples, _key_tolerance
from orderflow.fullclip_inputs import image_observations, read_input_table
from orderflow import core


class FullClipContracts(unittest.TestCase):
    def test_sparse_editor_keys_retain_turn_and_endpoints(self):
        times = np.arange(17)
        values = np.array([[min(i, 16 - i), -i] for i in times], float)
        self.assertEqual(retained_samples(times, values, 0.001), {0, 8, 16})

    def test_small_planted_leg_correction_survives_key_reduction(self):
        values = np.array([[0], [0.0008], [0]], dtype=float)
        for path in ("location", 'pose.bones["DEF-thigh.L"].rotation_quaternion'):
            with self.subTest(path=path):
                self.assertEqual(
                    retained_samples([0, 1, 2], values, _key_tolerance(path, True)), {0, 1, 2}
                )
                self.assertEqual(
                    retained_samples([0, 1, 2], values, _key_tolerance(path, False)), {0, 2}
                )
        arm = 'pose.bones["DEF-forearm.R"].rotation_quaternion'
        self.assertEqual(_key_tolerance(arm, True), _key_tolerance(arm, False))

    def test_prepared_data_rejects_shifted_source_pts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model"
            model.write_bytes(b"test")
            data = root / "observe.json"
            data.write_text(
                json.dumps(
                    dict(
                        source_sha256="src",
                        model_sha256=core.sha256(model),
                        frames=[dict(source_frame=0, pts=1, time_base="1/30")],
                    )
                )
            )
            with self.assertRaisesRegex(ValueError, "clock"):
                image_observations(data, root / "new", "src", model, dict(pts=["0"]))

    def test_prepared_data_excludes_rendered_inputs(self):
        with self.assertRaisesRegex(ValueError, "Unknown measurement"):
            read_input_table(
                Path("local.json"), dict(measurement_inputs={"scene": "preview.blend"})
            )

    @unittest.skipIf(av is None, "local media runtime required")
    def test_clock_reaches_eof_and_detects_a_real_cut(self):
        from orderflow.fullclip_timeline import inspect_source

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (64, 36))
            self.assertTrue(writer.isOpened())
            for i in range(42):
                im = np.full((36, 64, 3), 25 if i < 20 else 195, np.uint8)
                im[10:18, i : i + 4] = 100
                writer.write(im)
            writer.release()
            result = inspect_source(path)
            self.assertEqual(
                (result["frames"], result["cuts"], result["pts"][-1]), (42, [20], "41/30")
            )


if __name__ == "__main__":
    unittest.main()
