"""Native Blender regressions for editable pose keys and per-object paint isolation.

Run with blender -b -Y --factory-startup --python-exit-code 1 -P this_file.py.
"""

import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "Requires a native Blender Python runtime")
class NativeEditToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        result = bpy.ops.preferences.addon_enable(module="orderflow")
        if result != {"FINISHED"}:
            raise RuntimeError("Could not enable the worktree add-on")
        from orderflow import fullclip_edit

        cls.edit = fullclip_edit

    @classmethod
    def tearDownClass(cls):
        bpy.ops.preferences.addon_disable(module="orderflow")
        sys.path.remove(str(Path(__file__).resolve().parents[1]))

    def setUp(self):
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        self.created = []

    def tearDown(self):
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in reversed(self.created):
            bpy.data.objects.remove(obj, do_unlink=True)

    def test_color_on_unmaterialed_link_does_not_mutate_its_twin(self):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        twin = cube.copy()
        bpy.context.collection.objects.link(twin)
        self.created.extend((cube, twin))
        twin.select_set(False)
        self.assertEqual(cube.data, twin.data)
        self.assertEqual(len(cube.material_slots), 0)
        bpy.context.scene.orderflow_clay_color = (0.17, 0.31, 0.62)
        self.assertEqual(bpy.ops.orderflow.quick_clay_color(swatch="CUSTOM"), {"FINISHED"})
        self.assertEqual(len(twin.material_slots), 0)
        self.assertEqual(len(cube.material_slots), 1)
        color = cube.material_slots[0].material.diffuse_color
        for actual, wanted in zip(color, (0.17, 0.31, 0.62, 1.0)):
            self.assertAlmostEqual(actual, wanted, places=5)

    def test_pose_key_uses_active_rotation_mode_and_selected_bone_only(self):
        bpy.ops.object.armature_add()
        arm = bpy.context.active_object
        self.created.append(arm)
        bpy.ops.object.mode_set(mode="EDIT")
        first = arm.data.edit_bones[0]
        second = arm.data.edit_bones.new("Lower")
        second.head, second.tail, second.parent = first.tail, (0, 0, 2), first
        bpy.ops.object.mode_set(mode="OBJECT")
        self.assertEqual(bpy.ops.orderflow.edit_pose(), {"FINISHED"})
        self.assertEqual(bpy.context.mode, "POSE")
        bpy.ops.pose.select_all(action="DESELECT")
        parent = arm.pose.bones[0]
        arm.data.bones.active = parent.bone
        if hasattr(parent, "select"):
            parent.select = True
        else:
            parent.bone.select = True
        parent.rotation_mode = "XYZ"
        parent.rotation_euler.x = 0.35
        bpy.context.scene.frame_set(7)
        self.assertEqual(bpy.ops.orderflow.pose_key(), {"FINISHED"})
        from orderflow.fullclip_keyframes import action_curves

        curves = action_curves(arm.animation_data.action)
        self.assertEqual(
            {c.data_path for c in curves},
            {parent.path_from_id("location"), parent.path_from_id("rotation_euler")},
        )
        self.assertTrue(all(list(c.keyframe_points)[0].co.x == 7 for c in curves))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeEditToolsTest)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
