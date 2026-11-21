"""Stabilize large head observations against actual source inter-frame similarities."""

import math
from collections import defaultdict
import av
import cv2
import numpy as np

if __package__:
    from .fullclip_people_solve import measure, point
else:
    from fullclip_people_solve import measure, point


def weighted_median(values, weights):
    vals = np.asarray(values)
    w = np.asarray(weights)
    order = np.argsort(vals)
    return float(vals[order[np.searchsorted(np.cumsum(w[order]), w.sum() * 0.5)]])


def step(prev, curr, head, diameter):
    h, w = prev.shape
    x, y = head
    mask = np.zeros((h, w), np.uint8)
    radius = int(max(5, diameter * 0.42))
    cv2.circle(mask, (round(x), round(y)), radius, 255, -1)
    pts = cv2.goodFeaturesToTrack(prev, 95, 0.008, 3, mask=mask)
    if pts is None or len(pts) < 8:
        return np.zeros(2), 0.0, 0.0
    q, ok, _ = cv2.calcOpticalFlowPyrLK(prev, curr, pts, None, winSize=(21, 21), maxLevel=3)
    back, bok, _ = cv2.calcOpticalFlowPyrLK(curr, prev, q, None, winSize=(21, 21), maxLevel=3)
    keep = (ok[:, 0] > 0) & (bok[:, 0] > 0) & (np.linalg.norm((back - pts)[:, 0], axis=1) < 1.25)
    if np.count_nonzero(keep) < 7:
        return np.zeros(2), 0.0, 0.0
    old, new = pts[keep, 0], q[keep, 0]
    affine, inliers = cv2.estimateAffinePartial2D(
        old, new, method=cv2.RANSAC, ransacReprojThreshold=1.75, maxIters=500
    )
    if affine is None or np.count_nonzero(inliers) < 7:
        return np.median(new - old, axis=0), 0.0, 0.0
    ratio = math.hypot(affine[0, 0], affine[0, 1])
    displacement = affine[:, :2] @ head + affine[:, 2] - head
    quality = np.count_nonzero(inliers) / len(pts)
    if (
        not 0.92 <= ratio <= 1.09
        or np.linalg.norm(displacement) > diameter * 0.30
        or quality < 0.35
    ):
        return np.median(new - old, axis=0), 0.0, 0.0
    return displacement, math.log(ratio), float(quality)


def regularize_occluded_branches(centers, flow, diameters, direct, quality):
    """Resolve discontinuous weak loci against image motion inside one shot."""
    result = np.asarray(centers, dtype=float).copy()
    n = len(result)
    if n < 4:
        return result, []
    flow = np.asarray(flow, dtype=float)
    size = np.asarray(diameters, dtype=float)
    direct = np.asarray(direct, dtype=bool)
    observed_delta = np.diff(result, axis=0)
    mismatch = np.linalg.norm(observed_delta - flow[1:], axis=1)
    jump = (
        (mismatch > np.maximum(19.0, 0.13 * np.minimum(size[1:], size[:-1])))
        & ~direct[1:]
        & ~direct[:-1]
    )
    intervals = []
    for k in np.flatnonzero(jump) + 1:
        start = k - 1
        while start > 0 and k - start < 24 and not direct[start]:
            start -= 1
        end = k
        while end < n - 1 and end - k < 24 and not direct[end]:
            end += 1
        if intervals and start <= intervals[-1][1] + 1:
            intervals[-1] = (intervals[-1][0], max(end, intervals[-1][1]))
        else:
            intervals.append((start, end))
    if not intervals:
        return result, intervals
    from scipy.sparse import diags
    from scipy.sparse.linalg import spsolve

    for lo, hi in intervals:
        count = hi - lo + 1
        if count < 4:
            continue
        sub = slice(lo, hi + 1)
        raw = result[sub].copy()
        # Both image branches are hypotheses. Offset their influence by the
        # measured texture motion and a low-acceleration cue in the occluded
        # corridor. Real, directly corroborated movement keeps full weight.
        anchor_weights = np.where(direct[sub], 30.0, 0.095)
        anchor_weights[[0, -1]] = 120.0
        w = diags(anchor_weights, 0, shape=(count, count))
        slopes = diags([-np.ones(count - 1), np.ones(count - 1)], [0, 1], shape=(count - 1, count))
        curvature = diags(
            [np.ones(count - 2), -2 * np.ones(count - 2), np.ones(count - 2)],
            [0, 1, 2],
            shape=(count - 2, count),
        )
        motion = flow[lo + 1 : hi + 1]
        mv = diags(
            4.0 + 7.0 * np.asarray(quality[lo + 1 : hi + 1]), 0, shape=(count - 1, count - 1)
        )
        smooth = 16.0
        lhs = (w + slopes.T @ mv @ slopes + smooth * curvature.T @ curvature).tocsc()
        rhs = w @ raw + slopes.T @ mv @ motion + smooth * curvature.T @ np.diff(motion, axis=0)
        result[sub] = spsolve(lhs, rhs)
    return result, intervals


def resolve_branches(group, profile):
    centers = []
    flow = []
    diameters = []
    direct = []
    quality = []
    for _, actor, m, v, _, q in group:
        fit = actor.get("head_motion_fit")
        centers.append(fit["head_uv"] if fit else point(m["head"]))
        flow.append(v)
        diameters.append((fit["pixels_per_meter"] if fit else m["ppm"]) * profile["head_diameter"])
        # Border motion uses measurements on moving source texture. Keep its
        # authentic departure intact, including repeated source exposures.
        strong = bool(
            actor.get("border_motion")
            or actor.get("face", {}).get("score", 0) >= 0.76
            or actor.get("head_detection", {}).get("score", 0) >= 0.63
        )
        direct.append(strong)
        quality.append(q)
    corrected, intervals = regularize_occluded_branches(centers, flow, diameters, direct, quality)
    for lo, hi in intervals:
        # Use nearest direct face observations for a continuous orientation
        # hypothesis. Scores remain zero on weak frames; yaw is uncalibrated.
        valid = []
        for j, (_, a, m, _, _, _) in enumerate(group):
            face_score = a.get("face", {}).get("score", 0.0)
            support = a.get("shot_roll_support")
            if face_score >= 0.65 and (
                support is not None
                and np.dot(m["up"], support) > 0.85
                or support is None
                and face_score >= 0.78
            ):
                valid.append(j)
        for k in range(lo + 1, hi):
            frame, actor, m, _, _, _ = group[k]
            if direct[k] or "head_motion_fit" not in actor:
                continue
            a = max((v for v in valid if v <= k), default=None)
            b = min((v for v in valid if v >= k), default=None)
            yaw = None
            if a is not None and b is not None and a < b:
                u = (k - a) / (b - a)
                yaw = (1 - u) * group[a][2]["yaw"] + u * group[b][2]["yaw"]
            fit = actor["head_motion_fit"]
            actor["occlusion_branch"] = {
                "state": "unobserved_depth_motion_inferred",
                "source_frames": [group[lo][0], group[hi][0]],
                "unconstrained_head_uv": fit["head_uv"],
                "texture_motion_uv": np.array(flow[k]).tolist(),
                "yaw_proxy": yaw,
            }
            fit["head_uv"] = corrected[k].tolist()
            if yaw is not None:
                fit["yaw_proxy"] = float(yaw)


def stabilize(video, rows, width, height, profile):
    selected = {}
    preliminary = defaultdict(list)
    for row in rows:
        if len(row["actors"]) != 1:
            continue
        a = row["actors"][0]
        if "interpolation_endpoints" in a:
            continue
        m = measure(a, profile)
        if m["head"]:
            large = m["ppm"] * profile["head_diameter"] >= 0.23 * height
            preliminary[row["shot_id"]].append((row["source_frame"], a, m, large))
    for candidates in preliminary.values():
        established = sum(1 for _, _, _, large in candidates if large) >= 0.72 * len(candidates)
        for i, a, m, large in candidates:
            if large or (established and len(candidates) >= 5):
                selected[i] = (a, m)
    if not selected:
        return
    scale = min(1.0, 520 / width)
    groups = defaultdict(list)
    prev = None
    with av.open(str(video)) as c:
        for i, f in enumerate(c.decode(video=0)):
            if i not in selected:
                prev = None
                continue
            a, m = selected[i]
            gray = cv2.resize(
                f.to_ndarray(format="gray"), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )
            if prev and prev[0] == rows[i]["shot_id"] and prev[1] == i - 1:
                _, _, start, old, om = prev
                delta, sz, quality = step(
                    old,
                    gray,
                    point(om["head"]) * scale,
                    om["ppm"] * profile["head_diameter"] * scale,
                )
            else:
                start = i
                delta, sz, quality = np.zeros(2), 0.0, 0.0
            groups[start].append((i, a, m, delta / scale, sz, quality))
            prev = rows[i]["shot_id"], i, start, gray, m
    for group in groups.values():
        if len(group) < 4:
            continue
        travel = np.cumsum([q[3] for q in group], axis=0)
        zoom = np.cumsum([q[4] for q in group])
        for k, (frame, a, m, _, _, quality) in enumerate(group):
            if a.get("border_motion"):
                continue
            center_values = []
            size_values = []
            cw = []
            sw = []
            for j in range(max(0, k - 19), min(len(group), k + 20)):
                _, obs, measure_j, _, _, _ = group[j]
                weight = 0.70
                if obs.get("face"):
                    weight = 0.85 if obs["face"]["score"] >= 0.78 else 0.40
                elif obs.get("head_detection", {}).get("score", 0) >= 0.57:
                    weight = 0.87
                elif obs.get("border_motion"):
                    weight = 0.9
                distance = abs(k - j)
                size_values.append(math.log(measure_j["ppm"]) + zoom[k] - zoom[j])
                sw.append(weight * math.exp(-distance / 11))
                if distance <= 7:
                    center_values.append(point(measure_j["head"]) + travel[k] - travel[j])
                    cw.append(weight * math.exp(-distance / 3.3))
            centers = np.asarray(center_values)
            uv = [weighted_median(centers[:, axis], cw) for axis in (0, 1)]
            size = math.exp(weighted_median(size_values, sw))
            a["head_motion_fit"] = {
                "head_uv": uv,
                "pixels_per_meter": size,
                "measured_head_uv": point(m["head"]).tolist(),
                "measured_pixels_per_meter": m["ppm"],
                "same_frame_flow_inlier_fraction": quality,
                "source_interval": [
                    group[max(0, k - 19)][0],
                    group[min(len(group) - 1, k + 19)][0],
                ],
                "state": "source_motion_filtered",
            }
        resolve_branches(group, profile)
