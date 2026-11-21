"""Short, bidirectionally verified gaps in shot-local person detections."""

from collections import defaultdict
from copy import deepcopy
import av
import cv2
import numpy as np


def _center(box):
    return (np.asarray(box[:2]) + box[2:]) / 2


def _patch(image, box):
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    left = max(0, int(x1 + 0.25 * bw))
    right = min(w, int(x2 - 0.25 * bw))
    top = max(0, int(y1 + 0.08 * bh))
    bottom = min(h, int(y1 + 0.55 * bh))
    if right - left < 8 or bottom - top < 8:
        return None
    region = image[top:bottom, left:right]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 6], [0, 180, 0, 256, 0, 256])
    return hist.ravel() / max(1, hist.sum())


def _hist_distance(a, b):
    return np.sqrt(max(0.0, 1 - np.sqrt(a * b).sum()))


def _trace(images, box):
    gray = [cv2.cvtColor(v, cv2.COLOR_BGR2GRAY) for v in images]
    h, w = gray[0].shape
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    mask = np.zeros((h, w), np.uint8)
    x0 = max(0, int(x1 + 0.22 * bw))
    x3 = min(w, int(x2 - 0.22 * bw))
    y0 = max(0, int(y1 + 0.06 * bh))
    y3 = min(h, int(y1 + 0.58 * bh))
    mask[y0:y3, x0:x3] = 255
    initial = cv2.goodFeaturesToTrack(gray[0], 90, 0.009, 3, mask=mask)
    if initial is None or len(initial) < 7:
        return None
    pts = initial
    offsets = [np.zeros(2)]
    ratios = [1.0]
    for a, b in zip(gray, gray[1:]):
        q, ok, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None, winSize=(19, 19), maxLevel=3)
        back, bok, _ = cv2.calcOpticalFlowPyrLK(b, a, q, None, winSize=(19, 19), maxLevel=3)
        valid = (
            (ok[:, 0] > 0) & (bok[:, 0] > 0) & (np.linalg.norm((back - pts)[:, 0], axis=1) < 1.25)
        )
        if valid.sum() < 7 or valid.sum() < 0.38 * len(initial):
            return None
        delta = (q - pts)[valid, 0]
        shift = np.median(delta, axis=0)
        if np.median(np.linalg.norm(delta - shift, axis=1)) > max(3.0, 0.05 * bw):
            return None
        offsets.append(offsets[-1] + shift)
        ratios.append(float(valid.sum() / len(initial)))
        pts = q[valid]
    return offsets, ratios


def verify_gap(images, left, right):
    """Images/boxes share pixel coordinates. Return evidence only if both crops persist."""
    b0, b1 = (np.asarray(q, dtype=float) for q in (left, right))
    extent = np.maximum(b0[2:] - b0[:2], 1.0)
    if np.max(np.abs((b1[2:] - b1[:2]) / extent - 1.0)) > 0.32:
        return None
    if np.max(np.abs((_center(b1) - _center(b0)) / extent)) > 0.39:
        return None
    h0, h1 = _patch(images[0], b0), _patch(images[-1], b1)
    if h0 is None or h1 is None or _hist_distance(h0, h1) > 0.48:
        return None
    forward = _trace(images, b0)
    reverse = _trace(list(reversed(images)), b1)
    if not forward or not reverse:
        return None
    result = []
    for i in range(1, len(images) - 1):
        alpha = i / (len(images) - 1)
        a = b0 + np.tile(forward[0][i], 2)
        b = b1 + np.tile(reverse[0][-1 - i], 2)
        disagreement = np.max(np.abs((_center(a) - _center(b)) / extent))
        if disagreement > 0.24:
            return None
        box = a * (1 - alpha) + b * alpha
        hi = _patch(images[i], box)
        if hi is None:
            return None
        appearance = float(min(_hist_distance(h0, hi), _hist_distance(h1, hi)))
        if appearance > 0.51:
            return None
        result.append(
            (
                box,
                {
                    "flow_agreement": float(1 - disagreement),
                    "passing_tracks_fraction": min(forward[1][i], reverse[1][-1 - i]),
                    "appearance_distance": appearance,
                },
            )
        )
    return result


def fill(video, rows, width):
    tracks = defaultdict(list)
    for r in rows:
        for a in r["actors"]:
            if a["bbox_kind"] == "person_detection" and a.get("body_id") is not None:
                tracks[(r["shot_id"], a["track_id"], a["body_id"])].append((r["source_frame"], a))
    gaps = []
    for seq in tracks.values():
        for (lo, a), (hi, b) in zip(seq, seq[1:]):
            if not 2 <= hi - lo <= 5:
                continue
            if any(
                any(t["track_id"] == a["track_id"] for t in rows[f]["actors"])
                for f in range(lo + 1, hi)
            ):
                continue
            gaps.append((lo, a, hi, b))
    if not gaps:
        return
    scale = min(1.0, 640 / width)
    needed = {f for lo, _, hi, _ in gaps for f in range(lo, hi + 1)}
    images = {}
    with av.open(str(video)) as c:
        for i, f in enumerate(c.decode(video=0)):
            if i in needed:
                images[i] = cv2.resize(
                    f.to_ndarray(format="bgr24"),
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )
    for lo, a, hi, b in gaps:
        evidence = verify_gap(
            [images[i] for i in range(lo, hi + 1)],
            np.array(a["bbox"]) * scale,
            np.array(b["bbox"]) * scale,
        )
        if evidence is None:
            continue
        for off, (box, proof) in enumerate(evidence, 1):
            alpha = off / (hi - lo)
            src = a if alpha < 0.5 else b
            obj = deepcopy(src)
            obj["bbox"] = (box / scale).tolist()
            obj["bbox_kind"] = "bounded_person_flow"
            obj["detector_score"] = None
            obj["temporal_support"] = {
                "source_frames": [lo, hi],
                "state": "inferred_between_detections",
                **proof,
            }
            # Endpoint coordinates may guide the fixed-length solve but are never
            # recorded as same-frame network detections.
            for name, j in obj["joints2d"].items():
                pa, pb = a["joints2d"][name], b["joints2d"][name]
                if all(p["visibility"] == "model_estimate" for p in (pa, pb)):
                    j["u"] = (1 - alpha) * pa["u"] + alpha * pb["u"]
                    j["v"] = (1 - alpha) * pa["v"] + alpha * pb["v"]
                    j["visibility"] = "inferred"
                else:
                    j["visibility"] = "unknown"
                j["score"] = 0.0
                j["source"] = "bounded_person_flow"
            # Keep torso/head fitting as an inferred proposal. These private
            # anchors are carried explicitly rather than relabelled as observed.
            obj["interpolation_endpoints"] = (a, b, alpha)
            for field in (
                "face",
                "head_detection",
                "person_region",
                "region_track",
                "tracked_face_landmarks",
            ):
                obj.pop(field, None)
            rows[lo + off]["actors"].append(obj)
