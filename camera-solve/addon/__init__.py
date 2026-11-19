# -*- coding: utf-8 -*-
"""camera-solve → Blender：把 `camera_solve/v1` 的焦距写进相机，**不碰 AI 的机位**。

设计原则（2026-11-09，按同学要求定）：

    1. **只写焦距**，绝不覆盖 cam_loc / rotation。
       同学的机位是 AI 定的，还要求支持人工可控关键帧 ——
       本插件只在"我有测量结果"的镜头上**插入 lens 关键帧**，
       其余镜头**一个关键帧都不写**，他们的 AI 结果和人工关键帧原样保留。
    2. **口径自动匹配**：Blender 的 lens(mm) 与 sensor_width(mm) 只以比值出现，
       所以本插件用工程里的 sensor_width 反算 lens = f_px * sensor_width / width_px，
       渲染出的视角永远等于测出来的 vfov（实测三组约定 vfov 都是 50.603°）。
    3. **状态可见**：面板逐镜头列出 measured / range / zoom_only / blocked /
       prior_only / no_evidence，不可写的镜头灰掉并给原因，避免误用。

"不用 AI"这条在这里是硬约束：插件只认物理方法测出的 f_px；
`prior_f_px`（学习模型值）**只显示、不使用**。

安装：把整个 `camera_solve_addon` 文件夹拷到
`%APPDATA%\\Blender Foundation\\Blender\\<ver>\\scripts\\addons\\`，或在
Preferences → Add-ons → Install 里选文件夹里的 `__init__.py` 所在目录打包安装。
"""
bl_info = {
    "name": "Camera Solve (focal length)",
    "author": "camera-solve-research",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > CameraSolve",
    "description": "读取 camera_solve/v1 JSON，把测得的焦距写进相机（不覆盖机位）",
    "category": "Camera",
}

import json
import os
import sys

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

# 让 jsonio 无论被当插件还是被单独 import 都能找到
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import jsonio   # noqa: E402

SENSOR_ITEMS = [(k, v[0], "") for k, v in jsonio.SENSORS.items()]
STATUS_COLOR = {
    jsonio.ST_MEASURED: (0.35, 0.85, 0.35, 1),
    jsonio.ST_RANGE: (0.95, 0.80, 0.30, 1),
    jsonio.ST_ZOOM_ONLY: (0.55, 0.65, 0.95, 1),
    jsonio.ST_PRIOR_ONLY: (0.55, 0.55, 0.55, 1),
    jsonio.ST_BLOCKED: (0.95, 0.35, 0.35, 1),
    jsonio.ST_NO_EVIDENCE: (0.45, 0.45, 0.45, 1),
}


def _plans_from_scene(scene):
    raw = scene.cs_plans_json or "[]"
    try:
        return json.loads(raw)
    except Exception:
        return []


class CS_Settings(PropertyGroup):
    json_dir: StringProperty(
        name="camera_solve 目录", subtype="DIR_PATH", default="",
        description="包含 *.camera_solve.json 的目录（通常是 handover/camera_solve）")
    sensor_preset: EnumProperty(
        name="sensor 约定", items=SENSOR_ITEMS, default="s35",
        description="只影响 mm 数字，不影响渲染视角（lens 与 sensor 成对设置）")
    sensor_width: FloatProperty(name="sensor 宽度 (mm)", default=24.89,
                                min=1.0, max=100.0)
    fps_src: FloatProperty(name="源帧率", default=0.0, min=0.0,
                           description="0 = 用 JSON 里的 fps")
    fps_dst: FloatProperty(name="Blender 帧率", default=0.0, min=0.0,
                           description="0 = 与源帧率相同（同帧率时 Blender帧 = frame_start + 源帧）")
    frame_start: IntProperty(name="frame_start", default=1, min=0)
    make_markers: BoolProperty(name="建镜头标记", default=True)
    override_lens: FloatProperty(
        name="人工覆盖 lens (mm)", default=0.0, min=0.0,
        description="给选中镜头手动填一个 lens；0 = 不用覆盖")
    sel_index: IntProperty(name="选中镜头", default=0, min=0)
    # ── 方案 A：f 优先级（2026-11-09）──
    f_priority: EnumProperty(
        name="f 优先级",
        items=[("algo_first", "算法优先（有测量就覆盖）", "物理测量优先于 AI"),
               ("ai_first", "AI 优先（已有 lens 就不覆盖）", "保留 AI 的结果"),
               ("warn", "报警（差太多就不覆盖）", "只报冲突，人工裁决")],
        default="algo_first")
    conflict_pct: FloatProperty(name="冲突阈值 %", default=30.0, min=1.0,
                                max=200.0)
    dense_keys: BoolProperty(
        name="逐帧关键帧", default=True,
        description="有曲线时每帧写一个 lens 关键帧（关掉则只在采样点写）")
    use_relative: BoolProperty(
        name="用 AI 的 f × 相对曲线", default=True,
        description="没有绝对 f 但有相对曲线的镜头：把基准 lens 乘上 ratio")
    manual_anchor: FloatProperty(
        name="锚点 lens (mm)", default=0.0, min=0.0,
        description="给「只有相对曲线」的镜头一个基准焦距；0 = 不用。"
                    "填了它就不必等 AI 提供 f")
    use_current_lens: BoolProperty(
        name="把相机当前 lens 当锚点", default=True,
        description="相机静态 lens 不等于 Blender 默认 50mm 时，可用作基准")


class CAMERASOLVE_OT_import(Operator):
    bl_idname = "camera_solve.import_json"
    bl_label = "读取 camera_solve JSON"
    bl_description = "读取目录下的 *.camera_solve.json，生成逐镜头计划表"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs
        sw = (jsonio.SENSORS[s.sensor_preset][1]
              if s.sensor_preset in jsonio.SENSORS else s.sensor_width)
        s.sensor_width = sw
        docs = jsonio.load_docs(bpy.path.abspath(s.json_dir))
        if not docs:
            self.report({"ERROR"}, "目录里没有 camera_solve/v1 JSON")
            return {"CANCELLED"}
        plans = jsonio.build_plan(
            docs, sw,
            fps_src=(s.fps_src or None), fps_dst=(s.fps_dst or None),
            frame_start=s.frame_start)
        context.scene.cs_plans_json = json.dumps(plans, ensure_ascii=False)
        su = jsonio.summary(plans)
        self.report({"INFO"}, "读到 %d 镜头，可写 %d" %
                    (su["n_shots"], su["n_writable"]))
        return {"FINISHED"}


def _existing_lens(cam, frame):
    """读该相机在这个帧上**已有的** lens（他们的 AI / 人工关键帧）。

    只有存在 `lens` 的 F 曲线时才算"有 AI 值"；没有曲线就返回 None ——
    否则会把 Blender 默认的 50mm 误当成 AI 的结果，导致 `ai_first` 永远不写。
    """
    ad = cam.data.animation_data
    if ad and ad.action:
        fc = ad.action.fcurves.find("lens")
        if fc is not None:
            try:
                return float(fc.evaluate(frame))
            except Exception:
                return None
    return None


def _shot_stub(p):
    return {"frame_range": [p["src_f0"], p["src_f1"]],
            "f_curve": p.get("f_curve"), "f_px": p.get("f_px")}


class CAMERASOLVE_OT_check(Operator):
    bl_idname = "camera_solve.check"
    bl_label = "接口自检 / 冲突报告"
    bl_description = ("检查 JSON 与相机现有 lens 是否对得上："
                      "可写几个、冲突几个、哪些只能用相对曲线")
    bl_options = {"REGISTER"}

    def execute(self, context):
        cam = context.object
        s = context.scene.cs
        plans = _plans_from_scene(context.scene)
        if not plans:
            self.report({"ERROR"}, "还没有计划表，先点『读取』")
            return {"CANCELLED"}
        n_w = n_c = n_keep = n_notw = 0
        lines = []
        for p in plans:
            if not p["writable"]:
                n_notw += 1
                continue
            ai = _existing_lens(cam, p["frame_blender"]) if cam else None
            act, note = jsonio.decide_action(
                p, ai, s.f_priority, s.conflict_pct / 100.0)
            if act == "write":
                n_w += 1
            elif act == "conflict":
                n_c += 1
                lines.append("冲突 s%d: 算法 %.2fmm vs AI %s (%s)" %
                             (p["shot"], p["lens_mm"],
                              ("%.2fmm" % ai) if ai else "?", note))
            else:
                n_keep += 1
        # 只有相对曲线的镜头：能不能找到基准 lens
        n_zoom, n_anchor = 0, 0
        for p in plans:
            if p["status"] != jsonio.ST_ZOOM_ONLY:
                continue
            n_zoom += 1
            ai = _existing_lens(cam, p["frame_blender"]) if cam else None
            anchor, src = jsonio.resolve_anchor(
                ai_lens=ai, manual_anchor=s.manual_anchor,
                current_lens=(float(cam.data.lens) if (cam and s.use_current_lens)
                              else None))
            if anchor is not None:
                n_anchor += 1
                print("[CameraSolve] 相对曲线 s%d: 基准 %.2fmm（%s）"
                      % (p["shot"], anchor, jsonio.anchor_note(src)))
        print("[CameraSolve] 自检：可写 %d / 冲突 %d / 保留 AI %d / 无测量 %d"
              "｜相对曲线 %d 个，其中有基准 %d"
              % (n_w, n_c, n_keep, n_notw, n_zoom, n_anchor))
        for ln in lines[:20]:
            print("   " + ln)
        self.report({"INFO"}, "可写 %d，冲突 %d，保留 %d，无测量 %d；"
                              "相对曲线 %d 个（有基准 %d）"
                    % (n_w, n_c, n_keep, n_notw, n_zoom, n_anchor))
        return {"FINISHED"}


class CAMERASOLVE_OT_apply(Operator):
    bl_idname = "camera_solve.apply"
    bl_label = "写入焦距关键帧"
    bl_description = ("只给有测量结果的镜头插入 lens 关键帧；"
                      "其余镜头不写，AI 机位与人工关键帧原样保留")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cam = context.object
        if cam is None or cam.type != "CAMERA":
            self.report({"ERROR"}, "先选中一个 Camera 物体")
            return {"CANCELLED"}
        s = context.scene.cs
        plans = _plans_from_scene(context.scene)
        if not plans:
            self.report({"ERROR"}, "还没有计划表，先点『读取』")
            return {"CANCELLED"}

        cam.data.sensor_fit = "HORIZONTAL"
        cam.data.sensor_width = s.sensor_width
        n_keys, n_shot, n_rel, n_skip, n_noanchor = 0, 0, 0, 0, 0
        for p in plans:
            stub = _shot_stub(p)
            ai = _existing_lens(cam, p["frame_blender"])

            if p["writable"]:
                act, _note = jsonio.decide_action(
                    p, ai, s.f_priority, s.conflict_pct / 100.0)
                if act != "write":
                    n_skip += 1
                    continue
                if s.sel_index == p["shot"] and s.override_lens > 0.0:
                    cam.data.lens = s.override_lens
                    cam.data.keyframe_insert(data_path="lens",
                                             frame=p["frame_blender"])
                    n_keys += 1
                    continue
                keys = jsonio.per_frame_lens(
                    p, stub, p["W"], s.sensor_width, s.frame_start,
                    s.fps_src or None, s.fps_dst or None, dense=s.dense_keys)
                for f, lens in keys:
                    cam.data.lens = lens
                    cam.data.keyframe_insert(data_path="lens", frame=f)
                n_keys += len(keys)
                n_shot += 1
                continue

            # 没有绝对 f，但**有相对曲线 + 能找到基准 lens** ⇒ 相乘
            # 基准优先级：手工锚点 > AI 的 lens 曲线 > 相机当前 lens(非默认)
            if s.use_relative and s.f_priority != "ai_first" \
                    and p["status"] == jsonio.ST_ZOOM_ONLY:
                anchor, src = jsonio.resolve_anchor(
                    ai_lens=ai, manual_anchor=s.manual_anchor,
                    current_lens=(float(cam.data.lens) if s.use_current_lens
                                  else None))
                if anchor is None:
                    n_noanchor += 1
                    continue
                keys = jsonio.relative_curve_lens(
                    stub, anchor, s.frame_start, s.fps_src or None,
                    s.fps_dst or None)
                for f, lens in keys:
                    cam.data.lens = lens
                    cam.data.keyframe_insert(data_path="lens", frame=f)
                n_keys += len(keys)
                n_rel += 1
                print("[CameraSolve] s%d 相对曲线 × %.2fmm（%s）"
                      % (p["shot"], anchor, jsonio.anchor_note(src)))
                continue
            n_skip += 1

        self.report({"INFO"},
                    "写 %d 个镜头 / %d 个关键帧（相对曲线 %d），"
                    "跳过 %d，缺锚点 %d"
                    % (n_shot + n_rel, n_keys, n_rel, n_skip, n_noanchor))
        return {"FINISHED"}


class CAMERASOLVE_OT_markers(Operator):
    bl_idname = "camera_solve.make_markers"
    bl_label = "建镜头标记"
    bl_description = "按镜头起止在时间轴上打标记，便于人工对关键帧"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        plans = _plans_from_scene(context.scene)
        if not plans:
            self.report({"ERROR"}, "还没有计划表")
            return {"CANCELLED"}
        sc = context.scene
        for m in list(sc.timeline_markers):
            if m.name.startswith("CS:"):
                sc.timeline_markers.remove(m)
        n = 0
        for p in plans:
            name = "CS:%s:s%d %s" % ((p["video"] or "?")[:10], p["shot"],
                                     p["status"])
            m = sc.timeline_markers.new(name, frame=p["frame_blender"])
            m.select = False
            n += 1
        self.report({"INFO"}, "建了 %d 个镜头标记" % n)
        return {"FINISHED"}


class CAMERASOLVE_PT_main(Panel):
    bl_label = "Camera Solve 焦距"
    bl_idname = "CAMERASOLVE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CameraSolve"

    def draw(self, context):
        lay = self.layout
        s = context.scene.cs
        col = lay.column(align=True)
        col.prop(s, "json_dir")
        col.prop(s, "sensor_preset")
        row = col.row(align=True)
        row.prop(s, "fps_src")
        row.prop(s, "fps_dst")
        col.prop(s, "frame_start")
        col.prop(s, "make_markers")

        row = lay.row(align=True)
        row.operator("camera_solve.import_json", icon="FILE_REFRESH")
        row.operator("camera_solve.apply", icon="KEYFRAME_HLT")
        lay.operator("camera_solve.make_markers", icon="MARKER_HLT")

        box = lay.box()
        box.label(text="方案 A：与 AI 的 f 谁优先")
        box.prop(s, "f_priority", text="")
        sub = box.row(align=True)
        sub.prop(s, "conflict_pct", text="阈值%")
        sub.prop(s, "dense_keys", text="逐帧键")
        box.prop(s, "use_relative", text="AI 的 f × 相对曲线")
        row2 = box.row(align=True)
        row2.prop(s, "manual_anchor", text="锚点 mm")
        row2.prop(s, "use_current_lens", text="用当前值")
        box.operator("camera_solve.check", icon="CHECKMARK")

        plans = _plans_from_scene(context.scene)
        if not plans:
            lay.label(text="（还没有计划表）", icon="INFO")
            return
        su = jsonio.summary(plans)
        box = lay.box()
        box.label(text="%d 镜头 / 可写 %d" % (su["n_shots"], su["n_writable"]))
        for k, v in sorted(su["by_status"].items()):
            box.label(text="  %s: %d" % (k, v))

        lay.separator()
        lay.label(text="人工覆盖（选中镜头）")
        row = lay.row(align=True)
        row.prop(s, "sel_index", text="镜头#")
        row.prop(s, "override_lens", text="lens")

        lay.separator()
        lay.label(text="逐镜头（灰=不可写）")
        for p in plans:
            r = lay.row(align=True)
            r.alert = (not p["writable"])
            r.label(text="s%-2d %-9s" % (p["shot"], p["status"][:9]))
            if p["lens_mm"] is not None:
                tag = "·%dpt" % len((p.get("f_curve") or {}).get("frames") or [])
                r.label(text="%.2fmm %.1f° %s" %
                        (p["lens_mm"], p["vfov_deg"], tag))
            elif p.get("prior_f_px"):
                r.label(text="先验 %.0fpx（不用）" % p["prior_f_px"])
            elif p.get("zoom_ratio"):
                r.label(text="zoom %.3f×（无绝对）" % p["zoom_ratio"])
            else:
                r.label(text="—")


CLASSES = (CS_Settings, CAMERASOLVE_OT_import, CAMERASOLVE_OT_check,
           CAMERASOLVE_OT_apply, CAMERASOLVE_OT_markers, CAMERASOLVE_PT_main)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.cs = bpy.props.PointerProperty(type=CS_Settings)
    bpy.types.Scene.cs_plans_json = bpy.props.StringProperty(default="[]")


def unregister():
    if hasattr(bpy.types.Scene, "cs_plans_json"):
        del bpy.types.Scene.cs_plans_json
    if hasattr(bpy.types.Scene, "cs"):
        del bpy.types.Scene.cs
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()

