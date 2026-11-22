"""Check finite support in the same Blender geometry used for the final pose audit."""

from pathlib import Path
import sys
import unittest

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "Requires Blender Python")
class NativeContactAuditTests(unittest.TestCase):
    def test_grounded_floating_penetrating_and_beyond_an_edge(self):
        import numpy as np
        from itertools import product

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        self.addCleanup(sys.path.pop, 0)
        from orderflow.fullclip_contact_audit import _side, _surfaces

        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.5))
        floor = bpy.context.object
        self.addCleanup(bpy.data.objects.remove, floor, do_unlink=True)
        floor.name = "Finite test tread"
        surfaces = _surfaces([floor], bpy.context.evaluated_depsgraph_get())
        raw = np.array(list(product((-0.045, 0.045), (-0.055, 0.055), (0.0, 0.16))))
        lower = raw.copy()
        upper = raw + [0, 0, 0.16]
        end = raw.copy()
        end[:, 2] *= 0.15
        vertices = np.vstack((upper, lower, end))
        rule = (None, (list(range(8)), list(range(8, 16))), list(range(16, 24)), [])
        for dx, dz, ground, intersect in [
            (0, 0.006, True, False),
            (0, 0.20, False, False),
            (0, -0.012, False, True),
            (0.57, 0.006, False, False),
        ]:
            with self.subTest(dx=dx, dz=dz):
                row = _side(vertices + [dx, 0, dz], rule, surfaces)
                self.assertEqual(bool(row["support_within_30mm"]), ground)
                self.assertEqual(bool(row["crossed"]["foot_end"]), intersect)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeContactAuditTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
