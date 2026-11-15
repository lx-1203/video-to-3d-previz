"""Blender sidebar. Background jobs only operate on a saved copy of the scene."""
import json
from fractions import Fraction
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from types import SimpleNamespace

import bpy
from bpy.props import FloatProperty, IntProperty, PointerProperty, StringProperty

_PROCESSES = {}
_PACKAGE_PROCESSES = {}
_TERMINAL = {"complete", "failed", "cancelled"}
_PACKAGE_HELPER = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from orderflow.core import atomic_json, package_submission, read_json
job = Path(sys.argv[2])
try:
    result = package_submission(job, acceptance=read_json(sys.argv[3]))
    atomic_json(job / 'package_result.json', {'state': 'complete', 'path': str(result)})
except Exception as exc:
    atomic_json(job / 'package_result.json', {'state': 'failed', 'error': str(exc)})
    raise
"""


def _tool(name):
    return shutil.which(name) or ""


def _absolute(value):
    return str(Path(bpy.path.abspath(value)).expanduser().resolve()) if value else ""


def preferences(context):
    addon = context.preferences.addons.get(__package__)
    if addon:
        return addon.preferences
    # Direct script registration also supports smoke testing without installing an add-on.
    return SimpleNamespace(ffmpeg=_tool("ffmpeg"), media_python=_tool("python"),
                           motion_cli="", motion_update_script="", overlay_checker="")


class OrderFlowSettings(bpy.types.PropertyGroup):
    name: StringProperty(name="订单名", default="新订单")
    reference_video: StringProperty(name="参考视频", subtype="FILE_PATH")
    output_root: StringProperty(name="输出目录", subtype="DIR_PATH", default="//orderflow_output")
    frame_start: IntProperty(name="起始帧", default=1, min=1)
    frame_end: IntProperty(name="结束帧", default=250, min=1)
    source_start_seconds: FloatProperty(name="参考起点（秒）", default=0.0, min=0.0,
                                      description="工程首帧对应的原片时间；片段起始帧会另按帧率偏移")
    camera_name: StringProperty(name="相机", description="留空时使用工程当前活动相机")
    model_standard: StringProperty(name="模型规范（可选）", subtype="FILE_PATH")
    palette_path: StringProperty(name="角色配色 JSON", subtype="FILE_PATH")
    overlay_protocol: StringProperty(name="骨骼同帧审核协议", subtype="FILE_PATH")
    overlay_report: StringProperty(name="骨骼同帧审核报告", subtype="FILE_PATH")
    overlay_required_actors: StringProperty(name="审核角色 IDs", description="明确列出需要审核的稳定角色 ID，英文逗号分隔，不可重复")
    motion_job: StringProperty(name="动作任务目录", subtype="DIR_PATH")
    motion_result_path: StringProperty(name="动作辅助结果", subtype="FILE_PATH")
    motion_anchor_name: StringProperty(name="参考放置锚点", description="留空使用 3D 游标；只影响新导入的参考")
    active_job_dir: StringProperty(name="当前导出任务", subtype="DIR_PATH")
    status_message: StringProperty(name="进度", default="尚未开始导出")
    technical_status: StringProperty(name="技术核验", default="未核验")
    visual_status: StringProperty(name="视觉人工验收", default="未核验")
    motion_message: StringProperty(name="动作参考状态", default="尚未读取动作任务")
    progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype="FACTOR")


class OrderFlowPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    ffmpeg: StringProperty(name="FFmpeg", subtype="FILE_PATH", default=_tool("ffmpeg"))
    media_python: StringProperty(name="媒体 Python", subtype="FILE_PATH", default=_tool("python"),
                                 description="已装 av、numpy、Pillow 的 Python")
    motion_cli: StringProperty(name="动作工作台 CLI", subtype="FILE_PATH")
    motion_update_script: StringProperty(name="动作工作台更新检查", subtype="FILE_PATH")
    overlay_checker: StringProperty(name="已发布骨骼同帧检查器", subtype="FILE_PATH")

    def draw(self, context):
        for name in ("ffmpeg", "media_python", "motion_cli", "motion_update_script", "overlay_checker"):
            self.layout.prop(self, name)
        self.layout.label(text="媒体 Python 需要 av、numpy、Pillow。")


def _resolve_executable(value):
    if not value:
        return ""
    resolved = shutil.which(value)
    if resolved:
        return resolved
    path = Path(_absolute(value))
    return str(path) if path.is_file() else ""


def preflight(context, prefs=None, mode="preview"):
    """Read-only checks, safe to call from a real Blender test or the sidebar."""
    prefs = prefs or preferences(context)
    scene, settings = context.scene, context.scene.orderflow
    errors, warnings = [], []
    if not bpy.data.filepath or not Path(bpy.data.filepath).is_file():
        errors.append("请先保存当前 Blender 工程，再检查或导出。")
    camera = scene.objects.get(settings.camera_name) if settings.camera_name else scene.camera
    if camera is None or camera.type != "CAMERA":
        errors.append("找不到有效相机，请选择相机或设置工程活动相机。")
    elif camera != scene.camera:
        errors.append("指定相机不是工程活动相机，请先在工程中设为活动相机。")
    if not settings.reference_video or not Path(_absolute(settings.reference_video)).is_file():
        errors.append("参考视频不存在，请重新选择原片。")
    if settings.frame_start < scene.frame_start or settings.frame_end < settings.frame_start:
        errors.append("帧范围无效：起始帧须不早于工程首帧，结束帧须不早于起始帧。")
    if scene.frame_start < 1 or scene.render.fps <= 0 or scene.render.fps_base <= 0:
        errors.append("工程首帧和帧率必须大于零。")
    if settings.source_start_seconds < 0:
        errors.append("参考起点不能小于零。")
    if not settings.name.strip():
        errors.append("请填写订单名。")
    if settings.model_standard and not Path(_absolute(settings.model_standard)).is_file():
        errors.append("指定的模型规范文件不存在。")
    for field, label in (("ffmpeg", "FFmpeg"), ("media_python", "媒体 Python")):
        if not _resolve_executable(getattr(prefs, field, "")):
            errors.append(f"未找到{label}，请在插件偏好设置里选择可执行文件。")
    if not settings.output_root.strip():
        errors.append("请选择输出目录。")
    else:
        try:
            output = Path(_absolute(settings.output_root))
            if output.exists() and not output.is_dir():
                raise ValueError("所选路径是文件")
            ancestor = output
            while not ancestor.exists():
                ancestor = ancestor.parent
            if not os.access(ancestor, os.W_OK):
                raise ValueError("没有写入权限")
            width, height = (3840, 2160) if mode == "final" else (1280, 720)
            estimated = max(0, settings.frame_end - settings.frame_start + 1) * width * height * 4
            needed = estimated + 256 * 1024 * 1024
            if shutil.disk_usage(ancestor).free < needed:
                errors.append(f"输出盘空间不足，当前帧段预留至少 {needed / 1024**3:.1f} GB。")
        except (OSError, ValueError) as exc:
            errors.append(f"输出目录不可用：{exc}")
    # Missing external assets are reported before freezing a snapshot.
    from .worker import unsupported_simulations, unsupported_compositor_outputs
    errors.extend(unsupported_simulations(bpy))
    errors.extend(unsupported_compositor_outputs(scene))
    for group in (bpy.data.images, bpy.data.sounds, bpy.data.fonts, bpy.data.movieclips,
                  bpy.data.libraries, bpy.data.cache_files):
        for item in group:
            path = getattr(item, "filepath", "")
            if not path or path == "<builtin>" or getattr(item, "packed_file", None):
                continue
            if getattr(item, "source", None) in {"GENERATED", "VIEWER", "TILED"}:
                continue
            actual = bpy.path.abspath(path, library=getattr(item, "library", None))
            if not Path(actual).is_file():
                errors.append(f"缺少工程依赖：{item.name}（{actual}）")
    warnings.append("导出后仍需人工检查动作、接触、遮挡和颜色。")
    return {"errors": errors, "warnings": warnings, "camera": camera.name if camera else ""}


def collect_spec(context, mode, prefs=None):
    prefs = prefs or preferences(context)
    scene, settings = context.scene, context.scene.orderflow
    fps = Fraction(scene.render.fps, 1) / Fraction(scene.render.fps_base).limit_denominator(10000)
    return {"schema_version": 1, "name": settings.name.strip(),
            "source_blend": bpy.data.filepath, "reference_video": _absolute(settings.reference_video),
            "output_root": _absolute(settings.output_root), "frame_start": settings.frame_start,
            "frame_end": settings.frame_end, "scene_frame_start": scene.frame_start,
            "fps_num": fps.numerator, "fps_den": fps.denominator,
            "source_start_seconds": settings.source_start_seconds, "mode": mode,
            "camera": settings.camera_name or (scene.camera.name if scene.camera else ""),
            "ffmpeg": _resolve_executable(prefs.ffmpeg), "media_python": _resolve_executable(prefs.media_python),
            "model_standard": _absolute(settings.model_standard)}


def save_snapshot(context, job_dir):
    original = bpy.data.filepath
    if not original or not Path(original).is_file():
        raise ValueError("请先保存当前 Blender 工程。")
    snapshot = Path(job_dir) / "snapshot.blend"
    if snapshot.exists():
        raise ValueError("任务快照已存在，请创建新任务。")
    result = bpy.ops.wm.save_as_mainfile(filepath=str(snapshot), copy=True, relative_remap=True)
    if "FINISHED" not in result or not snapshot.is_file():
        raise RuntimeError("保存工程副本失败，请检查输出目录和磁盘空间。")
    if bpy.data.filepath != original:
        raise RuntimeError("保存副本后工程路径异常，已停止导出。")
    return snapshot


def _native_popen(argv, **kwargs):
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(argv, shell=False, **kwargs)


def _update_status(settings):
    if not settings.active_job_dir:
        return
    from . import core
    status = core.read_status(settings.active_job_dir)
    if not isinstance(status, dict):
        raise ValueError("任务状态应为 JSON 对象。")
    state = status.get("state", "unknown")
    phase = status.get("phase", state)
    progress = status.get("progress")
    if progress is None:
        total = status.get("total_frames", 0)
        progress = status.get("completed_frames", 0) / total if total else (1.0 if state == "complete" else 0.0)
    settings.progress = max(0.0, min(1.0, float(progress or 0.0)))
    frame = status.get("frame")
    settings.status_message = str(status.get("error") or status.get("message") or
                                  f"{phase}" + (f" · 第 {frame} 帧" if frame is not None else ""))
    # Only the frozen verification report can attest technical or visual verification.
    verification = Path(settings.active_job_dir) / "verification.json"
    settings.technical_status = "未核验"
    settings.visual_status = "未核验"
    if verification.is_file():
        try:
            report = json.loads(verification.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("核验报告应为 JSON 对象。")
            if state == "complete" and report.get("technical_passed") is True:
                settings.technical_status = "技术通过"
            settings.visual_status = {"passed": "人工通过", "failed": "人工未通过",
                                      "unverified": "未核验"}.get(report.get("visual_status"), "未核验")
        except (OSError, ValueError):
            settings.status_message = "核验报告不可读，请查看任务日志。"
    process = _PROCESSES.get(settings.active_job_dir)
    if process is not None and process.poll() is not None:
        _PROCESSES.pop(settings.active_job_dir, None)
        if state not in _TERMINAL:
            settings.status_message = "后台进程已结束，但没有完成核验；请查看 worker.log。"
    package_result = Path(settings.active_job_dir) / "package_result.json"
    if package_result.is_file():
        result = json.loads(package_result.read_text(encoding="utf-8"))
        if result.get("state") == "failed":
            settings.status_message = "打包失败：" + str(result.get("error", "请查看 package.log"))
        elif result.get("state") == "complete":
            settings.status_message = "交稿包：" + Path(result["path"]).name
        elif result.get("state") == "queued":
            settings.status_message = "正在后台核对文件并打包…"
            package_process = _PACKAGE_PROCESSES.get(settings.active_job_dir)
            if package_process is not None and package_process.poll() is not None:
                settings.status_message = "打包进程已结束，但没有核验结果；请查看 package.log。"


def _update_motion(settings):
    if not settings.motion_result_path:
        return
    path = Path(_absolute(settings.motion_result_path))
    if path.is_file():
        result = json.loads(path.read_text(encoding="utf-8"))
        settings.motion_message = str(result.get("message") or result.get("error") or result.get("state") or "等待动作结果")


def _status_timer():
    for scene in bpy.data.scenes:
        if hasattr(scene, "orderflow"):
            try:
                _update_status(scene.orderflow)
                _update_motion(scene.orderflow)
            except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
                scene.orderflow.status_message = f"无法读取任务状态：{exc}"
    for area in (bpy.context.screen.areas if bpy.context.screen else []):
        if area.type == "VIEW_3D":
            area.tag_redraw()
    return 1.0


def _ensure_timer():
    if not bpy.app.timers.is_registered(_status_timer):
        bpy.app.timers.register(_status_timer, first_interval=1.0, persistent=True)


class ORDERFLOW_OT_preflight(bpy.types.Operator):
    bl_idname = "orderflow.preflight"
    bl_label = "检查工程"

    def execute(self, context):
        result = preflight(context)
        errors = result["errors"]
        context.scene.orderflow.status_message = "；".join(errors) if errors else "工程基础检查通过，可以开始试渲。"
        for error in errors:
            self.report({"ERROR"}, error)
        if errors:
            return {"CANCELLED"}
        self.report({"INFO"}, "工程基础检查通过；导出后仍需人工检查画面。")
        return {"FINISHED"}


def _start_export(operator, context, mode):
    settings = context.scene.orderflow
    active = _PROCESSES.get(settings.active_job_dir)
    if active is not None and active.poll() is None:
        operator.report({"ERROR"}, "当前任务仍在导出，请等待完成或请求停止。")
        return {"CANCELLED"}
    result = preflight(context, mode=mode)
    if result["errors"]:
        settings.status_message = "；".join(result["errors"])
        operator.report({"ERROR"}, settings.status_message)
        return {"CANCELLED"}
    try:
        from . import core
        spec = collect_spec(context, mode)
        job_dir = core.new_job_dir(spec["output_root"], spec["name"])
        spec["snapshot_blend"] = str(save_snapshot(context, job_dir))
        job_path = core.write_job(job_dir, spec)
        command = core.blender_worker_command(bpy.app.binary_path, job_path)
        with (job_dir / "worker.log").open("ab") as log:
            process = _native_popen(command, stdout=log, stderr=subprocess.STDOUT)
        settings.active_job_dir = str(job_dir)
        _PROCESSES[str(job_dir)] = process
        settings.status_message = "已提交后台任务"
        settings.technical_status = "未核验"
        settings.visual_status = "未核验"
        settings.progress = 0.0
        _ensure_timer()
    except (OSError, ValueError, RuntimeError) as exc:
        settings.status_message = str(exc)
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    operator.report({"INFO"}, "后台导出已启动，可继续检查工程。")
    return {"FINISHED"}


class ORDERFLOW_OT_preview(bpy.types.Operator):
    bl_idname = "orderflow.preview"
    bl_label = "试渲片段 · 720p"

    def execute(self, context):
        return _start_export(self, context, "preview")


class ORDERFLOW_OT_export_final(bpy.types.Operator):
    bl_idname = "orderflow.export_final"
    bl_label = "4K 候选导出"

    def execute(self, context):
        return _start_export(self, context, "final")


class ORDERFLOW_OT_cancel(bpy.types.Operator):
    bl_idname = "orderflow.cancel"
    bl_label = "请求停止"

    def execute(self, context):
        settings = context.scene.orderflow
        try:
            job = Path(settings.active_job_dir)
            if not settings.active_job_dir or not (job / "job.json").is_file():
                # A readable status also permits recovering a cancelled job from a saved scene.
                if not settings.active_job_dir or not (job / "status.json").is_file():
                    raise ValueError("没有可停止的当前任务。")
            (job / "cancel.request").touch(exist_ok=True)
            settings.status_message = "已请求停止，后台会在安全检查点退出。"
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ORDERFLOW_OT_refresh(bpy.types.Operator):
    bl_idname = "orderflow.refresh"
    bl_label = "刷新状态"

    def execute(self, context):
        try:
            _update_status(context.scene.orderflow)
            _update_motion(context.scene.orderflow)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ORDERFLOW_OT_open_output(bpy.types.Operator):
    bl_idname = "orderflow.open_output"
    bl_label = "打开输出"

    def execute(self, context):
        settings = context.scene.orderflow
        try:
            path = _absolute(settings.active_job_dir or settings.output_root)
            if not path or not Path(path).is_dir():
                raise ValueError("输出目录尚未创建。")
            if os.name == "nt":
                os.startfile(path)
            else:
                _native_popen(["open" if __import__("sys").platform == "darwin" else "xdg-open", path])
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ORDERFLOW_OT_package(bpy.types.Operator):
    bl_idname = "orderflow.package"
    bl_label = "生成交稿 ZIP"

    def execute(self, context):
        try:
            from . import core
            settings = context.scene.orderflow
            if not settings.active_job_dir:
                raise ValueError("请先完成一次导出。")
            previous = _PACKAGE_PROCESSES.get(settings.active_job_dir)
            if previous is not None and previous.poll() is None:
                raise ValueError("当前任务仍在打包，请等待结果。")
            python = _resolve_executable(preferences(context).media_python)
            if not python:
                raise ValueError("请在偏好设置中选择媒体 Python。")
            job = Path(settings.active_job_dir)
            prefs = preferences(context)
            request = job/('acceptance_request_'+uuid.uuid4().hex+'.json')
            core.atomic_json(request, {'protocol':_absolute(settings.overlay_protocol), 'report':_absolute(settings.overlay_report), 'required_actors':[value.strip() for value in settings.overlay_required_actors.split(',')], 'checker':_absolute(prefs.overlay_checker), 'motion_update_script':_absolute(prefs.motion_update_script)})
            core.atomic_json(job / "package_result.json", {"state": "queued"})
            with (job / "package.log").open("ab") as log:
                process = _native_popen([python, "-c", _PACKAGE_HELPER, str(Path(__file__).resolve().parents[1]), str(job), str(request)],
                                        stdout=log, stderr=subprocess.STDOUT)
            _PACKAGE_PROCESSES[settings.active_job_dir] = process
            settings.status_message = "正在后台核对文件并打包…"
            _ensure_timer()
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def _motion_action(operator, context, action):
    try:
        from . import motion
        settings, prefs = context.scene.orderflow, preferences(context)
        # Root's motion bridge validates the updater and queues slow work without touching bpy.
        result = getattr(motion, "motion_" + action)(settings, prefs)
        if result.get("result_path"):
            settings.motion_result_path = str(result["result_path"])
            _ensure_timer()
        settings.motion_message = str(result.get("message") or result.get("status") or result.get("state") or result)
        if result.get("error"):
            raise ValueError(str(result["error"]))
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        context.scene.orderflow.motion_message = str(exc)
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    return {"FINISHED"}


class ORDERFLOW_OT_motion_status(bpy.types.Operator):
    bl_idname = "orderflow.motion_status"
    bl_label = "查看动作状态"

    def execute(self, context):
        return _motion_action(self, context, "status")


class ORDERFLOW_OT_motion_next(bpy.types.Operator):
    bl_idname = "orderflow.motion_next"
    bl_label = "继续动作辅助"

    def execute(self, context):
        return _motion_action(self, context, "next")


class ORDERFLOW_OT_import_motion_reference(bpy.types.Operator):
    bl_idname = "orderflow.import_motion_reference"
    bl_label = "导入动作候选参考"

    def execute(self, context):
        try:
            from . import motion
            result = motion.import_reference(context, _absolute(context.scene.orderflow.motion_job))
            context.scene.orderflow.motion_message = str(result.get("message") or "已导入独立参考，不参与正式渲染。")
            if result.get("error"):
                raise ValueError(str(result["error"]))
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class ORDERFLOW_OT_apply_palette(bpy.types.Operator):
    bl_idname = 'orderflow.apply_palette'
    bl_label = '应用固定角色配色'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            from . import palette
            path = _absolute(context.scene.orderflow.palette_path)
            if not path:
                raise ValueError('请先选择角色配色 JSON。')
            result = palette.apply(context, path)
            self.report({'INFO'}, f'已为 {result["actors"]} 个角色、{result["objects"]} 个对象设置固定配色。')
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class ORDERFLOW_PT_main(bpy.types.Panel):
    bl_label = "接单助手 / OrderFlow"
    bl_idname = "ORDERFLOW_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "接单助手"

    def draw(self, context):
        layout, settings = self.layout, context.scene.orderflow
        layout.prop(settings, "name")
        layout.prop(settings, "reference_video")
        layout.prop(settings, "output_root")
        layout.prop_search(settings, "camera_name", context.scene, "objects")
        row = layout.row(align=True)
        row.prop(settings, "frame_start")
        row.prop(settings, "frame_end")
        layout.prop(settings, "source_start_seconds")
        layout.prop(settings, "model_standard")
        layout.prop(settings, "palette_path")
        layout.operator('orderflow.apply_palette', icon='MATERIAL')
        layout.operator("orderflow.preflight", icon="CHECKMARK")
        row = layout.row(align=True)
        row.operator("orderflow.preview", icon="RENDER_ANIMATION")
        row.operator("orderflow.export_final", icon="EXPORT")
        box = layout.box()
        box.label(text=settings.status_message[:110])
        box.prop(settings, "progress", text="导出进度", slider=True)
        box.label(text="技术核验：" + settings.technical_status)
        box.label(text="视觉人工验收：" + settings.visual_status)
        row = box.row(align=True)
        row.operator("orderflow.refresh", icon="FILE_REFRESH")
        row.operator("orderflow.cancel", icon="CANCEL")
        box.prop(settings, 'overlay_protocol')
        box.prop(settings, 'overlay_report')
        box.prop(settings, 'overlay_required_actors')
        row = box.row(align=True)
        row.operator("orderflow.open_output", icon="FILE_FOLDER")
        row.operator("orderflow.package", icon="PACKAGE")
        box = layout.box()
        box.label(text="动作参考（保留现有角色动作）")
        box.prop(settings, "motion_job")
        box.prop_search(settings, "motion_anchor_name", context.scene, "objects")
        row = box.row(align=True)
        row.operator("orderflow.motion_status")
        row.operator("orderflow.motion_next")
        box.operator("orderflow.import_motion_reference", icon="IMPORT")
        box.label(text=settings.motion_message[:110])
        layout.label(text="工具路径在插件偏好设置中配置。", icon="PREFERENCES")


_CLASSES = (OrderFlowSettings, OrderFlowPreferences, ORDERFLOW_OT_preflight, ORDERFLOW_OT_preview,
            ORDERFLOW_OT_export_final, ORDERFLOW_OT_cancel, ORDERFLOW_OT_refresh, ORDERFLOW_OT_open_output,
            ORDERFLOW_OT_package, ORDERFLOW_OT_motion_status, ORDERFLOW_OT_motion_next,
            ORDERFLOW_OT_import_motion_reference, ORDERFLOW_OT_apply_palette, ORDERFLOW_PT_main)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.orderflow = PointerProperty(type=OrderFlowSettings)
    _ensure_timer()


def unregister():
    if bpy.app.timers.is_registered(_status_timer):
        bpy.app.timers.unregister(_status_timer)
    if hasattr(bpy.types.Scene, "orderflow"):
        del bpy.types.Scene.orderflow
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
