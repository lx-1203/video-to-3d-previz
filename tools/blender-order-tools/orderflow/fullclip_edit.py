"""Manual pose keys and one-click material swatches for Blender's native scene."""

import bpy
from bpy.props import EnumProperty, FloatVectorProperty
from . import palette


SWATCHES = {
    "WHITE": (0.82, 0.82, 0.82),
    "RED": (0.76, 0.045, 0.038),
    "BLUE": (0.045, 0.23, 0.72),
    "GREEN": (0.04, 0.48, 0.20),
    "YELLOW": (0.92, 0.56, 0.04),
}


def actor_rig(obj):
    if obj is None:
        return None
    if obj.type == "ARMATURE":
        return obj
    return obj.find_armature() if obj.type == "MESH" else None


def marker_slot(obj, index, material):
    if material and material.get("orderflow_role"):
        return material["orderflow_role"] == "marker"
    rig = actor_rig(obj)
    if not rig or "track_id" not in rig:
        return False
    head = obj.vertex_groups.get("DEF-spine.006")
    torso = obj.vertex_groups.get("DEF-spine")
    if not head or not torso:
        return False
    polygons = [p for p in obj.data.polygons if p.material_index == index]
    if not polygons or len(polygons) >= len(obj.data.polygons) * 0.15:
        return False
    used = {
        g.group
        for poly in polygons
        for v in poly.vertices
        for g in obj.data.vertices[v].groups
        if g.weight > 0.5
    }
    return used == {head.index, torso.index}


class ORDERFLOW_OT_edit_pose(bpy.types.Operator):
    bl_idname = "orderflow.edit_pose"
    bl_label = "编辑角色姿势"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return actor_rig(context.active_object) is not None

    def execute(self, context):
        rig = actor_rig(context.active_object)
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        rig.select_set(True)
        context.view_layer.objects.active = rig
        rig.show_in_front = True
        bpy.ops.object.mode_set(mode="POSE")
        return {"FINISHED"}


class ORDERFLOW_OT_pose_key(bpy.types.Operator):
    bl_idname = "orderflow.pose_key"
    bl_label = "给所选骨骼打关键帧"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "POSE" and bool(context.selected_pose_bones)

    def execute(self, context):
        for bone in context.selected_pose_bones:
            rotation = {
                "QUATERNION": "rotation_quaternion",
                "AXIS_ANGLE": "rotation_axis_angle",
            }.get(bone.rotation_mode, "rotation_euler")
            for prop in ("location", rotation):
                bone.keyframe_insert(
                    data_path=prop, frame=context.scene.frame_current, group=bone.name
                )
        return {"FINISHED"}


class ORDERFLOW_OT_quick_clay_color(bpy.types.Operator):
    bl_idname = "orderflow.quick_clay_color"
    bl_label = "给所选白模上色"
    bl_options = {"REGISTER", "UNDO"}
    swatch: EnumProperty(items=[(key, key, "") for key in [*SWATCHES, "CUSTOM"]], default="CUSTOM")

    @classmethod
    def poll(cls, context):
        return any(obj.type in ("MESH", "ARMATURE") for obj in context.selected_objects)

    def execute(self, context):
        meshes = set()
        for obj in context.selected_objects:
            if obj.type == "MESH":
                meshes.add(obj)
            elif obj.type == "ARMATURE":
                meshes.update(child for child in obj.children if child.type == "MESH")
        if not meshes:
            return {"CANCELLED"}
        color = tuple(
            SWATCHES[self.swatch]
            if self.swatch in SWATCHES
            else context.scene.orderflow_clay_color
        ) + (1.0,)
        plan = []
        try:
            for obj in meshes:
                for slot in obj.material_slots:
                    old = slot.material
                    if old:
                        palette._base_sockets(old)
                    plan.append(
                        (obj, slot.slot_index, old, marker_slot(obj, slot.slot_index, old))
                    )
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        materials = {}
        for obj in meshes:
            if not obj.material_slots:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                obj.data.materials.append(None)
                plan.append((obj, 0, None, False))
        for obj, index, original, marker in plan:
            if (original, marker) not in materials:
                mat = original.copy() if original else bpy.data.materials.new("White model paint")
                if marker:
                    mat["orderflow_role"] = "marker"
                palette._set_material(mat, palette.complement(color) if marker else color)
                materials[original, marker] = mat
            slot = obj.material_slots[index]
            slot.link = "OBJECT"
            slot.material = materials[original, marker]
        return {"FINISHED"}


class ORDERFLOW_PT_native_edit(bpy.types.Panel):
    bl_idname = "ORDERFLOW_PT_native_edit"
    bl_label = "动作关键帧与快速上色"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "接单助手"

    def draw(self, context):
        layout = self.layout
        layout.operator("orderflow.edit_pose", icon="POSE_HLT")
        layout.operator("orderflow.pose_key", icon="KEY_HLT")
        row = layout.row(align=True)
        for key, title in zip(SWATCHES, ("白", "红", "蓝", "绿", "黄")):
            row.operator("orderflow.quick_clay_color", text=title).swatch = key
        row = layout.row(align=True)
        row.prop(context.scene, "orderflow_clay_color", text="")
        row.operator("orderflow.quick_clay_color", text="应用自选色").swatch = "CUSTOM"


classes = (
    ORDERFLOW_OT_edit_pose,
    ORDERFLOW_OT_pose_key,
    ORDERFLOW_OT_quick_clay_color,
    ORDERFLOW_PT_native_edit,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.orderflow_clay_color = FloatVectorProperty(
        name="白模颜色", subtype="COLOR", size=3, min=0, max=1, default=(0.72, 0.72, 0.72)
    )


def unregister():
    del bpy.types.Scene.orderflow_clay_color
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
