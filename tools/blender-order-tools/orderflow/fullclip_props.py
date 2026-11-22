"""Observe visible rigid-looking patches beside wrists and bind editable shot-local silhouettes.

The single-camera observation measures the visible outline only. Out-of-plane tilt,
thickness and the hidden outline remain unknown. Blender uses the contacting wrist
as a depth estimate and only displays frames with source-supported contours.
"""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile


def read_data(value):
    return value if isinstance(value, dict) else json.loads(Path(value).read_bytes())


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def observed(point):
    return (
        point
        and point.get("score", 0) >= 0.45
        and point.get("visibility") in ("model_estimate", "observed", "visible")
        and point.get("u") is not None
        and point.get("v") is not None
    )


def contacts(actors):
    import numpy as np

    hands = []
    for actor in actors:
        joints = actor["joints2d"]
        for side in ("left", "right"):
            elbow, wrist = (joints.get(side + "_" + k) for k in ("elbow", "wrist"))
            if not (observed(elbow) and observed(wrist)):
                continue
            to_xy = lambda p: np.array([p["u"], p["v"]], dtype=float)
            tip = to_xy(wrist)
            axis = tip - to_xy(elbow)
            length = np.linalg.norm(axis)
            if length < 7:
                continue
            # A foreshortened forearm may project to a small fraction of its true length.
            x1, y1, x2, y2 = actor["bbox"]
            radius = max(length, min(0.22 * (x2 - x1), 0.15 * (y2 - y1)), 11.0)
            hands.append(
                dict(
                    track_id=str(actor["track_id"]),
                    side=side,
                    wrist=tip,
                    palm=tip + axis * 0.16,
                    axis=axis / length,
                    radius=float(radius),
                )
            )
    return hands


def candidate_patches(image, hand, faces):
    import cv2
    import numpy as np

    h, w = image.shape[:2]
    palm, radius = hand["palm"], hand["radius"]
    x0, y0 = np.maximum(np.floor(palm - 1.45 * radius), 0).astype(int)
    x1, y1 = np.minimum(np.ceil(palm + 1.45 * radius) + 1, [w, h]).astype(int)
    roi = image[y0:y1, x0:x1]
    if min(roi.shape[:2]) < 8:
        return []
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    bright = (val >= max(143, np.percentile(val, 93))) & (sat < 113)
    vivid = (sat > max(100, np.percentile(sat, 76))) & (val >= 80)
    # Warm skin-like hues are ambiguous without an independent object cue.
    possible_skin = ((hue < 23) | (hue > 171)) & (sat >= 37)
    mask = ((bright | vivid) & ~possible_skin).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    for x, y, xx, yy in faces:
        cv2.rectangle(mask, (int(x - x0), int(y - y0)), (int(xx - x0), int(yy - y0)), 0, -1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for border in contours:
        area = cv2.contourArea(border)
        if not max(12, 0.026 * radius**2) < area < 1.65 * radius**2:
            continue
        bx, by, bw, bh = cv2.boundingRect(border)
        if bx <= 0 or by <= 0 or bx + bw >= roi.shape[1] or by + bh >= roi.shape[0]:
            continue
        center, dims, angle = cv2.minAreaRect(border)
        long, short = max(dims), min(dims)
        if short < max(3, 0.10 * radius) or long > 2.35 * radius or long < 0.30 * radius:
            continue
        if short / long < 0.17 or area / (long * short) < 0.45:
            continue
        xy = np.array(center) + [x0, y0]
        relative = xy - hand["wrist"]
        if np.dot(relative, hand["axis"]) < -0.19 * radius:
            continue
        dist = -cv2.pointPolygonTest(border, tuple((palm - [x0, y0]).tolist()), True)
        if dist > 0.30 * radius or np.linalg.norm(xy - palm) > 1.05 * radius:
            continue
        inside = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(inside, [border], -1, 1, -1)
        filled = inside.astype(bool)
        if np.mean(mask[filled] > 0) < 0.82:
            continue
        surround = cv2.dilate(inside, np.ones((7, 7), np.uint8)).astype(bool) & ~filled
        color = np.median(lab[filled], axis=0)
        contrast = np.linalg.norm(color - np.median(lab[surround], axis=0))
        if contrast < 29:
            continue
        # Reject disconnected seat/backdrop patches merely close to a wrist. A visible
        # object should also have contact on the side reached by the hand, or overlap it.
        if np.linalg.norm(relative) < 0.14 * radius:
            continue
        angle = (angle + (90 if dims[0] < dims[1] else 0)) % 180
        poly = cv2.approxPolyDP(border, max(1.0, 0.012 * long), True).reshape(-1, 2) + [x0, y0]
        if len(poly) < 3:
            continue
        found.append(
            dict(
                center=xy,
                axes=np.array([long, short]),
                angle=float(angle),
                poly=poly,
                rgb=np.median(roi[filled], axis=0)[::-1],
                lab=color,
                contrast=float(contrast),
                contacts=[hand],
                score=float(contrast / (1 + max(0.0, dist) / radius)),
            )
        )
    return found


def same_patch(a, b):
    import cv2
    import numpy as np

    if np.linalg.norm(a["center"] - b["center"]) > 0.39 * max(a["axes"][0], b["axes"][0]):
        return False
    pa, pb = (cv2.convexHull(s["poly"].astype(np.float32)) for s in (a, b))
    common, _ = cv2.intersectConvexConvex(pa, pb)
    return common >= 0.63 * min(cv2.contourArea(pa), cv2.contourArea(pb))


def source_background(prev, cur, old_actors, actors, scale):
    """Track the surrounding set only, rejecting detections inside people at either end."""
    import cv2
    import numpy as np

    def background_mask(im, people):
        result = np.full(im.shape, 255, np.uint8)
        for actor in people:
            x, y, xx, yy = np.rint(np.asarray(actor["bbox"]) * scale).astype(int)
            cv2.rectangle(result, (x - 2, y - 2), (xx + 2, yy + 2), 0, -1)
        return result

    mask = background_mask(prev, old_actors)
    p = cv2.goodFeaturesToTrack(prev, 220, 0.014, 5, mask=mask)
    if p is None or len(p) < 5:
        # Textureless tripod shot is measurable when the actual image is invariant.
        valid = (mask > 0) & (background_mask(cur, actors) > 0)
        if valid.mean() > 0.15 and np.mean(cv2.absdiff(cur, prev)[valid]) < 1.2:
            return np.eye(2, 3)
        return None
    q, state, err = cv2.calcOpticalFlowPyrLK(prev, cur, p, None, winSize=(17, 17), maxLevel=2)
    if q is None:
        return None
    p, q = p[:, 0], q[:, 0]
    ix = np.rint(q[:, 0]).astype(int).clip(0, cur.shape[1] - 1)
    iy = np.rint(q[:, 1]).astype(int).clip(0, cur.shape[0] - 1)
    keep = (state[:, 0] > 0) & (err[:, 0] < 23) & (background_mask(cur, actors)[iy, ix] > 0)
    if keep.sum() < 5:
        return None
    aff, inliers = cv2.estimateAffinePartial2D(
        p[keep] / scale, q[keep] / scale, method=cv2.RANSAC, ransacReprojThreshold=2.8 / scale
    )
    return aff if aff is not None and inliers.mean() > 0.55 else None


def hand_map(patch):
    return {(h["track_id"], h["side"]): h for h in patch["contacts"]}


def associate(active, visible, frame, shot, tracks):
    import numpy as np

    options = []
    for tr in active:
        last = tr[-1]
        if last["shot"] != shot or frame - last["frame"] > 3:
            continue
        a = hand_map(last)
        for k, p in enumerate(visible):
            b = hand_map(p)
            shared = a.keys() & b.keys()
            if not shared:
                continue
            size = max(last["axes"][0], p["axes"][0])
            gap = frame - last["frame"]
            travel = np.linalg.norm(last["center"] - p["center"])
            if travel > size * (0.68 + 0.20 * gap):
                continue
            color = np.linalg.norm(last["lab"] - p["lab"])
            ratio = np.abs(np.log(p["axes"] / last["axes"])).max()
            if color > 48 or ratio > 0.65:
                continue
            options.append((travel / size + color / 110 + ratio * 0.6, id(tr), k, tr))
    assigned, claimed = set(), set()
    for _, identity, k, tr in sorted(options, key=lambda o: o[:3]):
        if identity not in assigned and k not in claimed:
            tr.append(visible[k])
            assigned.add(identity)
            claimed.add(k)
    for k, p in enumerate(visible):
        if k not in claimed:
            tr = [p]
            active.append(tr)
            tracks.append(tr)
    active[:] = [t for t in active if t[-1]["shot"] == shot and frame - t[-1]["frame"] < 3]


def motion_evidence(track, transforms):
    import numpy as np

    approved = set()
    for i, first in enumerate(track):
        for j in range(i + 1, len(track)):
            last = track[j]
            span = last["frame"] - first["frame"]
            if span > 12:
                break
            if span < 4 or j - i + 1 < span * 0.72:
                continue
            shared = hand_map(first).keys() & hand_map(last).keys()
            if not shared:
                continue
            predicted = first["center"]
            for f in range(first["frame"] + 1, last["frame"] + 1):
                if transforms[f] is None:
                    break
                predicted = transforms[f] @ [*predicted, 1.0]
            else:
                change = last["center"] - first["center"]
                bg = predicted - first["center"]
                relative = np.linalg.norm(change - bg)
                size = np.median([first["axes"][0], last["axes"][0]])
                err = min(
                    np.linalg.norm(
                        change - (hand_map(last)[k]["wrist"] - hand_map(first)[k]["wrist"])
                    )
                    for k in shared
                )
                if (
                    relative > max(3.2, 0.13 * size)
                    and err < 0.60 * relative
                    and err < max(4, 0.27 * size)
                ):
                    approved.update(range(i, j + 1))
    return [track[i] for i in sorted(approved)] if len(approved) >= 5 else []


def export_sample(patch):
    return dict(
        frame=patch["frame"],
        center_uv=patch["center"].round(2).tolist(),
        axes_px=patch["axes"].round(2).tolist(),
        angle_deg=round(patch["angle"], 2),
        polygon_uv=patch["poly"].astype(int).tolist(),
        rgb=patch["rgb"].astype(int).tolist(),
        contacts=[dict(track_id=h["track_id"], side=h["side"]) for h in patch["contacts"]],
    )


def join_tracklets(segments, fps, cuts):
    """Associate uniquely compatible same-hand intervals. Missing frames stay hidden."""
    import numpy as np

    groups = []
    for rows in sorted(segments, key=lambda seg: seg[0]["frame"]):
        first = rows[0]
        matches = []
        for index, group in enumerate(groups):
            last = group[-1]
            gap = first["frame"] - last["frame"]
            if last["shot"] != first["shot"] or not 0 < gap <= fps * 0.55:
                continue
            if any(last["frame"] < cut <= first["frame"] for cut in cuts):
                continue
            shared = hand_map(first).keys() & hand_map(last).keys()
            size = max(first["axes"][0], last["axes"][0])
            if not shared or np.linalg.norm(first["lab"] - last["lab"]) > 30:
                continue
            if np.max(np.abs(np.log(first["axes"] / last["axes"]))) > 0.8:
                continue
            delta = first["center"] - last["center"]
            error = min(
                np.linalg.norm(delta - (hand_map(first)[k]["wrist"] - hand_map(last)[k]["wrist"]))
                for k in shared
            )
            if error < max(4, size * 0.35):
                matches.append(index)
        if len(matches) == 1:
            groups[matches[0]].extend(rows)
        else:
            groups.append(list(rows))
    return groups


def follow_confirmed(source, info, tracks, cuts, fps):
    """Require good local pixel correspondences even between directly confirmed intervals."""
    if __package__:
        from .fullclip_props_flow import recover
    else:
        from fullclip_props_flow import recover
    import av
    import numpy as np

    if not tracks:
        return
    fixed = [{r["frame"]: r for r in t["samples"]} for t in tracks]
    state = {}
    limit = max(2, int(fps * 0.7))
    with av.open(str(source)) as source_video:
        stream = source_video.streams.video[0]
        stream.thread_count = 2
        previous = None
        for i, frame in enumerate(source_video.decode(stream)):
            relevant = [k for k, rows in enumerate(fixed) if i in rows or k in state]
            if not relevant:
                previous = None
                continue
            image = frame.to_ndarray(format="bgr24")
            actors = info["frames"][i]["actors"]
            shot = info["frames"][i]["shot_id"]
            hands = contacts(actors)
            faces = [a["face"]["bbox"] for a in actors if a.get("face")]
            # A source-confirmed contour takes precedence over any proposed continuation.
            occupied = [rows[i] for rows in fixed if i in rows]
            for k in relevant:
                if i in fixed[k]:
                    state[k] = (fixed[k][i], fixed[k][i])
                    continue
                last, seed = state.pop(k)
                if (
                    i in cuts
                    or previous is None
                    or tracks[k]["shot_id"] != shot
                    or i - seed["frame"] > limit
                ):
                    continue
                sample = recover(previous, image, last, seed, hands, faces)
                if sample is None:
                    continue
                if any(
                    np.linalg.norm(np.subtract(sample["center_uv"], other["center_uv"]))
                    < 0.42 * min(sample["axes_px"][0], other["axes_px"][0])
                    for other in occupied
                ):
                    continue
                sample["frame"] = i
                occupied.append(sample)
                tracks[k]["samples"].append(sample)
                state[k] = (sample, seed)
            previous = image
    for tr in tracks:
        tr["samples"].sort(key=lambda s: s["frame"])


def join_adjacent(tracks, cuts):
    """Link a continuation to a new anchor when the same visible patch persists."""
    import cv2
    import numpy as np

    result = []
    for tr in sorted(tracks, key=lambda t: t["samples"][0]["frame"]):
        first = tr["samples"][0]
        candidates = []
        for prior in result:
            last = prior["samples"][-1]
            if (
                prior["shot_id"] != tr["shot_id"]
                or first["frame"] in cuts
                or first["frame"] != last["frame"] + 1
            ):
                continue
            before = {(r["track_id"], r["side"]) for r in last["contacts"]}
            after = {(r["track_id"], r["side"]) for r in first["contacts"]}
            if not before.intersection(after):
                continue
            if np.linalg.norm(np.subtract(first["rgb"], last["rgb"])) > 65:
                continue
            a, b = (cv2.convexHull(np.float32(s["polygon_uv"])) for s in (first, last))
            overlap, _ = cv2.intersectConvexConvex(a, b)
            if overlap > 0.45 * min(cv2.contourArea(a), cv2.contourArea(b)):
                candidates.append(prior)
        if len(candidates) == 1:
            candidates[0]["samples"].extend(tr["samples"])
        else:
            result.append(tr)
    return result


def measure_props(source, people, output, *, timeline=None):
    import cv2
    from fractions import Fraction
    import av

    info = read_data(people)
    if timeline is None and not isinstance(people, dict):
        sibling = Path(people).with_name("timeline.json")
        timeline = sibling if sibling.is_file() else None
    clock = read_data(timeline) if timeline is not None else {}
    cuts = set(clock.get("cuts", []))
    sha = digest(source)
    if info.get("source_sha256", sha) != sha:
        raise ValueError("Prop source differs from people observations")
    video = av.open(str(source))
    stream = video.streams.video[0]
    stream.thread_count = 2
    decoded = iter(video.decode(stream))
    w, h = stream.width, stream.height
    if (w, h) != (info["width"], info["height"]):
        video.close()
        raise ValueError("Source video and people dimensions disagree")
    fps = float(stream.average_rate or 24)
    cv2.setNumThreads(1)
    scale = min(1.0, 480.0 / w)
    tracks, active, transforms = [], [], []
    prev = prev_shot = None
    old_actors = []
    stats = Counter()
    try:
        for i, row in enumerate(info["frames"]):
            frame = next(decoded, None)
            if frame is None or row.get("source_frame", i) != i:
                raise ValueError(f"Missing matching source frame {i}")
            if (
                "pts" in row
                and Fraction(row["pts"]) * Fraction(row["time_base"])
                != frame.pts * frame.time_base
            ):
                raise ValueError(f"People/source PTS mismatch at frame {i}")
            if "pts" in clock and Fraction(clock["pts"][i]) != frame.pts * frame.time_base:
                raise ValueError(f"Timeline/source PTS mismatch at frame {i}")
            im = frame.to_ndarray(format="bgr24")
            shot = row["shot_id"]
            actors = row["actors"]
            gray = cv2.resize(
                cv2.cvtColor(im, cv2.COLOR_BGR2GRAY),
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
            if prev is None or i in cuts or shot != prev_shot:
                active.clear()
                transforms.append(None)
            else:
                transforms.append(source_background(prev, gray, old_actors, actors, scale))
            prev, old_actors, prev_shot = gray, actors, shot
            faces = [a["face"]["bbox"] for a in actors if a.get("face")]
            palms = contacts(actors)
            stats["frames_with_observed_elbow_wrist"] += bool(palms)
            proposals = [p for hand in palms for p in candidate_patches(im, hand, faces)]
            visible = []
            for p in sorted(proposals, key=lambda p: -p["score"]):
                overlap = next((v for v in visible if same_patch(p, v)), None)
                if overlap is None:
                    p.update(frame=i, shot=shot)
                    visible.append(p)
                elif (p["contacts"][0]["track_id"], p["contacts"][0]["side"]) not in hand_map(
                    overlap
                ):
                    overlap["contacts"].extend(p["contacts"])
            stats["contours_before_motion_gate"] += len(visible)
            associate(active, visible, i, shot, tracks)
        if next(decoded, None) is not None:
            raise ValueError("People observations terminate before video")
    finally:
        video.close()
    confirmed = [rows for tr in tracks if (rows := motion_evidence(tr, transforms))]
    accepted = [
        dict(
            id=f"prop_{k + 1:03}",
            shot_id=rows[0]["shot"],
            samples=[export_sample(p) for p in rows],
        )
        for k, rows in enumerate(join_tracklets(confirmed, fps, cuts))
    ]
    follow_confirmed(source, info, accepted, cuts, fps)
    accepted = join_adjacent(accepted, cuts)
    if __package__:
        from .fullclip_handover import fit_handover_solids
    else:
        from fullclip_handover import fit_handover_solids
    fit_handover_solids(source, accepted, info)
    result = dict(
        schema="orderflow.visible_props.v1",
        source_sha256=sha,
        width=w,
        height=h,
        frames=len(info["frames"]),
        tracks=accepted,
        stats=dict(stats),
        method="wrist_contact_color_contour_and_background_relative_motion",
        contact_state="2d_wrist_proximity",
        depth_state="estimated_from_bound_wrist",
        out_of_plane_orientation="unknown",
        hidden_surface="unknown",
    )
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf8", dir=dest.parent, delete=False
        ) as fh:
            temporary = fh.name
            json.dump(result, fh, allow_nan=False, separators=(",", ":"))
        os.replace(temporary, dest)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return result


def bind_props(scene, camera, evidence, binding_info):
    if __package__:
        from .fullclip_props_bind import bind
    else:
        from fullclip_props_bind import bind
    return bind(scene, camera, read_data(evidence), binding_info)
