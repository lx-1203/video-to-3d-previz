"""Regression for a valid media timeline containing the wrong white-model pictures."""

from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

import numpy as np

try:
    import av
    import cv2
except ImportError:
    av = None


@unittest.skipIf(av is None, "local media runtime required")
class EditablePreviewMedia(unittest.TestCase):
    def test_native_movie_rejects_uniform_and_reordered_renders(self):
        from orderflow.fullclip_verify import verify_native_movie

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frames = [np.full((36, 64, 3), level, np.uint8) for level in (35, 120, 215)]
            for i, raster in enumerate(frames, 1):
                self.assertTrue(cv2.imwrite(str(root / f"{i:06}.png"), raster))
            clock = {"frames": len(frames), "pts": [str(Fraction(i, 30)) for i in range(3)]}
            for name, sequence in (
                ("native", frames),
                ("uniform", [frames[-1]] * 3),
                ("reordered", frames[::-1]),
            ):
                with self.subTest(name=name):
                    movie = root / f"{name}.avi"
                    writer = cv2.VideoWriter(
                        str(movie), cv2.VideoWriter_fourcc(*"MJPG"), 30, (64, 36)
                    )
                    self.assertTrue(writer.isOpened())
                    try:
                        for raster in sequence:
                            writer.write(raster)
                    finally:
                        writer.release()
                    source = root / "native.avi"
                    if name == "native":
                        self.assertEqual(
                            verify_native_movie(source, movie, root, clock)["frames"], 3
                        )
                    else:
                        with self.assertRaisesRegex(ValueError, "image mismatch"):
                            verify_native_movie(source, movie, root, clock)
