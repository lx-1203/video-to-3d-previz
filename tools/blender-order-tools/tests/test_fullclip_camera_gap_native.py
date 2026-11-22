import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "Requires Blender Python")
class CameraGapTest(unittest.TestCase):
    def test_gap_joins_final_root_and_preserves_a_supported_entrance(self):
        from mathutils import Vector

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        self.addCleanup(sys.path.pop, 0)
        from orderflow.fullclip_binding import camera_root_locations

        # Endpoints enter the final median fit. Missing-frame pixel-per-metre
        # estimates by themselves would put a direct interpolation behind the wall.
        depths = [8.0, 8.0, 8.0, 7.6, 7.6, 9.5]
        samples = [
            (i, None, None, None, Vector((i / 4, z, 0.3)), False) for i, z in enumerate(depths)
        ]
        bridges = {3: (2, 5, 1 / 3, None), 4: (2, 5, 2 / 3, None)}
        result = camera_root_locations(samples, bridges)
        for i in (3, 4):
            want = result[2].lerp(result[5], (i - 2) / 3)
            self.assertLess((result[i] - want).length, 1e-6)
        self.assertGreater((samples[2][4].lerp(samples[5][4], 2 / 3) - result[4]).length, 0.3)
        enter = [
            (0, None, None, None, Vector((0, 15, 0)), False),
            (1, None, None, None, Vector((0, 7, 0)), True),
        ]
        self.assertEqual(list(camera_root_locations(enter, {})), [s[4] for s in enter])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CameraGapTest)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
