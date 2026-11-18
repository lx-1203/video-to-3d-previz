# -*- coding: utf-8 -*-
"""`camera_solve/v1` → Blender 镜头参数：**纯逻辑层，不 import bpy**。

为什么单独拆出来（2026-11-09）：
    同学要求"搭成 Blender 插件基础"。但插件里的 bpy 部分在这台机器上跑不了，
    所以把**所有会算错的地方**（口径换算、帧映射、状态判定）放在这个纯模块里，
    可以用普通 python 直接做单元测试（见 tools\\test_addon_jsonio.py）。
    `__init__.py` 里只剩"把数字写进 Blender"这一层薄壳。

口径问题的数学事实（必须讲清楚，否则插件没法写）：
    能从画面测出来的只有 ``f_px``。而 Blender 的 ``lens(mm)`` 与 ``sensor_width(mm)``
    只以**比值**出现：``lens = f_px * sensor_width / width_px``。
    ⇒ **sensor 宽度在数学上不可从画面反推**，它是一句"约定"。
    ⇒ 但只要 lens 和 sensor_width **成对**按某个约定设置，**渲染出的视角完全相同**。
    实测（f_px=1142.3，1920×1080）：36mm→21.4mm、24.89mm→14.8mm、17.3mm→10.3mm，
    三者 vfov 都是 50.603°。
    ⇒ 所以"口径自动匹配"是**可以做到**的：本模块输出 f_px + vfov，
      插件按自己工程里的 sensor_width 反算 lens。视角不会错，mm 数字随约定走。
"""
from __future__ import annotations

import json
import math
import os

# Blender 里常用的几种 sensor 宽度（横向，mm）
SENSORS = {
    "ff": ("全画幅 36mm", 36.0),
    "s35": ("Super 35 24.89mm", 24.89),
    "m43": ("M4/3 17.3mm", 17.3),
    "s16": ("Super 16 12.52mm", 12.52),
}

# 镜头状态：决定插件**允不允许写关键帧**
ST_MEASURED = "measured"        # 有绝对焦距（物理方法）→ 可写
ST_RANGE = "measured_range"     # 有绝对焦距但段内变焦 → 可写，且提示是区间
ST_ZOOM_ONLY = "zoom_only"      # 只有倍率，没有绝对 → **不可写绝对 lens**
ST_PRIOR_ONLY = "prior_only"    # 只有学习模型 → **不可写**（实测偏 −22%~−55%）
ST_BLOCKED = "blocked"          # 被闸门拦下 → 不可写
ST_NO_EVIDENCE = "no_evidence"  # 无证据 → 不可写


def vfov_deg(f_px, height_px):
    return math.degrees(2.0 * math.atan(height_px / (2.0 * f_px)))


def hfov_deg(f_px, width_px):
    return math.degrees(2.0 * math.atan(width_px / (2.0 * f_px)))


def lens_mm(f_px, sensor_w_mm, width_px):
    """f_px（原生像素）→ Blender 的 lens(mm)，按给定 sensor 宽度。"""
    return f_px * sensor_w_mm / float(width_px)


def status_of(shot):
    """把一份 shot 判成上面 6 种状态之一。"""
    if shot.get("split_required") or shot.get("gate_reason"):
        return ST_BLOCKED
    f_px = shot.get("f_px")
    if f_px is None:
        if shot.get("prior_f_px"):
            return ST_PRIOR_ONLY
        if (shot.get("zoom") or {}).get("usable"):
            return ST_ZOOM_ONLY
        return ST_NO_EVIDENCE
    if shot.get("f_px_per_frame_model") == "range":
        return ST_RANGE
    return ST_MEASURED


def build_plan(docs, sensor_w_mm, fps_src=None, fps_dst=None,
               frame_start=1):
    """把若干 doc 摊平成**逐镜头计划表**，插件就是照这张表写关键帧。

    帧映射遵循同学的约定（`references/reference-and-camera.md` §1）：
        无时间变换且同帧率时，源视频零基帧 i ↔ Blender `frame_start + i`。
        fps 不同则按时间戳显式映射，不套用 +1。
    """
    plans = []
    for fn, d in docs:
        meta = d.get("meta") or {}
        W = int(meta.get("width_px") or 1920)
        H = int(meta.get("height_px") or 1080)
        src_fps = float(fps_src or meta.get("fps") or 30.0)
        dst_fps = float(fps_dst or src_fps)
        for s in d.get("shots") or []:
            st = status_of(s)
            f0, f1 = (int(s["frame_range"][0]), int(s["frame_range"][1]))
            f_px = s.get("f_px")
            lm = (lens_mm(float(f_px), sensor_w_mm, W)
                  if f_px is not None else None)
            zoom = s.get("zoom") or {}
            # 源帧 → 时间 → 目标帧（同帧率时就是 frame_start+i）
            tb0 = frame_start + int(round(f0 * dst_fps / src_fps))
            tb1 = frame_start + int(round(f1 * dst_fps / src_fps))
            plans.append({
                "file": fn,
                "video": meta.get("source_video"),
                "shot": int(s["shot_id"]),
                "src_f0": f0, "src_f1": f1,
                "frame_blender": tb0,
                "frame_blender_end": tb1,
                "status": st,
                "writable": st in (ST_MEASURED, ST_RANGE),
                "f_px": f_px,
                "lens_mm": (round(lm, 4) if lm is not None else None),
                "vfov_deg": (round(vfov_deg(float(f_px), H), 3)
                             if f_px is not None else None),
                "hfov_deg": (round(hfov_deg(float(f_px), W), 3)
                             if f_px is not None else None),
                "zoom_ratio": (zoom.get("zoom_ratio") if zoom.get("usable")
                               else None),
                "regime": s.get("regime"),
                "reason": s.get("regime_reason"),
                "prior_f_px": s.get("prior_f_px"),
                # 逐帧曲线（tools\f_curve.py 写入）——插件用它决定写几个关键帧
                "has_curve": bool((s.get("f_curve") or {}).get("frames")),
                "f_curve": s.get("f_curve"),
                "W": W, "H": H,
            })
    return plans


def summary(plans):
    c = {}
    for p in plans:
        c[p["status"]] = c.get(p["status"], 0) + 1
    return {"n_shots": len(plans), "by_status": c,
            "n_writable": sum(1 for p in plans if p["writable"])}


# ── f 优先级（方案 A，2026-11-09）──────────────────────────────────────────
# 冲突场景：他们的 AI 解机位时**通常也顺带给了一个焦距**。
# 于是同一个镜头上会有两个 f：AI 的和我的物理测量。必须显式定优先级，
# 否则集成后会出现"模型跟不住又查不出原因"。
PRIORITY_MODES = {
    "ai_first": ("AI 优先（摄像头已有 lens 就不覆盖）", "keep_ai"),
    "algo_first": ("算法优先（有物理测量就覆盖 AI）", "write"),
    "warn": ("报警（差值超阈值时不覆盖，只报）", "conflict"),
}


def decide_action(plan, ai_lens, mode="algo_first", conflict_ratio=0.30):
    """单个镜头该不该写、写谁的 f。

    ai_lens: 该镜头在**当前相机上已有的** lens（他们的 AI 结果 / 人工关键帧）；
             None 表示没有。
    返回 (action, note)：
        write     → 写我的测量值
        keep_ai   → 保留他们的值，不写
        conflict  → 不写，但要报冲突（人工裁决）
        skip      → 本来就没有测量结果
    """
    if not plan.get("writable") or plan.get("lens_mm") is None:
        return "skip", plan.get("status", "")
    if ai_lens is None:
        return "write", "no_ai_value"
    algo = float(plan["lens_mm"])
    ai = float(ai_lens)
    if mode == "ai_first":
        return "keep_ai", "ai_first"
    if mode == "algo_first":
        return "write", "algo_first"
    # warn：只在差得离谱时才报，其余保留 AI
    rel = abs(algo - ai) / max(algo, ai, 1e-9)
    if rel > conflict_ratio:
        return "conflict", "diff=%.0f%%" % (rel * 100.0)
    return "keep_ai", "diff=%.0f%%" % (rel * 100.0)


# ── 逐帧 f 曲线（2026-11-09，第三个需求）────────────────────────────────
# 变焦镜头不能只给一个常数 f。实测法只能拿到**稀疏采样点**（有脸的帧），
# 所以用 log 线性插值铺到每一帧 —— f 的变化本质是乘性的，log 空间插值才合理。
def interp_ratio(fc, frames):
    """把 f_curve 里的稀疏 ratio 序列按帧号插值到 `frames`（升序）。"""
    xs = [float(v) for v in (fc.get("frames") or [])]
    ys = [float(v) for v in (fc.get("ratio") or [])]
    if not xs or len(xs) != len(ys):
        return [1.0] * len(frames)
    if len(xs) == 1:
        return [ys[0]] * len(frames)
    logs = [math.log(max(v, 1e-6)) for v in ys]
    out = []
    for f in frames:
        if f <= xs[0]:
            out.append(math.exp(logs[0]))
        elif f >= xs[-1]:
            out.append(math.exp(logs[-1]))
        else:
            # 二分找区间
            lo, hi = 0, len(xs) - 1
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if xs[mid] <= f:
                    lo = mid
                else:
                    hi = mid
            t = (f - xs[lo]) / max(xs[hi] - xs[lo], 1e-9)
            out.append(math.exp(logs[lo] * (1 - t) + logs[hi] * t))
    return out


def plan_has_curve(plan):
    return bool(plan.get("has_curve"))


def per_frame_lens(plan, shot, W, sensor_w_mm, frame_start=1,
                   fps_src=None, fps_dst=None, dense=True):
    """返回 [(blender_frame, lens_mm), ...]；没有曲线时退化为单点。

    `f_curve` 由 `tools/f_curve.py` 生成并写进 shot。锚定规则：
        · 该镜头有绝对 f ⇒ ratio 归一到 f_px 的**中位**，绝对值可信；
        · 只有倍率     ⇒ ratio 以镜头首帧为 1.0，**只能当相对变化**。

    dense=True  → 每帧一个关键帧（最准；旗舰镜头约 138 个）
    dense=False → 只在实测采样点与首尾写关键帧，其余交给 Blender 插值
    """
    fc = shot.get("f_curve") or {}
    f_px0 = shot.get("f_px")
    if not plan.get("writable") or f_px0 is None:
        return []
    src_fps = float(fps_src or 30.0)
    dst_fps = float(fps_dst or src_fps)
    f0 = int(shot["frame_range"][0])
    f1 = int(shot["frame_range"][1])

    def _bf(sf):
        return frame_start + int(round(sf * dst_fps / src_fps))

    if not fc.get("frames"):
        return [(_bf(f0), float(plan["lens_mm"]))]

    if dense:
        frames = list(range(f0, f1 + 1))
    else:
        pts = [int(v) for v in fc["frames"] if f0 <= int(v) <= f1]
        frames = sorted({f0, f1, *pts})
    ratios = interp_ratio(fc, frames)
    return [(_bf(fr), lens_mm(float(f_px0) * r, sensor_w_mm, W))
            for fr, r in zip(frames, ratios)]


def relative_curve_lens(shot, ai_lens, frame_start=1, fps_src=None,
                        fps_dst=None):
    """**配合 AI 用**：没有绝对 f、但有相对曲线时，把 AI 的 lens 乘上 ratio。

    这是方案 A 里最值钱的一条 —— 他们的 AI 给了 f，我的算法给了"怎么变"，
    相乘就得到可信的逐帧 f，**不需要任何拍摄参数**。
    """
    fc = shot.get("f_curve") or {}
    if not fc.get("frames") or ai_lens is None:
        return []
    src_fps = float(fps_src or 30.0)
    dst_fps = float(fps_dst or src_fps)
    f0 = int(shot["frame_range"][0])
    f1 = int(shot["frame_range"][1])
    frames = list(range(f0, f1 + 1))
    ratios = interp_ratio(fc, frames)
    return [(frame_start + int(round(fr * dst_fps / src_fps)),
             float(ai_lens) * r) for fr, r in zip(frames, ratios)]





# 给"只有相对曲线、没有绝对 f"的镜头找基准 lens。
# 这是 last-mile 的关键：拿到一个基准数，ratio 就能变成逐帧绝对 f。
BLENDER_DEFAULT_LENS = 50.0


def resolve_anchor(ai_lens=None, manual_anchor=0.0, current_lens=None,
                   default_lens=BLENDER_DEFAULT_LENS):
    """返回 (anchor_lens, source)：给相对曲线找一个乘上去的基准。

    优先级（人工的显式输入最高）：
        manual   面板里填的「锚点 lens」—— 用户明确指定，最可信
        ai       AI 在相机上留下的 `lens` F 曲线值
        current  相机当前的静态 lens，**但不能等于 Blender 默认 50mm**
                 （默认值不是任何人的结论，当基准会凭空造出一个 f）
        none     找不到
    """
    if manual_anchor and manual_anchor > 0.0:
        return float(manual_anchor), "manual"
    if ai_lens is not None and ai_lens > 0.0:
        return float(ai_lens), "ai"
    if (current_lens is not None and current_lens > 0.0
            and abs(current_lens - default_lens) > 1e-3):
        return float(current_lens), "current"
    return None, "none"


def anchor_note(source):
    return {
        "manual": "手工锚点（面板填写）",
        "ai": "AI 的 lens 关键帧",
        "current": "相机当前 lens（非默认值）",
        "none": "没有锚点，只能给相对变化",
    }.get(source, source)


def load_docs(json_dir):
    """读目录下所有 *.camera_solve.json，返回 [(文件名, doc), ...]。"""
    out = []
    if not os.path.isdir(json_dir):
        return out
    for fn in sorted(os.listdir(json_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(json_dir, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if d.get("schema") == "camera_solve/v1":
            out.append((fn, d))
    return out
