"""Infer cropped shoulder support and border exits from the same shot's image motion."""

from collections import defaultdict
import cv2
import numpy as np
import av

if __package__:
    from .fullclip_people_tracks import midpoint
    from .fullclip_people_solve import unit
else:
    from fullclip_people_tracks import midpoint
    from fullclip_people_solve import unit


def head_box(a):
    region = a.get("face") or a.get("head_detection")
    return region["bbox"] if region else None


def analyze(video, rows, width, height):
    shots = defaultdict(list)
    for row in rows:
        shots[row["shot_id"]].append(row)
    for part in shots.values():
        if np.mean([len(r["actors"]) <= 1 for r in part]) < 0.85:
            continue
        confirmed = []
        large_count = 0
        observed_face = 0
        body_samples = []
        for rr in part:
            for a in rr["actors"]:
                hb = head_box(a)
                if not hb or max(np.subtract(hb[2:], hb[:2])) < 0.22 * height:
                    continue
                large_count += 1
                hcenter = midpoint(hb)
                dim = max(np.subtract(hb[2:], hb[:2]))
                if a.get("face", {}).get("score", 0) > 0.76:
                    observed_face += 1
                    confirmed.append((rr["source_frame"], a))
                p = a.get("person_region")
                if p:
                    shift = hcenter - midpoint(p["bbox"])
                    if np.linalg.norm(shift) > 0.15 * dim:
                        body_samples.append((rr["source_frame"], shift / dim, p["score"]))
                if a.get("head_detection", {}).get("score", 0) > 0.42:
                    js = a["joints2d"]
                    pts = [
                        js[s + "_shoulder"]
                        for s in ("left", "right")
                        if js[s + "_shoulder"]["visibility"] == "model_estimate"
                    ]
                    if len(pts) == 2:
                        sh = np.mean([[pt["u"], pt["v"]] for pt in pts], axis=0)
                        shift = hcenter - sh
                        if 0.22 * dim < np.linalg.norm(shift) < 1.4 * dim:
                            body_samples.append(
                                (rr["source_frame"], shift / dim, min(pt["score"] for pt in pts))
                            )
        if (
            large_count >= 0.55 * len(part)
            and observed_face < 0.06 * len(part)
            and len(body_samples) >= 3
        ):
            for rr in part:
                i = rr["source_frame"]
                near = sorted(body_samples, key=lambda q: abs(q[0] - i))[:9]
                vec = np.median([q[1] for q in near], axis=0)
                if np.linalg.norm(vec) < 0.15:
                    continue
                for a in rr["actors"]:
                    for name, joint in a["joints2d"].items():
                        if name in ("nose", "left_eye", "right_eye", "left_ear", "right_ear") or (
                            a["bbox_kind"] == "unboxed_pose_extent" and joint["score"] < 0.62
                        ):
                            joint["visibility"] = "unknown"
                            joint["rejection_reason"] = (
                                "large head/shoulder region; no corroborating face or limb"
                            )
                    a["shoulder_support"] = {
                        "up_uv": unit(vec, [0, -1]).round(5).tolist(),
                        "samples": len(near),
                        "source_frames": [q[0] for q in near],
                        "face_detections": observed_face,
                        "state": "back_or_down_inferred",
                        "evidence": "large head detections without face landmarks; anatomical shoulder/person regions lie on one side",
                    }
        if len(confirmed) >= 6:
            border_tail(video, part, confirmed, width, height)


def border_tail(video, part, confirmed, width, height):
    end = part[-1]["source_frame"]
    last, act = confirmed[-1]
    if end - last < 4 or end - last > 40:
        return
    # Estimate a recent motion direction from measured eye lines. A stationary
    # close-up must never start travelling just because a landmark disappears.
    supported = [(i, a) for i, a in confirmed if last - 15 <= i <= last]
    if len(supported) < 4:
        return
    times = [i for i, _ in supported]
    centers = np.array([np.mean(a["face"]["landmarks"][:2], axis=0) for _, a in supported])
    velocities = [
        (centers[b] - centers[a]) / (times[b] - times[a])
        for a in range(len(times))
        for b in range(a + 1, len(times))
        if times[b] - times[a] >= 3
    ]
    if not velocities:
        return
    vel = np.median(velocities, axis=0)
    headbox = np.array(act["face"]["bbox"])
    size = max(headbox[2:] - headbox[:2])
    if np.linalg.norm(vel) < 0.009 * size:
        return
    origin = centers[-1].copy()
    limits = np.array([width, height])
    away = origin + vel * min(24, end - last)
    edge = np.maximum(-away, away - limits)
    axis = int(np.argmax(edge / limits))
    if edge[axis] < -0.15 * size:
        return
    sign = np.sign(vel[axis])
    scale = min(1.0, 640 / width)
    gray = []
    with av.open(str(video)) as c:
        for i, f in enumerate(c.decode(video=0)):
            if last <= i <= end:
                g = f.to_ndarray(format="gray")
                gray.append(cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
            if i >= end:
                break
    yy, xx = np.mgrid[: gray[0].shape[0], : gray[0].shape[1]]
    center = origin * scale
    for k in range(1, len(gray)):
        prev, nxt = gray[k - 1], gray[k]
        difference = cv2.absdiff(prev, nxt)
        d = ((xx - center[0]) / (size * scale * 0.54)) ** 2 + (
            (yy - center[1]) / (size * scale * 0.66)
        ) ** 2
        spatial = d < 1
        varying = difference[spatial & (difference > 0)]
        signal = np.array([], dtype=bool)
        if len(varying):
            threshold = max(2.0, np.quantile(varying, 0.58))
            mask = np.uint8(spatial & (difference >= threshold)) * 255
            pts = cv2.goodFeaturesToTrack(prev, 450, 0.001, 3, mask=mask)
            if pts is not None and len(pts) >= 6:
                ahead, ok, _ = cv2.calcOpticalFlowPyrLK(
                    prev, nxt, pts, None, winSize=(21, 21), maxLevel=4
                )
                back, bok, _ = cv2.calcOpticalFlowPyrLK(
                    nxt, prev, ahead, None, winSize=(21, 21), maxLevel=4
                )
                delta = (ahead - pts)[:, 0]
                signal = (
                    (ok[:, 0] > 0)
                    & (bok[:, 0] > 0)
                    & (np.linalg.norm((back - pts)[:, 0], axis=1) < 1.5)
                )
                signal &= delta[:, axis] * sign > max(0.55, abs(vel[axis]) * scale * 0.09)
                signal &= delta[:, axis] * sign < max(
                    size * scale * 0.13, abs(vel[axis]) * scale * 5
                )
        if np.count_nonzero(signal) < 6:
            if not np.any(spatial) or np.quantile(difference[spatial], 0.90) > 2.5:
                break
            shift = np.zeros(2)
        else:
            shift = np.median(delta[signal], axis=0)
        center += shift
        i = last + k
        for candidate in part[i - part[0]["source_frame"]]["actors"]:
            hbox = head_box(candidate)
            if hbox and candidate.get("face", {}).get("score", 0) > 0.78:
                continue
            for name, joint in candidate["joints2d"].items():
                if joint["u"] is None:
                    continue
                if (
                    name in ("nose", "left_eye", "right_eye", "left_ear", "right_ear")
                    and np.linalg.norm(np.array([joint["u"], joint["v"]]) - center / scale)
                    > 0.34 * size
                ):
                    joint["visibility"] = "unknown"
                    joint["rejection_reason"] = (
                        "disagrees with tracked texture during partial frame exit"
                    )
            candidate["border_motion"] = {
                "head_uv": (center / scale).round(3).tolist(),
                "source_frame": last,
                "flow_samples": int(np.sum(signal)),
                "state": "partial_exit_flow_inferred",
                "last_measured_face_width": float(min(headbox[2:] - headbox[:2])),
            }
