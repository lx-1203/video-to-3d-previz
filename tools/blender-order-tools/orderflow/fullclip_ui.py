"""Full-source local preview exposed through the normal OrderFlow sidebar."""

from pathlib import Path
import subprocess
import bpy
from bpy.props import EnumProperty, StringProperty
from . import core, motion
from .ui import _absolute, _native_popen, _resolve_executable, preferences

_processes = {}


class OrderFlowFullClip(bpy.types.PropertyGroup):
    profile: StringProperty(name="全片后端配置", subtype="FILE_PATH")
    height: EnumProperty(
        name="审片单栏高度",
        items=[("360", "360p", ""), ("540", "540p", ""), ("720", "720p", "")],
        default="540",
    )
    job: StringProperty(name="当前全片任务", subtype="DIR_PATH")


class ORDERFLOW_OT_fullclip_preview(bpy.types.Operator):
    bl_idname = "orderflow.fullclip_preview"
    bl_label = "全片转白模预演"

    def execute(self, context):
        settings = context.scene.orderflow_fullclip
        project = context.scene.orderflow
        try:
            current = _processes.get(settings.job)
            if current and current.poll() is None:
                raise ValueError("当前全片任务仍在运行。")
            source = Path(_absolute(project.reference_video))
            profile = Path(_absolute(settings.profile))
            if not source.is_file() or not profile.is_file() or not project.output_root:
                raise ValueError("请指定原片、全片本地后端配置和输出目录。")
            prefs = preferences(context)
            py = _resolve_executable(prefs.media_python)
            ff = _resolve_executable(prefs.ffmpeg)
            if not py or not ff:
                raise ValueError("请在插件偏好设置指定媒体 Python 和 FFmpeg。")
            runner = Path(__file__).with_name("fullclip_run.py")
            job = core.new_job_dir(_absolute(project.output_root), "full-clip")
            args = dict(
                source=str(source),
                profile=str(profile),
                media_python=py,
                ffmpeg=ff,
                blender=bpy.app.binary_path,
                height=int(settings.height),
                update_script=_absolute(prefs.motion_update_script),
            )
            core.atomic_json(job / "request.json", args)
            core.atomic_json(job / "status.json", dict(status="queued"))
            with (job / "run.log").open("xb") as log:
                _processes[str(job)] = _native_popen(
                    [py, "-B", str(runner), "--job", str(job / "request.json")],
                    env=motion._env(),
                    stdout=log,
                    stderr=-2,
                )
            settings.job = str(job)
        except (ValueError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "全片白模任务已启动。")
        return {"FINISHED"}


class ORDERFLOW_OT_open_preview_scene(bpy.types.Operator):
    bl_idname = "orderflow.open_preview_scene"
    bl_label = "在新窗口打开可编辑工程"

    def execute(self, context):
        folder = Path(_absolute(context.scene.orderflow_fullclip.job))
        blend = folder / "preview.blend"
        if not blend.is_file():
            self.report({"ERROR"}, "当前任务尚未生成可编辑工程。")
            return {"CANCELLED"}
        try:
            subprocess.Popen([bpy.app.binary_path, "-Y", str(blend)])
        except OSError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ORDERFLOW_PT_fullclip_preview(bpy.types.Panel):
    bl_label = "全片自动预演"
    bl_idname = "ORDERFLOW_PT_fullclip_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "接单助手"

    def draw(self, context):
        s = context.scene.orderflow_fullclip
        p = context.scene.orderflow
        layout = self.layout
        layout.prop(p, "reference_video")
        layout.prop(p, "output_root")
        layout.prop(s, "profile")
        layout.prop(s, "height")
        layout.operator("orderflow.fullclip_preview")
        if s.job:
            folder = Path(_absolute(s.job))
            status_path = folder / "status.json"
            if status_path.is_file():
                try:
                    st = core.read_json(status_path)
                    layout.label(
                        text=str(st.get("error") or st.get("stage") or st.get("status", ""))
                    )
                except (ValueError, OSError):
                    pass
            if (folder / "preview.blend").is_file():
                layout.operator("orderflow.open_preview_scene", icon="BLENDER")
            for target, text in [
                (folder / "comparison.mp4", "播放原声上下对照"),
                (folder / "white-model.mp4", "播放白模原声"),
                (folder, "打开任务目录"),
            ]:
                if target.exists():
                    layout.operator("wm.path_open", text=text).filepath = str(target)


_CLASSES = (
    OrderFlowFullClip,
    ORDERFLOW_OT_fullclip_preview,
    ORDERFLOW_OT_open_preview_scene,
    ORDERFLOW_PT_fullclip_preview,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.orderflow_fullclip = bpy.props.PointerProperty(type=OrderFlowFullClip)


def unregister():
    del bpy.types.Scene.orderflow_fullclip
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
