"""Bidirectional image tracking between independent close-head detections."""

import math
import cv2
import numpy as np
import av

if __package__:
    from . import fullclip_people_chroma as chroma
    from .fullclip_people_faces import overlap
    from .fullclip_people_tracks import head_region, empty_joints, midpoint
else:
    import fullclip_people_chroma as chroma
    from fullclip_people_faces import overlap
    from fullclip_people_tracks import head_region, empty_joints, midpoint


def is_large(a, h):
    region = a.get("face") or a.get("head_detection")
    if region:
        box = region["bbox"]
        return max(box[2] - box[0], box[3] - box[1]) >= 0.19 * h and region["score"] >= 0.36
    return False


def march(images, source, end, box, model):
    params = cv2.TrackerVit_Params()
    params.net = str(model)
    params.tracking_score_threshold = 0.22
    tracker = cv2.TrackerVit_create(params)
    h, w = images[source].shape[:2]
    b = np.clip(box, [0, 0, 0, 0], [w - 1, h - 1, w - 1, h - 1])
    x, y, x2, y2 = map(int, np.rint(b))
    if min(x2 - x, y2 - y) < 8:
        return {}
    tracker.init(images[source], (x, y, x2 - x, y2 - y))
    step = 1 if end > source else -1
    out = {}
    for i in range(source + step, end + step, step):
        found, (x, y, bw, bh) = tracker.update(images[i])
        s = tracker.getTrackingScore()
        if not found or not np.isfinite(s) or s < 0.28 or min(bw, bh) < 5:
            break
        out[i] = (np.array([x, y, x + bw, y + bh], dtype=float), float(s))
    return out


def fill(video, rows, model, width, height):
    if not model:
        return
    cv2.setNumThreads(2)
    scale = min(1.0, 640 / width)
    seeds = {}
    for r in rows:
        valid = [a for a in r["actors"] if is_large(a, height)]
        if len(valid) == 1:
            seeds[r["source_frame"]] = valid[0]
    if not seeds:
        return
    # Process one shot at a time to bound working memory on long files.
    with av.open(str(video)) as container:
        images = []
        base = 0
        for i, fr in enumerate(container.decode(video=0)):
            if images and rows[i]["shot_id"] != rows[i - 1]["shot_id"]:
                fill_shot(rows, images, base, seeds, model, scale, height)
                images = []
                base = i
            bgr = fr.to_ndarray(format="bgr24")
            images.append(cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
        if images:
            fill_shot(rows, images, base, seeds, model, scale, height)


def fill_shot(rows, images, base, seeds, model, scale, height):
    n = len(images)
    at = [f - base for f in sorted(seeds) if base <= f < base + n]
    # Never infer a foreground character solely from an unknown frame.
    if len(at) < 2:
        return
    aliases = {}
    lut = chroma.learn(images, [(i, seeds[base + i]) for i in at[:: max(1, len(at) // 18)]], scale)
    for left, right in zip([-1] + at, at + [n]):
        if right - left <= 1:
            continue
        if right - left > 95:
            continue
        l = seeds.get(base + left) if left >= 0 else None
        r = seeds.get(base + right) if right < n else None
        if not l and not r:
            continue
        # Avoid edge extrapolation across most of a shot with no observations.
        ab = (
            march(
                images, left, min(right, n - 1, left + 60), np.array(head_region(l)) * scale, model
            )
            if l
            else {}
        )
        ba = (
            march(images, right, max(left, 0, right - 60), np.array(head_region(r)) * scale, model)
            if r
            else {}
        )
        if l and r and (left in ba or right in ab):
            end_l = overlap(ba[left][0] / scale, head_region(l)) if left in ba else 0
            end_r = overlap(ab[right][0] / scale, head_region(r)) if right in ab else 0
            if max(end_l, end_r) > 0.38 and l["track_id"] != r["track_id"]:
                # Merge only non-overlapping anonymous tracks within this shot.
                id_l, id_r = l["track_id"], r["track_id"]
                if not any(
                    {id_l, id_r} <= {a["track_id"] for a in rr["actors"]}
                    for rr in rows[base : base + n]
                ):
                    aliases[id_r] = id_l
        for t in range(left + 1, right):
            options = []
            for obs, origin, samples in ((l, left, ab), (r, right, ba)):
                if obs is None or t not in samples:
                    continue
                b, s = samples[t]
                if s < 0.39:
                    continue
                if abs(t - origin) > 16 and (s < 0.53 or not (l and r)):
                    continue
                strength = s * math.exp(-abs(t - origin) / 36)
                options.append((strength, origin, b / scale, s, obs))
            orig = l if r is None or (l and t - left <= right - t) else r
            origin = left if orig is l else right
            keybox = np.array(head_region(orig), dtype=float)
            if l and r:
                alpha = (t - left) / (right - left)
                keybox = np.array(head_region(l)) * (1 - alpha) + np.array(head_region(r)) * alpha
            matched = chroma.localization(images, t, lut, keybox, scale)
            if matched is not None:
                estimated, evidence = matched
                # Reject tracking on a differently coloured background object.
                kept = []
                for option in options:
                    if overlap(option[2], estimated) >= 0.19:
                        kept.append(option)
                options = kept
                if evidence > 0.085 and ((l and r) or abs(t - origin) <= 18):
                    options.append((0.75 + min(0.20, evidence), origin, estimated, evidence, orig))
            if not options:
                continue
            _, origin, box, confidence, obs = max(options, key=lambda x: x[0])
            if (
                len(options) == 2
                and overlap(options[0][2], options[1][2]) < 0.1
                and min(t - left, right - t) > 9
            ):
                continue
            row = rows[base + t]
            # A small, weak fallback skeleton can refer to a fragment of the
            # same close body. Avoid simultaneous full-body and head-only clones.
            candidates = [
                a
                for a in row["actors"]
                if a["track_id"] == obs["track_id"] or overlap(head_region(a), box) > 0.1
            ]
            if not candidates:
                candidates = [
                    a
                    for a in row["actors"]
                    if a["bbox_kind"] == "unboxed_pose_extent"
                    and np.linalg.norm(midpoint(head_region(a)) - midpoint(box))
                    < 0.57 * max(box[2] - box[0], box[3] - box[1])
                ]
            target = (
                min(
                    candidates,
                    key=lambda a: np.linalg.norm(midpoint(head_region(a)) - midpoint(box)),
                )
                if candidates
                else None
            )
            if target is None:
                target = {
                    "track_id": obs["track_id"],
                    "body_id": None,
                    "bbox": box.tolist(),
                    "bbox_kind": "vit_tracked_head",
                    "detector_score": None,
                    "joints2d": empty_joints(),
                }
                row["actors"].append(target)
            old_box = head_region(obs)
            track = {
                "source_frame": origin + base,
                "steps": abs(t - origin),
                "response": confidence,
                "method": "source_chroma"
                if matched is not None and np.array_equal(box, matched[0])
                else "vittrack",
            }
            target["region_track"] = track
            target["head_detection"] = {
                "bbox": box.tolist(),
                "score": 0.0,
                "kind": track["method"],
            }
            if "face" in obs:
                mapped = (np.array(obs["face"]["landmarks"]) - old_box[:2]) / np.maximum(
                    1, np.subtract(old_box[2:], old_box[:2])
                )
                target["tracked_face_landmarks"] = (
                    mapped * np.subtract(box[2:], box[:2]) + box[:2]
                ).tolist()
            if "person_region" in obs:
                target["tracked_neck_axis"] = (
                    (midpoint(old_box) - midpoint(obs["person_region"]["bbox"]))
                    / max(old_box[2] - old_box[0], old_box[3] - old_box[1])
                ).tolist()
    for row in rows[base : base + n]:
        for actor in row["actors"]:
            seen = set()
            while actor["track_id"] in aliases and actor["track_id"] not in seen:
                seen.add(actor["track_id"])
                actor["track_id"] = aliases[actor["track_id"]]
