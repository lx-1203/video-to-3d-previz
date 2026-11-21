"""Measure a person's screen size and refine only camera-relative root translation.

A detected face box is not an anatomical head outline. Accept a complete head
detector box or an image-supported silhouette, and record each cue separately.
The depth fit moves fixed-length geometry along its previously fitted shoulder ray.
"""

import json
import hashlib
import math
import os
import tempfile
from pathlib import Path

import numpy as np


def _read(value):
    return value if isinstance(value, dict) else json.loads(Path(value).read_bytes())


def _joint(actor, name):
    p = actor["joints2d"].get(name, {})
    if (
        p.get("visibility") not in ("visible", "observed", "model_estimate")
        or p.get("score", 0) < 0.39
    ):
        return None
    xy = np.array([p["u"], p["v"]], dtype=float)
    return (xy, float(p["score"])) if np.all(np.isfinite(xy)) else None


def _pair(actor, name):
    pair = [_joint(actor, s + "_" + name) for s in ("left", "right")]
    if any(p is None for p in pair):
        return None
    return np.vstack([p[0] for p in pair]), min(p[1] for p in pair)


def _torso(actor, w, h):
    a, b = _pair(actor, "shoulder"), _pair(actor, "hip")
    if a is None or b is None:
        return {}
    shoulder, hip = a[0].mean(axis=0), b[0].mean(axis=0)
    axis = hip - shoulder
    if axis[1] < max(12, 0.58 * np.linalg.norm(axis)) or np.any(np.vstack([a[0], b[0]]) < 0):
        return {}
    if np.any(np.vstack([a[0], b[0]]) >= [w - 1, h - 1]):
        return {}
    span = np.linalg.norm(a[0][0] - a[0][1])
    height = np.linalg.norm(axis)
    return dict(
        torso_px=round(float(height), 3),
        shoulder_px=round(float(span), 3),
        confidence=round(min(a[1], b[1]), 4),
        shoulder_uv=shoulder.round(2).tolist(),
        hip_uv=hip.round(2).tolist(),
        state="joint_observations",
    )


def _head_matches(actors, items):
    boxes = []
    for item in sorted(items, key=lambda r: -r.get("score", 0)):
        if item.get("class_id", 1) != 1 or item.get("score", 0) < 0.51:
            continue
        b = np.asarray(item["bbox"], float)
        wh = b[2:] - b[:2]
        if min(wh) < 8 or not 0.40 < wh[0] / wh[1] < 1.65:
            continue
        if any(
            np.linalg.norm((b[:2] + b[2:] - c[:2] - c[2:]) / 2) < 0.30 * min(wh) for c, _ in boxes
        ):
            continue
        boxes.append((b, float(item["score"])))
    matches = []
    for k, actor in enumerate(actors):
        box = np.asarray(actor["bbox"])
        a = actor.get("anchors", {}).get("head", {})
        ref = None
        if actor.get("face"):
            f = np.asarray(actor["face"]["bbox"])
            ref = (f[:2] + f[2:]) / 2
        elif a.get("u") is not None:
            ref = np.array([a["u"], a["v"]])
        elif (p := _joint(actor, "nose")) is not None:
            ref = p[0]
        if ref is None:
            continue
        for j, (b, score) in enumerate(boxes):
            center = (b[:2] + b[2:]) / 2
            size = b[2:] - b[:2]
            if not (
                box[0] - 0.10 * size[0] <= center[0] <= box[2] + 0.10 * size[0]
                and box[1] - 0.2 * size[1] <= center[1] <= box[3]
            ):
                continue
            error = np.linalg.norm((center - ref) / size)
            if error < 0.79:
                matches.append((float(error) - 0.08 * score, k, j))
    result = {}
    used = set()
    for _, k, j in sorted(matches):
        if k not in result and j not in used:
            result[k] = boxes[j]
            used.add(j)
    return result


def _head_outline(im, box, score, from_face=False):
    import cv2

    h, w = im.shape[:2]
    box = np.asarray(box, float)
    lo, hi = box[:2].copy(), box[2:].copy()
    wh = hi - lo
    if min(wh) < 9:
        return {}
    if from_face:
        lo -= wh * [0.36, 0.46]
        hi += wh * [0.28, 0.04]
        wh = hi - lo
    if np.any(lo < 1) or np.any(hi >= [w - 1, h - 1]):
        return {}
    center = (lo + hi) / 2
    # Sample raw source boundaries around the box before using its width or height.
    # Taking medians of many stripes discounts isolated hair wisps and eye edges.
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)

    def edges(dim):
        # Transpose for horizontal and vertical profiles to keep the same units.
        view = gray if dim == 0 else gray.T
        side_axis = 1 - dim
        mids = np.unique(
            np.rint(
                np.linspace(
                    center[side_axis] - 0.22 * wh[side_axis],
                    center[side_axis] + 0.22 * wh[side_axis],
                    13,
                )
            ).astype(int)
        ).clip(0, view.shape[0] - 1)
        strip = view[mids].astype(float)
        strip = (strip[:, :-2] + 2 * strip[:, 1:-1] + strip[:, 2:]) * 0.25
        grad = np.abs(strip[:, 2:] - strip[:, :-2])
        locations = []
        qualities = []
        for coordinate in (lo[dim], hi[dim]):
            rad = max(2.0, wh[dim] * (0.20 if from_face else 0.12))
            x0 = max(0, int(coordinate - rad) - 2)
            x1 = min(grad.shape[1], int(coordinate + rad))
            if x1 - x0 < 2:
                return None
            loc = np.argmax(grad[:, x0:x1], axis=1)
            strength = grad[np.arange(len(mids)), x0 + loc]
            xs = x0 + loc + 2
            good = strength > max(5.0, 0.14 * np.percentile(np.ptp(strip, axis=1), 65))
            if good.mean() < 0.42:
                return None
            estimate = np.median(xs[good])
            coherent = np.abs(xs - estimate) < max(2.0, 0.095 * wh[dim])
            if (good & coherent).mean() < 0.36:
                return None
            locations.append(float(estimate))
            qualities.append(float((good & coherent).mean()))
        return locations, min(qualities)

    ew, eh = edges(0), edges(1)
    if from_face and (not ew or not eh):
        return {}
    out = np.r_[lo, hi]
    states = []
    for dim, item in enumerate((ew, eh)):
        if item:
            side, quality = item
            size = side[1] - side[0]
            if 0.70 * wh[dim] < size < 1.35 * wh[dim]:
                out[dim], out[dim + 2] = side
                states.append("source_edge")
                continue
        if from_face:
            return {}
        states.append("head_detector")
    return dict(
        head_box=out.round(2).tolist(),
        head_score=round(float(score) * (0.73 if from_face else 1.0), 4),
        head_edges=states,
        head_source="face_seeded_source_contour" if from_face else "head_detector_and_source",
    )


def _smooth_source(frames):
    groups = {}
    for i, row in enumerate(frames):
        for tid, obs in row["actors"].items():
            groups.setdefault((row["shot_id"], tid), []).append((i, obs))
    for entries in groups.values():
        raw = {i: dict(q) for i, q in entries}
        for i, obs in entries:
            if not obs.get("torso_px") and obs["bbox_kind"] == "bounded_person_flow":
                prev = next(
                    (
                        raw[f]
                        for f in range(i - 1, i - 7, -1)
                        if f in raw and raw[f].get("torso_px")
                    ),
                    None,
                )
                after = next(
                    (raw[f] for f in range(i + 1, i + 7) if f in raw and raw[f].get("torso_px")),
                    None,
                )
                if prev and after and abs(math.log(prev["torso_px"] / after["torso_px"])) < 0.20:
                    # The buffer only fills measurements; it does not invent visible joints.
                    for name in ("shoulder_uv", "hip_uv"):
                        obs[name] = np.mean([prev[name], after[name]], axis=0).round(2).tolist()
                    for name in ("torso_px", "shoulder_px"):
                        obs[name] = round((prev[name] + after[name]) * 0.5, 3)
                    obs.update(
                        state="bounded_neighbor_inference",
                        confidence=round(0.62 * min(prev["confidence"], after["confidence"]), 4),
                    )
            for name in ("torso_px", "shoulder_px"):
                if name not in obs:
                    continue
                values = [raw[f][name] for f in range(i - 2, i + 3) if f in raw and name in raw[f]]
                if len(values) >= 3:
                    obs[name] = round(float(np.median(values)), 3)


def _record_interactions(frames, people, props):
    # A 2-D hand relationship is not proof of physical contact. Use these records
    # to preserve already-correct projected hand positions while estimating depth.
    for tr in props["tracks"]:
        seq = {r["frame"]: r for r in tr["samples"]}
        for i, s in seq.items():
            actor_map = {str(a["track_id"]): a for a in people["frames"][i]["actors"]}
            refs = {}
            for f in range(i - 4, i + 5):
                if f not in seq or frames[f]["shot_id"] != frames[i]["shot_id"]:
                    continue
                for c in seq[f]["contacts"]:
                    key = (str(c["track_id"]), c["side"])
                    refs[key] = refs.get(key, 0.0) + (5 - abs(f - i)) / 25.0
            active = {}
            for (tid, side), weight in refs.items():
                if tid not in actor_map or weight < 0.09:
                    continue
                actor = actor_map[tid]
                wrist = actor["joints2d"].get(side + "_wrist", {})
                if wrist.get("u") is None or wrist.get("v") is None:
                    continue
                wrist_uv = np.array([wrist["u"], wrist["v"]])
                if np.linalg.norm(wrist_uv - s["center_uv"]) > max(12.0, 0.90 * s["axes_px"][0]):
                    continue
                direct = any(
                    str(c["track_id"]) == tid and c["side"] == side for c in s["contacts"]
                )
                if not direct and weight < 0.27:
                    continue
                obs = frames[i]["actors"][tid]
                obs.setdefault("grip_anchors", []).append(
                    dict(
                        side=side,
                        weight=round(min(1.0, weight), 4),
                        prop_id=tr["id"],
                        polygon_uv=s["polygon_uv"],
                        extent=s["axes_px"][0],
                        state="observed_2d_proximity" if direct else "neighboring_2d_proximity",
                    )
                )
                active[tid] = max(weight, active.get(tid, 0.0))
            for tid in set(active):
                others = set(active) - {tid}
                if others:
                    obs = frames[i]["actors"][tid]
                    partners = []
                    for other in sorted(others):
                        target = frames[i]["actors"][other]
                        if target.get("head_box"):
                            hb = target["head_box"]
                            x = (hb[0] + hb[2]) * 0.5
                        else:
                            ref = actor_map[other].get("anchors", {}).get("head", {})
                            if not ref or ref.get("u") is None:
                                continue
                            x = ref["u"]
                        partners.append(
                            dict(
                                track_id=other,
                                head_u=round(x, 2),
                                weight=round(min(active[tid], active[other]), 4),
                            )
                        )
                    obs["partners"] = partners


def _observed_yaws(frames, people):
    for i, row in enumerate(frames):
        for actor in people["frames"][i]["actors"]:
            tid = str(actor["track_id"])
            q = row["actors"][tid]
            ori = actor.get("orientation", {})
            if ori.get("face_score", 0) > 0.45 and abs(ori.get("face_yaw_proxy", 0)) > 0.30:
                q["facing_yaw"] = float(ori["face_yaw_proxy"])
                q["facing_state"] = "observed_face"
    for i, row in enumerate(frames):
        for tid, q in row["actors"].items():
            if "facing_yaw" in q or q.get("bbox_kind") != "bounded_person_flow":
                continue
            neighbors = []
            for sign in (-1, 1):
                for step in range(1, 6):
                    f = i + sign * step
                    if not 0 <= f < len(frames) or frames[f]["shot_id"] != row["shot_id"]:
                        break
                    a = frames[f]["actors"].get(tid, {})
                    if a.get("facing_state") == "observed_face":
                        neighbors.append(a["facing_yaw"])
                        break
            if len(neighbors) == 2 and neighbors[0] * neighbors[1] > 0:
                q["facing_yaw"] = float(np.mean(neighbors))
                q["facing_state"] = "bounded_face_inference"


def interaction_torso_axes(actor, observation, parts):
    # Only infer torso yaw when both a turned face and foreshortened shoulders
    # support the side of a second actor beside the same observed object.
    q = observation or {}
    if not q.get("partners") or not q.get("head_box") or not q.get("torso_px"):
        return None
    yaw = q.get("facing_yaw", 0)
    hb = q["head_box"]
    u = (hb[0] + hb[2]) * 0.5
    if abs(yaw) < 0.35 or not any(
        (p["head_u"] - u) * yaw > max(6.0, 0.14 * (hb[2] - hb[0])) for p in q["partners"]
    ):
        return None

    def center(name):
        return np.asarray(parts[name]["proximal_endpoint"])

    shoulder = [center("DEF-upper_arm." + side) for side in ("L", "R")]
    hips = [center("DEF-thigh." + side) for side in ("L", "R")]
    breadth = np.linalg.norm(shoulder[0] - shoulder[1])
    height = np.linalg.norm(np.mean(shoulder, axis=0) - np.mean(hips, axis=0))
    if breadth < 1e-6:
        return None
    cosine = q.get("shoulder_px", 0) * height / (q["torso_px"] * breadth)
    if not 0.04 < cosine < 0.92:
        return None
    js = actor["joints3d"]
    old = np.asarray(js["left_shoulder"], float) - js["right_shoulder"]
    up = np.asarray(js["shoulder"], float) - js["hip"]
    up /= max(1e-8, np.linalg.norm(up))
    if up[2] < 0.65:
        return None
    horizontal = np.array([old[0], 0.0, old[2]])
    horizontal /= max(1e-8, np.linalg.norm(horizontal))
    proposed = horizontal * cosine + np.array(
        [0.0, math.copysign(math.sqrt(1 - cosine**2), yaw), 0.0]
    )
    proposed -= up * np.dot(up, proposed)
    proposed /= max(1e-8, np.linalg.norm(proposed))
    weight = max((p.get("weight", 0.0) for p in q["partners"]), default=0.0)
    if weight <= 0:
        return None
    old /= max(1e-8, np.linalg.norm(old))
    axis = (1 - weight) * old + weight * proposed
    norm = np.linalg.norm(axis)
    if norm < 0.1:
        return None
    return tuple(up), tuple(axis / norm)


def _nearest_polygon(point, poly):
    poly = np.asarray(poly, float)
    a = poly
    edges = np.roll(poly, -1, axis=0) - poly
    t = np.clip(
        np.einsum("ij,ij->i", point - a, edges) / np.maximum(1e-8, np.sum(edges * edges, axis=1)),
        0,
        1,
    )
    edgepts = a + t[:, None] * edges
    near = edgepts[np.argmin(np.linalg.norm(point - edgepts, axis=1))]
    # Even-odd fill also handles a concave visible silhouette.
    y = point[1]
    b = a + edges
    crossings = (a[:, 1] > y) != (b[:, 1] > y)
    active = np.flatnonzero(crossings)
    hits = 0
    for k in active:
        hits += point[0] < (b[k, 0] - a[k, 0]) * (y - a[k, 1]) / (b[k, 1] - a[k, 1]) + a[k, 0]
    return np.array(point) if hits % 2 else near


def _distance_to_polygon(point, poly):
    return float(np.linalg.norm(np.asarray(point) - _nearest_polygon(point, poly)))


def measure_body_sizes(source, people, output, *, heads=None, props=None):
    import av

    data = _read(people)
    head_data = _read(heads) if heads else None
    with Path(source).open("rb") as fh:
        sha = hashlib.file_digest(fh, "sha256").hexdigest()
    if data.get("source_sha256") != sha or (head_data and head_data.get("source_sha256") != sha):
        raise ValueError("Body measurements require observations from the same source video")
    frames = []
    from fractions import Fraction

    with av.open(str(source)) as stream:
        for i, frame in enumerate(stream.decode(video=0)):
            if i >= len(data["frames"]) or (head_data and i >= len(head_data["frames"])):
                raise ValueError("Incomplete body-size timeline")
            row = data["frames"][i]
            if row["source_frame"] != i or frame.pts * frame.time_base != Fraction(
                row["pts"]
            ) * Fraction(row["time_base"]):
                raise ValueError("People/source PTS mismatch")
            im = frame.to_ndarray(format="bgr24")
            h, w = im.shape[:2]
            if (w, h) != (data["width"], data["height"]):
                raise ValueError("People/source image dimensions differ")
            actors = row["actors"]
            items = []
            if head_data:
                hr = head_data["frames"][i]
                if (
                    hr["source_frame"] != i
                    or Fraction(hr["pts"]) * Fraction(hr["time_base"])
                    != frame.pts * frame.time_base
                ):
                    raise ValueError("Head/source PTS mismatch")
                items = hr["items"]
            matches = _head_matches(actors, items)
            records = {}
            for k, actor in enumerate(actors):
                obs = _torso(actor, w, h)
                obs["bbox_kind"] = actor.get("bbox_kind", "")
                if k in matches:
                    box, score = matches[k]
                    obs.update(_head_outline(im, box, score))
                elif actor.get("face") and actor["face"].get("score", 0) > 0.70:
                    obs.update(
                        _head_outline(
                            im,
                            np.asarray(actor["face"]["bbox"]),
                            actor["face"]["score"],
                            True,
                        )
                    )
                records[str(actor["track_id"])] = obs
            frames.append(dict(source_frame=i, shot_id=row["shot_id"], actors=records))
    if len(frames) != len(data["frames"]) or (
        head_data and len(frames) != len(head_data["frames"])
    ):
        raise ValueError("Incomplete body-size timeline")
    _smooth_source(frames)
    _observed_yaws(frames, data)
    if props is not None:
        prop_data = _read(props)
        if prop_data.get("source_sha256") != sha:
            raise ValueError("Body/prop source mismatch")
        _record_interactions(frames, data, prop_data)
    result = dict(
        schema="orderflow.body_size.v1",
        source_sha256=sha,
        width=data["width"],
        height=data["height"],
        frames=frames,
    )
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf8", dir=dest.parent, delete=False
        ) as fh:
            temp = fh.name
            json.dump(result, fh, allow_nan=False, separators=(",", ":"))
        os.replace(temp, dest)
    finally:
        if temp:
            Path(temp).unlink(missing_ok=True)
    return result


def neutral_head_offsets(mesh, parts):
    """Collect once from the approved neutral mesh; these coordinates are never edited."""
    head = parts["DEF-spine.006"]
    return (
        np.array([tuple(mesh.data.vertices[i].co) for i in head["vertex_indices"]])
        - head["proximal_endpoint"]
    )


def posed_head_vertices(offsets, pose):
    pos, rot = pose["DEF-spine.006"]
    return offsets @ np.asarray(rot).T + np.asarray(pos)


def _constrain_centers(loc, depth_factor, pivot, q, lm, head, p, width, height):
    sh = (np.asarray(lm["left_shoulder"]) + lm["right_shoulder"]) * 0.5
    shape = q.get("head_box")
    focal = np.array([p[0, 0] * width * 0.5, -p[1, 1] * height * 0.5])
    principal = np.array([(1 - p[0, 2]) * width / 2, (1 + p[1, 2]) * height / 2])

    def xy(pt, at):
        d = pt + at
        return principal + d[[0, 2]] / d[1] * focal

    constraints = []

    def add(name, pt, target, weight, uncertainty, poly=False):
        pt = np.asarray(pt)
        old_xy = xy(pt, loc)
        before = (
            _distance_to_polygon(old_xy, target)
            if poly
            else float(np.linalg.norm(old_xy - target))
        )
        tolerance = before + max(2.0, uncertainty * 0.33)
        constraints.append(
            (
                name,
                pt,
                np.asarray(target),
                weight,
                max(uncertainty, tolerance),
                before,
                poly,
            )
        )

    if shape and len(head):
        bbox = np.array(shape)
        head_center = np.asarray(lm.get("head", head.mean(axis=0)))
        add(
            "head_center",
            head_center,
            (bbox[:2] + bbox[2:]) * 0.5,
            1.4 * q["head_score"],
            (bbox[3] - bbox[1]) * 0.085,
        )
    add(
        "shoulder_center",
        sh,
        np.asarray(q.get("shoulder_uv", xy(sh, loc))),
        1.5 * q.get("confidence", 0.45),
        q["torso_px"] * 0.055,
    )
    for g in q.get("grip_anchors", []):
        k = g["side"] + "_wrist"
        pt = np.asarray(lm[k]) if k in lm else None
        if pt is None:
            continue
        if _distance_to_polygon(xy(pt, loc), g["polygon_uv"]) < max(5.0, 0.24 * g["extent"]):
            add(
                k,
                pt,
                g["polygon_uv"],
                2.6 * g["weight"],
                g["extent"] * (0.055 + 0.12 * (1 - g["weight"])),
                True,
            )
    anchor = pivot + loc
    best = None
    # For a proposed depth, image shifts from camera-local x/z translation are
    # linear. Intersect the admissible head, shoulder and visible-grip regions.
    # Give up part of the enlargement if the fixed template cannot match them all.
    for amount in np.linspace(1.0, 0.0, 13):
        factor = 1 + (depth_factor - 1) * amount
        at = anchor * factor - pivot
        active = []
        desired = []
        weights = []
        for name, pt, target, weight, tolerance, before, poly in constraints:
            measured = xy(pt, at)
            gain = (anchor[1] * factor) / (pt[1] + at[1])
            target_xy = _nearest_polygon(measured, target) if poly else target
            desired.append((target_xy - measured) / gain)
            weights.append(weight * gain * gain)
            active.append((name, measured, gain, target, tolerance, before, poly))
        shift = np.average(desired, axis=0, weights=weights)
        for _ in range(24):
            previous = shift.copy()
            for _, measured, gain, target, tolerance, _, poly in active:
                now = measured + gain * shift
                delta = now - (_nearest_polygon(now, target) if poly else target)
                error = np.linalg.norm(delta)
                if error > tolerance:
                    shift -= (1.0 - tolerance / error) * delta / gain
            if np.linalg.norm(shift - previous) < 0.01:
                break
        metrics = {}
        excess = 0.0
        for name, measured, gain, target, tolerance, before, poly in active:
            now = measured + gain * shift
            error = (
                _distance_to_polygon(now, target) if poly else float(np.linalg.norm(now - target))
            )
            metrics[name] = dict(
                before=round(before, 2),
                after=round(error, 2),
                allowed=round(tolerance, 2),
            )
            excess = max(excess, error - tolerance)
        out = at.copy()
        out[[0, 2]] += shift * anchor[1] * factor / focal
        best = (out, factor, metrics)
        if excess < 0.25:
            break
    return best


def adjust_body_depth(
    actor, observation, landmarks, head_vertices, location, projection, width, height
):
    """Return translation and fit residuals using the observed head, shoulders and nearby wrist."""
    loc = np.asarray(location, dtype=float)
    report = {"applied": False, "state": "insufficient_body_evidence"}
    if (
        not observation
        or not observation.get("torso_px")
        or observation.get("confidence", 0) < 0.24
    ):
        return loc.copy(), report
    if abs(np.asarray(projection)[3, 2]) < 1e-6:
        return loc.copy(), {"applied": False, "state": "no_perspective_size_cue"}
    required = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    if any(k not in landmarks for k in required):
        return loc.copy(), report
    sh = np.array([landmarks[k] for k in required[:2]])
    hp = np.array([landmarks[k] for k in required[2:]])
    head = np.asarray(head_vertices) if head_vertices is not None else np.zeros((0, 3))
    shoulder = sh.mean(axis=0)
    pivot = shoulder
    pivot_state = "shoulder_ray"
    if observation.get("grip_anchors"):
        pr = np.asarray(projection)
        candidates = []
        for g in observation["grip_anchors"]:
            name = g["side"] + "_wrist"
            if name not in landmarks:
                continue
            wrist = np.asarray(landmarks[name])
            q = wrist + loc
            if q[1] <= 0:
                continue
            xy = np.array(
                [
                    (1 - pr[0, 2]) * width / 2 + pr[0, 0] * width / 2 * q[0] / q[1],
                    (1 + pr[1, 2]) * height / 2 - pr[1, 1] * height / 2 * q[2] / q[1],
                ]
            )
            dist = _distance_to_polygon(xy, g["polygon_uv"])
            limit = max(4.0, g["extent"] * 0.23)
            if dist < limit:
                confidence = g["weight"] * max(0.0, 1 - dist / limit)
                candidates.append((confidence, wrist))
        if candidates:
            blend = max(v[0] for v in candidates)
            grip = np.average(
                [a for _, a in candidates], axis=0, weights=[v for v, _ in candidates]
            )
            pivot = (1 - blend) * shoulder + blend * grip
            pivot_state = "visible_prop_wrist_ray"
    anchor = pivot + loc
    if anchor[1] <= 0:
        return loc.copy(), report
    ray = anchor / anchor[1]
    p = np.asarray(projection)
    focal = np.array([p[0, 0] * width / 2, -p[1, 1] * height / 2])

    def lengths(at):
        def uv(points):
            world = points + at
            if np.any(world[:, 1] <= 0):
                return None
            return world[:, [0, 2]] / world[:, 1, None] * focal

        points = uv(np.vstack((sh, hp)))
        if points is None:
            return {}
        sizes = {
            "torso_px": np.linalg.norm(points[:2].mean(axis=0) - points[2:].mean(axis=0)),
            "shoulder_px": np.linalg.norm(points[0] - points[1]),
        }
        if len(head):
            q = uv(head)
            if q is not None:
                sizes["head_w"], sizes["head_h"] = np.ptp(q, axis=0)
        return sizes

    target = {"torso_px": float(observation["torso_px"])}
    weights = {"torso_px": 2.6 * observation["confidence"]}
    # At side view, small u/v shoulder separations primarily measure yaw, not scale.
    span = observation.get("shoulder_px", 0)
    if span > 0.40 * target["torso_px"]:
        target["shoulder_px"] = span
        weights["shoulder_px"] = 0.43 * observation["confidence"]
    hb = observation.get("head_box")
    if hb is not None and len(head) and observation.get("head_score", 0) > 0.47:
        hw, hh = np.asarray(hb[2:]) - hb[:2]
        if min(hw, hh) > 7 and 0.18 < hh / target["torso_px"] < 1.05:
            target.update(head_w=float(hw), head_h=float(hh))
            head_weight = observation["head_score"]
            weights.update(head_w=0.75 * head_weight, head_h=0.88 * head_weight)
    # Robust log ratios avoid allowing any one large target to dominate. The only
    # free parameter is distance along the shoulder ray in template units.
    old = lengths(loc)
    if not all(old.get(k, 0) > 0 for k in target):
        return loc.copy(), report
    scales = np.asarray([old[k] / target[k] for k in target])
    if not np.all(np.isfinite(scales)):
        return loc.copy(), report
    # A body without a head constraint still needs two independent upper-body spans,
    # except where both reliably measured hip and shoulder pairs support its torso.
    if len(target) == 1 and observation["confidence"] < 0.52:
        return loc.copy(), report
    low = max(0.5, min(1.0, float(scales.min()) * 0.91))
    hi = min(1.8, max(1.0, float(scales.max()) * 1.08))

    def cost(factor):
        t = ray * (anchor[1] * factor) - pivot
        got = lengths(t)
        if not all(got.get(k, 0) > 0 for k in target):
            return 1e4
        error = np.array([math.log(got[k] / target[k]) for k in target])
        w = np.array([weights[k] for k in target])
        return (
            np.sum(w * (np.sqrt(1 + (error / 0.16) ** 2) - 1))
            + 0.13 * (math.log(factor) / 0.20) ** 2
        )

    a, b = low, hi
    for _ in range(21):
        one = a + (b - a) / 3
        two = b - (b - a) / 3
        if cost(one) < cost(two):
            b = two
        else:
            a = one
    factor = float((a + b) * 0.5)
    new, factor, anchor_metrics = _constrain_centers(
        loc, factor, pivot, observation, landmarks, head, p, width, height
    )
    report = dict(
        anchor_errors_px=anchor_metrics,
        applied=True,
        state="depth_fit",
        pivot=pivot_state,
        depth_factor=round(factor, 6),
        source=target,
        before={k: round(float(old[k]), 3) for k in target},
        after={k: round(float(v), 3) for k, v in lengths(new).items() if k in target},
        source_state=observation.get("state"),
        face_yaw=actor.get("orientation", {}).get("face_yaw_proxy"),
    )
    return new, report


def _reach(ray, shoulder, length):
    rr = np.dot(ray, ray)
    center = np.dot(ray, shoulder) / rr
    width2 = (length**2 - np.dot(ray * center - shoulder, ray * center - shoulder)) / rr
    return (center - math.sqrt(width2), center + math.sqrt(width2)) if width2 >= 0 else None


def _screen(xyz, p, w, h):
    a = np.asarray(xyz)
    q = a @ p[:, :3].T + p[:, 3]
    return np.stack(
        [(1 + q[..., 0] / q[..., 3]) * w / 2, (1 - q[..., 1] / q[..., 3]) * h / 2],
        axis=-1,
    )


def _elbow_candidates(chain, z, p, w, h):
    shoulder, old = chain["shoulder"], chain["elbow"]
    wrist = chain["ray"] * z
    distance = np.linalg.norm(wrist - shoulder)
    a, b = chain["lengths"]
    if not abs(a - b) + 1e-5 < distance < a + b - 1e-6:
        return None
    line = (wrist - shoulder) / distance
    along = (distance**2 + a * a - b * b) / (2 * distance)
    radius = math.sqrt(max(0.0, a * a - along * along))
    center = shoulder + line * along
    side = old - center
    side -= np.dot(side, line) * line
    if np.linalg.norm(side) < 1e-6:
        side = np.cross(line, np.eye(3)[np.argmin(np.abs(line))])
    side /= np.linalg.norm(side)
    angles = np.arange(64) * 2 * np.pi / 64
    ring = center + radius * (
        np.cos(angles)[:, None] * side + np.sin(angles)[:, None] * np.cross(line, side)
    )
    error = np.linalg.norm(_screen(ring, p, w, h) - chain["source_elbow"], axis=1)
    # Limit ambiguity between the two elbow branches with the original calibrated pose.
    score = error / chain["tolerance"] + 0.12 * np.linalg.norm(ring - old, axis=1) / a
    best = np.argmin(np.where(error <= chain["tolerance"], score, 1e4 + score))
    return dict(
        elbow=ring[best],
        wrist=wrist,
        error=float(error[best]),
        score=float(score[best]),
    )


def _fade_goal(chain, goal, strength, p, w, h):
    if strength >= 1.0:
        return goal
    # Blend the wrist position before solving the joints. Independent rotation
    # blends swing the palm in an arc that can briefly leave the visible prop.
    wrist = (1 - strength) * chain["wrist"] + strength * goal["wrist"]
    desired = (1 - strength) * _screen(chain["elbow"], p, w, h) + strength * _screen(
        goal["elbow"], p, w, h
    )
    phased = dict(chain, ray=wrist / -wrist[2], source_elbow=desired)
    result = _elbow_candidates(phased, -wrist[2], p, w, h)
    if result:
        result["error"] = float(
            np.linalg.norm(_screen(result["elbow"], p, w, h) - chain["source_elbow"])
        )
    return result


def _choose_common_depth(chains, p, w, h):
    low = max(c["interval"][0] for c in chains)
    high = min(c["interval"][1] for c in chains)
    if high <= low:
        return None, {"reason": "disjoint_reach", "gap": round(low - high, 5)}
    best = None
    for z in np.linspace(low, high, 39):
        wishes = [_elbow_candidates(c, z, p, w, h) for c in chains]
        if any(item is None for item in wishes):
            continue
        score = sum(t["score"] for t in wishes) + 0.10 * sum(
            abs(z + c["wrist"][2]) / sum(c["lengths"]) for c in chains
        )
        if any(t["error"] > c["tolerance"] for t, c in zip(wishes, chains)):
            score += 1e4
        if best is None or score < best[0]:
            best = (score, wishes, z)
    if best is None:
        return None, {"reason": "no_fixed_length_solution"}
    _, wishes, z = best
    if any(t["error"] > c["tolerance"] for t, c in zip(wishes, chains)):
        return None, {
            "reason": "source_elbow_outside_interval",
            "elbow_error": [round(t["error"], 2) for t in wishes],
        }
    return wishes, {
        "state": "inferred_monocular_contact",
        "depth": round(float(z), 5),
        "interval": [round(float(low), 5), round(float(high), 5)],
    }


def _world_chains(scene, cam, projection, w, h, actors, obs, rigs):
    import bpy
    from mathutils import Vector

    deps = bpy.context.evaluated_depsgraph_get()
    mw = cam.matrix_world.normalized()
    iv = mw.inverted()
    proj_inv = projection.inverted()
    by_prop = {}
    free_arms = {}
    for actor in actors:
        tid = str(actor["track_id"])
        source = obs.get(tid, {})
        if not source.get("partners") or not source.get("grip_anchors"):
            continue
        rig = rigs.get(tid)

        if rig is None or rig.hide_render:
            continue
        er = rig.evaluated_get(deps)
        visible_props = {g["prop_id"]: g for g in source["grip_anchors"]}
        contexts = [
            dict(g, side=side, context=True)
            for g in visible_props.values()
            for side in ("left", "right")
        ]
        for hand in [*source["grip_anchors"], *contexts]:
            secondary = hand.get("context", False)
            side = hand["side"]
            suffix = ".L" if side == "left" else ".R"
            j = actor["joints2d"]
            el = j.get(side + "_elbow", {})
            wr = j.get(side + "_wrist", {})
            if (
                (
                    not secondary
                    and (hand["state"] != "observed_2d_proximity" or hand["weight"] < 0.42)
                )
                or el.get("visibility") not in ("observed", "visible", "model_estimate")
                or el.get("score", 0) < 0.40
            ):
                continue
            if (
                wr.get("visibility") not in ("observed", "visible", "model_estimate")
                or wr.get("score", 0) < 0.40
            ):
                continue
            target = _nearest_polygon(np.asarray([wr["u"], wr["v"]]), hand["polygon_uv"])
            wrist_uv = np.array([wr["u"], wr["v"]])
            if secondary:
                target = wrist_uv
            elbow_uv = np.array([el["u"], el["v"]])
            if np.linalg.norm(elbow_uv - target) < 6:
                continue
            upper = er.pose.bones.get("DEF-upper_arm" + suffix)
            fore = er.pose.bones.get("DEF-forearm" + suffix)
            if upper is None or fore is None or fore.parent != upper:
                continue

            def pt(v):
                return np.asarray(iv @ (er.matrix_world @ v))

            shoulder, elbow, wrist = pt(upper.head), pt(upper.tail), pt(fore.tail)
            lengths = np.array([np.linalg.norm(elbow - shoulder), np.linalg.norm(wrist - elbow)])
            clip = proj_inv @ Vector((2 * target[0] / w - 1, 1 - 2 * target[1] / h, 0, 1))
            ray = np.asarray(clip.to_3d() / -clip.z)
            interval = _reach(ray, shoulder, sum(lengths))
            if interval is None:
                continue
            chain = dict(
                projection=np.asarray(projection),
                tid=tid,
                rig=rig,
                side=side,
                upper=upper.name,
                fore=fore.name,
                prop=hand["prop_id"],
                ray=ray,
                interval=interval,
                shoulder=shoulder,
                elbow=elbow,
                wrist=wrist,
                lengths=lengths,
                source_elbow=elbow_uv,
                tolerance=max(7.0, np.linalg.norm(elbow_uv - target) * 0.19),
                weight=hand["weight"],
                cam_to_rig=np.asarray(er.matrix_world.inverted() @ mw)[:3, :3],
                upper_q=upper.matrix.to_quaternion().copy(),
                fore_q=fore.matrix.to_quaternion().copy(),
            )
            if secondary:
                center = np.mean(hand["polygon_uv"], axis=0)

                def distance_to_segment(a, b):
                    diff = b - a
                    t = np.clip(
                        np.dot(center - a, diff) / max(1e-8, np.dot(diff, diff)),
                        0.0,
                        1.0,
                    )
                    return np.linalg.norm(a + t * diff - center)

                predicted = _screen(np.vstack((elbow, wrist)), chain["projection"], w, h)
                observed = distance_to_segment(elbow_uv, wrist_uv)
                old = distance_to_segment(*predicted)
                extent = hand["extent"]
                if (
                    np.linalg.norm(predicted[1] - wrist_uv)
                    > 0.3 * np.linalg.norm(wrist_uv - elbow_uv)
                    and np.linalg.norm(center - wrist_uv) < 1.25 * extent
                    and old < 0.4 * extent
                    and observed > old + 0.1 * extent
                ):
                    free_arms[hand["prop_id"], tid, side] = chain
                continue
            group = by_prop.setdefault(hand["prop_id"], {})
            # Wrist is an anatomical joint; the template palm ends a few pixels away.
            # Search only the visible contour near the measured wrist.
            polygon = np.asarray(hand["polygon_uv"], float)
            wrist_uv = np.array([wr["u"], wr["v"]])
            allowance = max(
                3.0,
                min(0.14 * np.linalg.norm(elbow_uv - target), 0.17 * hand["extent"]),
            )
            pts = np.vstack(
                [
                    polygon * 0.25 + np.roll(polygon, 1, axis=0) * 0.75,
                    polygon * 0.5 + np.roll(polygon, 1, axis=0) * 0.5,
                    polygon,
                ]
            )
            near = [target]
            for uv in sorted(pts, key=lambda v: np.linalg.norm(v - wrist_uv)):
                if np.linalg.norm(uv - target) > allowance or any(
                    np.linalg.norm(uv - v) < 2 for v in near
                ):
                    continue
                near.append(uv)
            for uv in near:
                clip = proj_inv @ Vector((2 * uv[0] / w - 1, 1 - 2 * uv[1] / h, 0, 1))
                ray = np.asarray(clip.to_3d() / -clip.z)
                reach = _reach(ray, shoulder, sum(lengths))
                if reach is not None:
                    group.setdefault(tid, []).append(
                        dict(
                            chain,
                            ray=ray,
                            interval=reach,
                            target_uv=uv,
                            target_cost=np.linalg.norm(uv - target) / allowance,
                        )
                    )
    return by_prop, free_arms


def _free_arm_goal(c, depth, w, h, previous=None):
    # This source-visible arm lies beside the object in the image; a misprojected
    # arm in the rig can otherwise occlude its entire visible face.
    low = max(c["interval"][0], depth + 0.07 * min(c["lengths"]))
    hi = c["interval"][1]
    if low >= hi:
        return None
    candidates = []
    for z in np.linspace(low, hi, 65):
        goal = _elbow_candidates(c, z, c["projection"], w, h)
        if goal is None or goal["error"] > c["tolerance"]:
            continue
        score = goal["score"] + 0.22 * abs(z + c["wrist"][2]) / sum(c["lengths"])
        if previous is not None:
            score += 0.5 * abs(z - previous) / sum(c["lengths"])
        candidates.append((score, goal))
    return min(candidates, key=lambda v: v[0])[1] if candidates else None


def fit_handover(scene, camera, people, body_sizes, props, binding_info):
    """Refine arms only in sustained shared, reachable, source-observed prop intervals."""
    import bpy
    from mathutils import Vector

    data = _read(people)
    sizes = _read(body_sizes)
    evidence = _read(props)
    sha = data["source_sha256"]
    if sha != sizes.get("source_sha256") or sha != evidence.get("source_sha256"):
        raise ValueError("Handover inputs do not share a source")
    w, h = data["width"], data["height"]
    report = dict(
        state="monocular_inference",
        geometry_verified_as_contact=False,
        applied=[],
        unknown=[],
    )
    rigs = {
        (
            str(bpy.data.objects[n]["shot_id"]),
            str(bpy.data.objects[n]["track_id"]),
        ): bpy.data.objects[n]
        for n in binding_info["editable_tracks"].values()
    }
    visible = {(str(t["id"]), s["frame"]) for t in evidence["tracks"] for s in t["samples"]}
    original = scene.frame_current
    todo = {}
    active = {}
    surrounding = {}
    try:
        for i, frame in enumerate(sizes["frames"]):
            if not any(o.get("partners") for o in frame["actors"].values()):
                continue
            scene.frame_set(i + 1)
            deps = bpy.context.evaluated_depsgraph_get()
            cam = camera.evaluated_get(deps)
            if cam.data.type != "PERSP":
                report["unknown"].append(dict(frame=i, reason="no_perspective_depth_cue"))
                continue
            proj = cam.calc_matrix_camera(
                deps,
                x=w,
                y=h,
                scale_x=scene.render.pixel_aspect_x,
                scale_y=scene.render.pixel_aspect_y,
            )
            subset = {
                tid: obj for (shot, tid), obj in rigs.items() if shot == str(frame["shot_id"])
            }
            groups, surrounding[i] = _world_chains(
                scene,
                cam,
                proj,
                w,
                h,
                data["frames"][i]["actors"],
                frame["actors"],
                subset,
            )
            if not any(len(v) == 2 for v in groups.values()):
                report["unknown"].append(
                    dict(frame=i, reason="two_elbows_and_wrists_not_directly_observed")
                )
            for prop, choices in groups.items():
                if len(choices) != 2 or (str(prop), i) not in visible:
                    continue
                from itertools import product

                pairs = {}
                failures = []
                for pair in product(*(choices[k] for k in sorted(choices))):
                    answer, details = _choose_common_depth(pair, np.asarray(proj), w, h)
                    if answer is None:
                        failures.append(details)
                        continue
                    loss = sum(
                        a["score"] + 0.16 * c["target_cost"] + 0.1 * (1 - c["weight"])
                        for a, c in zip(answer, pair)
                    )
                    key = (
                        str(frame["shot_id"]),
                        prop,
                        tuple((c["tid"], c["side"]) for c in pair),
                    )
                    if key not in pairs or loss < pairs[key][0]:
                        pairs[key] = (loss, pair, answer, details)
                if not pairs:
                    meta = (
                        min(failures, key=lambda f: sum(f.get("elbow_error", [1e4])))
                        if failures
                        else {"reason": "no_fixed_length_solution"}
                    )
                    report["unknown"].append(dict(frame=i, prop=prop, **meta))
                for key, (loss, chains, goal, meta) in pairs.items():
                    active.setdefault(key, []).append(i)
                    todo[key, i] = (chains, goal, meta, loss)
        # Prefer one continuously supported hand over changing sides for a better
        # single-frame elbow fit. Never apply two rotations to the same arm.
        runs = []
        for key, frames in active.items():
            for seq in np.split(frames, np.where(np.diff(frames) > 1)[0] + 1):
                loss = np.mean([todo[key, int(i)][3] for i in seq])
                runs.append((-len(seq), loss, key, seq))
        occupied = set()
        accepted = set()
        for _, _, key, candidate in sorted(runs, key=lambda x: x[:2]):
            free = [
                int(i)
                for i in candidate
                if all((int(i), tid, side) not in occupied for tid, side in key[2])
                and (int(i), key[1]) not in accepted
            ]
            for seq in np.split(free, np.where(np.diff(free) > 1)[0] + 1):
                if len(seq) < 3:
                    continue
                supplemental = {}
                prev = {}
                for number in seq:
                    i = int(number)
                    chains, _, meta, _ = todo[key, i]
                    for (prop, tid, side), c in surrounding[i].items():
                        arm = (tid, side)
                        if prop != key[1] or arm in key[2]:
                            continue
                        goal = _free_arm_goal(c, meta["depth"], w, h, prev.get(arm))
                        if goal is not None:
                            supplemental[i, arm] = (c, goal)
                            prev[arm] = -goal["wrist"][2]
                for offset, number in enumerate(seq):
                    i = int(number)
                    scene.frame_set(i + 1)
                    # Fade both ends while observations still support both hands.
                    fade = min(3.0, (len(seq) + 1) / 2)
                    z = min(1.0, (offset + 1) / fade, (len(seq) - offset) / fade)
                    strength = z * z * (3 - 2 * z)
                    chains, goals, meta, _ = todo[key, i]
                    primary = chains
                    nearby = []
                    for (f, arm), (c, g) in supplemental.items():
                        if f != i or not any((i + d, arm) in supplemental for d in (-1, 1)):
                            continue
                        nearby.append((c, g))
                    chains = [*chains, *(c for c, g in nearby)]
                    goals = [*goals, *(g for c, g in nearby)]
                    phased = [
                        _fade_goal(c, g, strength, c["projection"], w, h)
                        for c, g in zip(chains, goals)
                    ]
                    if any(g is None for g in phased):
                        continue
                    accepted.add((i, key[1]))
                    occupied.update((i, tid, side) for tid, side in key[2])
                    for c, g in zip(chains, phased):
                        rig = c["rig"]
                        for name, base_q, base_axis, target_axis in (
                            (
                                c["upper"],
                                c["upper_q"],
                                c["elbow"] - c["shoulder"],
                                g["elbow"] - c["shoulder"],
                            ),
                            (
                                c["fore"],
                                c["fore_q"],
                                c["wrist"] - c["elbow"],
                                g["wrist"] - g["elbow"],
                            ),
                        ):
                            bone = rig.pose.bones[name]
                            before = Vector(c["cam_to_rig"] @ base_axis).normalized()
                            desired = Vector(c["cam_to_rig"] @ target_axis).normalized()
                            target = before.rotation_difference(desired) @ base_q
                            parent = bone.parent
                            rest = bone.bone.matrix_local
                            parent_space = (
                                (parent.matrix @ parent.bone.matrix_local.inverted() @ rest)
                                if parent
                                else rest
                            )
                            local = parent_space.to_quaternion().inverted() @ target
                            local.make_compatible(bone.rotation_quaternion)
                            bone.rotation_quaternion = local
                            bone.keyframe_insert(data_path="rotation_quaternion", frame=i + 1)
                            bpy.context.view_layer.update()
                    report["applied"].append(
                        dict(
                            frame=i,
                            strength=round(strength, 4),
                            prop=key[1],
                            chains=[[c["tid"], c["side"]] for c in primary],
                            source_supported_adjacent_arms=[
                                [c["tid"], c["side"]] for c, g in nearby
                            ],
                            target_uv=[c["target_uv"].round(2).tolist() for c in primary],
                            elbow_error_px=[round(g["error"], 3) for g in phased[: len(primary)]],
                            shared_depth_elbow_error_px=[
                                round(g["error"], 3) for g in goals[: len(primary)]
                            ],
                            **meta,
                        )
                    )
        for i, prop in sorted({(f, k[1]) for k, f in todo} - accepted):
            report["unknown"].append(dict(frame=i, prop=prop, reason="isolated_reach"))
    finally:
        scene.frame_set(original)
    return report


def reconcile_handover_props(scene, camera, evidence, prop_binding, handover):
    """Refine an existing visible prop toward a supported handover plane, preserving its projection."""
    import bpy
    from mathutils import Vector

    data = _read(evidence)
    samples = {
        (track["id"], s["frame"]): (track, s) for track in data["tracks"] for s in track["samples"]
    }
    objects = [bpy.data.objects[n] for n in prop_binding["objects"] if n in bpy.data.objects]
    results = []
    original = scene.frame_current
    try:
        for step in handover.get("applied", []):
            i = step["frame"]
            key = (step["prop"], i)
            if key not in samples:
                continue
            track, sample = samples[key]
            name = "Observed " + str(track["id"])
            matches = [
                obj
                for obj in objects
                if (obj.name == name or obj.name.startswith(name + "."))
                and obj.get("source_sha256") == data["source_sha256"]
                and str(obj.get("shot_id")) == str(track["shot_id"])
            ]
            if len(matches) != 1:
                continue
            obj = matches[0]
            scene.frame_set(i + 1)
            deps = bpy.context.evaluated_depsgraph_get()
            cam = camera.evaluated_get(deps)
            if cam.data.type != "PERSP" or obj.parent is not None or obj.hide_render:
                continue
            camera_matrix = cam.matrix_world.normalized()
            inverse = camera_matrix.inverted()
            origin = camera_matrix.translation
            old = obj.location.copy()
            scale = obj.scale.copy()
            depth = -(inverse @ old).z
            if depth <= cam.data.clip_start:
                continue
            polygon = np.asarray(sample["polygon_uv"])
            lo = polygon.min(axis=0)
            hi = polygon.max(axis=0)
            if min(hi - lo) < 2:
                continue
            w, h = data["width"], data["height"]
            matrix = cam.calc_matrix_camera(
                deps,
                x=w,
                y=h,
                scale_x=scene.render.pixel_aspect_x,
                scale_y=scene.render.pixel_aspect_y,
            )
            unproject = matrix.inverted()
            rays = []
            for u in np.linspace(lo[0], hi[0], 11):
                for v in np.linspace(lo[1], hi[1], 9):
                    if _distance_to_polygon([u, v], polygon) > 0.01:
                        continue
                    clip = unproject @ Vector((2 * u / w - 1, 1 - 2 * v / h, 0, 1))
                    local = clip.to_3d() / clip.w
                    rays.append((camera_matrix.to_quaternion() @ local).normalized())

            def visible():
                return sum(scene.ray_cast(deps, origin, ray)[4] == obj for ray in rays)

            baseline = visible()
            if baseline < 3:
                results.append(dict(frame=i, prop=key[0], state="insufficient_visible_surface"))
                continue
            target = (1 - step["strength"]) * depth + step["strength"] * step["depth"]
            # Look for the closest supported plane that still shows most of the
            # previously visible object surface. Source finger occlusion is allowed.
            trial = depth
            count = baseline
            for trial in np.linspace(target, depth, 9):
                ratio = trial / depth
                obj.location = origin + (old - origin) * ratio
                obj.scale = (scale.x * ratio, scale.y * ratio, scale.z)
                bpy.context.view_layer.update()
                count = visible()
                if count >= 0.60 * baseline:
                    break
            for attr in ("location", "scale"):
                obj.keyframe_insert(data_path=attr, frame=i + 1)
            results.append(
                dict(
                    frame=i,
                    prop=key[0],
                    source_visible_rays=len(rays),
                    baseline_visible_rays=baseline,
                    visible_rays=count,
                    previous_depth=round(depth, 6),
                    fitted_depth=round(float(trial), 6),
                    shared_depth=step["depth"],
                    state="source_visibility_constrained_depth",
                )
            )
    finally:
        scene.frame_set(original)
    return results
