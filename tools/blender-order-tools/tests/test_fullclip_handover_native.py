"""Exercise the normal post-bind handover fit in native Blender for both mesh types."""

import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "Requires Blender Python")
class HandoverPostfitTest(unittest.TestCase):
    def test_rigid_trajectory_is_untouched_and_legacy_outline_still_refits(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        self.addCleanup(sys.path.pop, 0)
        from orderflow.fullclip_body_size import reconcile_handover_props

        scene = bpy.context.scene
        bpy.ops.object.camera_add(location=(80, 80, 0), rotation=(0, 0, 0))
        camera = bpy.context.object
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(80, 80, -4))
        obj = bpy.context.object
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        self.addCleanup(bpy.data.objects.remove, camera, do_unlink=True)
        obj.name = "Observed sample"
        obj["source_sha256"] = "fixture"
        obj["shot_id"] = "shot"
        obj["fixed_mesh"] = True
        obj.keyframe_insert(data_path="location", frame=1)
        obj.location.z = -4.1
        obj.keyframe_insert(data_path="location", frame=2)
        orig = scene.frame_current
        self.addCleanup(scene.frame_set, orig)
        item = dict(frame=1, polygon_uv=[[150, 90], [170, 90], [170, 110], [150, 110]])
        data = dict(
            width=320,
            height=200,
            source_sha256="fixture",
            tracks=[dict(id="sample", shot_id="shot", samples=[item])],
        )
        ik = dict(applied=[dict(frame=1, prop="sample", strength=1.0, depth=3.0)])
        binding = dict(objects=[obj.name])
        report = reconcile_handover_props(scene, camera, data, binding, ik)
        self.assertEqual(report[0]["state"], "rigid_track_pose_preserved")
        scene.frame_set(2)
        self.assertAlmostEqual(obj.location.z, -4.1, places=5)
        self.assertEqual(tuple(obj.scale), (1.0, 1.0, 1.0))
        del obj["fixed_mesh"]
        report = reconcile_handover_props(scene, camera, data, binding, ik)
        self.assertEqual(report[0]["state"], "source_visibility_constrained_depth")
        scene.frame_set(2)
        self.assertAlmostEqual(obj.location.z, -3.0)
        self.assertLess(obj.scale.x, 0.9)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HandoverPostfitTest)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
