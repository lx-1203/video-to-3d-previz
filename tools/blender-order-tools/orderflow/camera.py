"""Turn one CameraSolve shot into a bounded OrderFlow focal-length candidate."""
from dataclasses import dataclass, replace
from fractions import Fraction
from importlib.util import module_from_spec, spec_from_file_location
import json
import math
from pathlib import Path, PureWindowsPath

from .core import sha256


def _load_camera_solve():
    source = Path(__file__).with_name("camera_solve_jsonio.py")
    if not source.is_file():
        source = Path(__file__).resolve().parents[3] / "camera-solve/addon/jsonio.py"
    spec = spec_from_file_location(f"{__package__}.camera_solve_jsonio", source)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


jsonio = _load_camera_solve()


@dataclass(frozen=True)
class FocalPlan:
    keys: tuple[tuple[int, float], ...]
    relative: bool
    blocker: str = ""


def _positive(value, label):
    if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{label}必须为正数。")
    return value


def make_plan(document, *, shot_id, reference_name, scene_start, frame_range,
              fps, source_start, aspect, sensor, anchor):
    if not isinstance(document, dict) or document.get("schema") != "camera_solve/v1":
        raise ValueError("请选择 camera_solve/v1 JSON。")
    meta = document.get("meta")
    if not isinstance(meta, dict) or meta.get("frame_index_base") != 0:
        raise ValueError("测量文件必须声明原片零基帧号。")
    source = meta.get("source_video")
    if not isinstance(source, str) or PureWindowsPath(source).name.casefold() != reference_name.casefold():
        raise ValueError("测量文件的参考视频名称与当前订单不一致。")
    width = _positive(meta.get("width_px"), "测量画幅宽度")
    height = _positive(meta.get("height_px"), "测量画幅高度")
    if not math.isclose(width / height, aspect, rel_tol=0.001):
        raise ValueError("测量画幅与工程输出画幅不一致。")
    source_fps = Fraction(_positive(meta.get("fps"), "原片帧率")).limit_denominator(100_000)
    shots = document.get("shots")
    matching = [row for row in shots if isinstance(row, dict) and row.get("shot_id") == shot_id] if isinstance(shots, list) else []
    if len(matching) != 1:
        raise ValueError("测量文件中没有唯一匹配的镜头号。")
    shot = matching[0]
    bounds = shot.get("frame_range")
    if (not isinstance(bounds, list) or len(bounds) != 2 or
            any(type(frame) is not int for frame in bounds) or not 0 <= bounds[0] <= bounds[1]):
        raise ValueError("测量镜头的原片帧段无效。")
    source_first, source_last = bounds
    offset = Fraction(source_start).limit_denominator(100_000)
    # Use the same source start as OrderFlow's export. The source range is inclusive.
    first = max(frame_range[0], scene_start + math.ceil((source_first / source_fps - offset) * fps))
    last = min(frame_range[1], scene_start + math.ceil(((source_last + 1) / source_fps - offset) * fps) - 1)
    if first > last:
        raise ValueError("选定镜头与订单导出帧段没有重叠。")
    zoom = shot.get("zoom")
    curve = shot.get("f_curve")
    if ((zoom is not None and not isinstance(zoom, dict)) or
            (curve is not None and not isinstance(curve, dict))):
        raise ValueError("变焦曲线格式无效。")
    status = jsonio.status_of(shot)
    if status == jsonio.ST_BLOCKED:
        raise ValueError("CameraSolve 标记此镜头为不可应用，请核对切镜或测量条件。")
    f_px = shot.get("f_px")
    relative = f_px is None
    if relative:
        if not (zoom or {}).get("usable") or not curve:
            raise ValueError("此镜头没有可用的实测焦距或相对变焦曲线。")
        baseline = _positive(anchor, "相对变焦的基准焦距")
    else:
        baseline = jsonio.lens_mm(_positive(f_px, "实测像素焦距"), sensor, width)
    blocker = "测量结果有冲突，仅供对比。" if shot.get("regime_reason") == "measured_conflict" else ""
    if not curve:
        keys = ((first, baseline),) if first == last else ((first, baseline), (last, baseline))
        return FocalPlan(keys, relative, blocker)

    points, ratios = curve.get("frames"), curve.get("ratio")
    if (not isinstance(points, list) or not points or not isinstance(ratios, list) or
            len(points) != len(ratios) or any(type(frame) is not int for frame in points) or
            points != sorted(set(points))):
        raise ValueError("变焦曲线须提供有序、唯一且等长的帧号和倍率。")
    for ratio in ratios:
        _positive(ratio, "变焦倍率")
    samples = [float((offset + Fraction(frame - scene_start, 1) / fps) * source_fps)
               for frame in range(first, last + 1)]
    values = jsonio.interp_ratio(curve, samples)
    keys = tuple((frame, baseline * ratio) for frame, ratio in enumerate(values, first))
    return FocalPlan(keys, relative, blocker)


def preview(scene, settings, json_path, reference):
    camera = scene.objects.get(settings.camera_name) if settings.camera_name else scene.camera
    if camera is None or camera.type != "CAMERA" or camera != scene.camera:
        raise ValueError("请先在工程中启用订单使用的相机。")
    data = camera.data
    if data.type != "PERSP":
        raise ValueError("焦距测量需要透视相机。")
    reference = Path(reference)
    if not reference.is_file():
        raise ValueError("请先选择订单参考视频。")
    document = json.loads(Path(json_path).read_text(encoding="utf-8-sig"))
    if isinstance(document, dict) and isinstance(document.get("meta"), dict):
        expected = document["meta"].get("source_sha256")
        if expected and sha256(reference) != expected:
            raise ValueError("参考视频的 SHA-256 与测量文件不符。")
    render = scene.render
    aspect = render.resolution_x * render.pixel_aspect_x / (render.resolution_y * render.pixel_aspect_y)
    # Blender computes the effective sensor width from its existing projection settings.
    corners = data.view_frame(scene=scene)
    sensor = data.lens * (max(v.x for v in corners) - min(v.x for v in corners)) / abs(corners[0].z)
    plan = make_plan(
        document, shot_id=settings.camera_shot, reference_name=reference.name, scene_start=scene.frame_start,
        frame_range=(max(scene.frame_start, settings.frame_start), min(scene.frame_end, settings.frame_end)),
        fps=Fraction(render.fps, 1) / Fraction(render.fps_base).limit_denominator(10_000),
        source_start=settings.source_start_seconds, aspect=aspect, sensor=sensor,
        anchor=settings.camera_anchor or None,
    )
    return camera, replace(plan, blocker=_apply_blocker(scene, camera, plan))


def _apply_blocker(scene, camera, plan):
    if plan.blocker:
        return plan.blocker
    original = camera.data
    animation = original.animation_data
    if animation and (animation.action or animation.nla_tracks or animation.drivers):
        return "当前相机数据已有动画或驱动，焦距候选仅供对比。"
    if not camera.is_editable or camera.override_library:
        return "当前相机不可直接编辑。"
    first, last = plan.keys[0][0], plan.keys[-1][0]
    markers = sorted((m for m in scene.timeline_markers if m.camera and m.frame <= last),
                     key=lambda marker: marker.frame)
    previous = [marker for marker in markers if marker.frame <= first]
    if (previous and previous[-1].camera != camera or
            any(marker.frame > first and marker.camera != camera for marker in markers)):
        return "当前镜头的时间轴绑定了其它相机，请先核对镜头范围。"
    lens_limits = original.bl_rna.properties["lens"]
    if any(not lens_limits.hard_min <= value <= lens_limits.hard_max for _, value in plan.keys):
        return "候选焦距超出 Blender 相机的有效范围。"
    return ""


def apply(scene, camera, plan):
    blocker = _apply_blocker(scene, camera, plan)
    if blocker:
        raise ValueError(blocker)

    import bpy

    original = camera.data
    first, last = plan.keys[0][0], plan.keys[-1][0]
    candidate = original.copy()
    try:
        keys = ((first - 1, original.lens), *plan.keys, (last + 1, original.lens))
        for frame, value in keys:
            candidate.lens = value
            if not candidate.keyframe_insert(data_path="lens", frame=frame):
                raise RuntimeError(f"无法写入焦距关键帧 {frame}。")
        animation = candidate.animation_data
        action = animation.action
        if hasattr(action, "fcurves"):
            curves = action.fcurves
        else:
            curves = action.layers[0].strips[0].channelbag(animation.action_slot).fcurves
        curve = curves.find("lens")
        curve.extrapolation = "CONSTANT"
        for key in curve.keyframe_points:
            key.interpolation = "CONSTANT" if key.co.x in (first - 1, last, last + 1) else "LINEAR"
        curve.update()
        candidate.lens = original.lens
        camera.data = candidate
    except Exception:
        action = candidate.animation_data.action if candidate.animation_data else None
        bpy.data.cameras.remove(candidate)
        if action and not action.users:
            bpy.data.actions.remove(action)
        raise
