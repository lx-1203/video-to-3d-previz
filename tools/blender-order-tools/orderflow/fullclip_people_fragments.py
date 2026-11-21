"""Associate interrupted, nearly stationary background tracks under camera motion."""

from collections import defaultdict
from copy import deepcopy

import av
import cv2
import numpy as np

if __package__:
    from .fullclip_people_gaps import _patch, _hist_distance
else:
    from fullclip_people_gaps import _patch, _hist_distance


def background_transform(left, right, boxes):
    gray = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    other = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
    mask = np.full(gray.shape, 255, np.uint8)
    for x, y, end_x, end_y in np.asarray(boxes, int).reshape(-1, 4):
        mask[max(0, y) : max(0, end_y), max(0, x) : max(0, end_x)] = 0
    pts = cv2.goodFeaturesToTrack(gray, 450, 0.008, 6, mask=mask)
    if pts is None or len(pts) < 20:
        return None
    target, ok, _ = cv2.calcOpticalFlowPyrLK(gray, other, pts, None)
    if target is None:
        return None
    back, ok_back, _ = cv2.calcOpticalFlowPyrLK(other, gray, target, None)
    if back is None:
        return None
    good = (
        (ok[:, 0] > 0)
        & (ok_back[:, 0] > 0)
        & (np.linalg.norm(back[:, 0] - pts[:, 0], axis=1) < 1.5)
    )
    if good.sum() < 16:
        return None
    H, inliers = cv2.findHomography(pts[good], target[good], cv2.RANSAC, 1.7)
    if H is None or inliers.sum() < max(12, 0.5 * good.sum()):
        return None
    if np.linalg.cond(H) > 1e5 or not np.isfinite(H).all():
        return None
    return H


def warp(box, H):
    return cv2.perspectiveTransform(np.asarray(box, float).reshape(2, 1, 2), H).reshape(4)


def match_fragments(images, matrices, left, right):
    """Require consistent camera-compensated endpoints, clothing and enough interior evidence."""
    lo, a, hi, b = left[0], left[1], right[0], right[1]
    if any(matrices[i] is None for i in range(lo, hi)):
        return None
    H = np.eye(3)
    transforms = [H]
    for i in range(lo, hi):
        H = matrices[i] @ H
        H = H / H[2, 2]
        transforms.append(H)
    est = warp(a, H)
    extent = (b[2:] - b[:2]).clip(1)
    delta = ((est[:2] + est[2:]) - (b[:2] + b[2:])) / (2 * extent)
    residual = np.linalg.norm(delta)
    if residual > 0.49 or np.max(np.abs(np.log((est[2:] - est[:2]).clip(1) / extent))) > 0.54:
        return None
    patches = [_patch(images[j], box) for j, box in [(lo, a), (hi, b)]]
    if any(p is None for p in patches):
        return None
    appearance = _hist_distance(*patches)
    if appearance > 0.48:
        return None
    result = {}
    inv = np.linalg.inv(H)
    for k, T in enumerate(transforms[1:-1], 1):
        alpha = k / (hi - lo)
        box = (1 - alpha) * warp(a, T) + alpha * warp(b, T @ inv)
        patch = _patch(images[lo + k], box)
        error = min(_hist_distance(p, patch) for p in patches) if patch is not None else 1.0
        result[lo + k] = box, float(error)
    if not result or np.mean([error < 0.52 for box, error in result.values()]) < 0.50:
        return None
    return float(residual + appearance), result


def intervening_track(path, rows, scale):
    """Prefer a separately detected intermediate fragment to an overlapping inferred body."""
    witnesses = defaultdict(int)
    for i, (box, _) in path.items():
        for actor in rows[i]["actors"]:
            if actor["bbox_kind"] != "person_detection":
                continue
            other = np.asarray(actor["bbox"]) * scale
            union_box = np.maximum(
                0, np.minimum(other[2:], box[2:]) - np.maximum(other[:2], box[:2])
            )
            intersection = np.prod(union_box)
            union = np.prod(box[2:] - box[:2]) + np.prod(other[2:] - other[:2]) - intersection
            if union > 0 and intersection / union > 0.55:
                witnesses[actor["track_id"]] += 1
                if witnesses[actor["track_id"]] >= 2:
                    return True
    return False


def fill_stationary_fragments(video, rows, width):
    tracks = defaultdict(list)
    for row in rows:
        for actor in row["actors"]:
            if actor["bbox_kind"] == "person_detection":
                tracks[(row["shot_id"], actor["track_id"])].append((row["source_frame"], actor))
    spans = defaultdict(list)
    for row in rows:
        for actor in row["actors"]:
            spans[actor["track_id"]].append(row["source_frame"])
    candidates = [
        (left, right)
        for (shot, lid), left in tracks.items()
        for (shot2, rid), right in tracks.items()
        if shot == shot2
        and lid != rid
        and min(len(left), len(right)) >= 3
        and max(spans[lid]) == left[-1][0]
        and min(spans[rid]) == right[0][0]
        and 3 <= right[0][0] - left[-1][0] <= 32
    ]
    if not candidates:
        return
    wanted = {i for left, right in candidates for i in range(left[-1][0], right[0][0] + 1)}
    scale = min(1.0, 640 / width)
    images = {}
    with av.open(str(video)) as source:
        for i, frame in enumerate(source.decode(video=0)):
            if i in wanted:
                images[i] = cv2.resize(
                    frame.to_ndarray(format="bgr24"),
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )
    pairs = {i for left, right in candidates for i in range(left[-1][0], right[0][0])}
    matrices = {
        i: background_transform(
            images[i], images[i + 1], [np.asarray(a["bbox"]) * scale for a in rows[i]["actors"]]
        )
        for i in pairs
    }
    proposals = []
    for left, right in candidates:
        lo, a = left[-1]
        hi, b = right[0]
        found = match_fragments(
            images,
            matrices,
            (lo, np.asarray(a["bbox"]) * scale),
            (hi, np.asarray(b["bbox"]) * scale),
        )
        if found:
            cost, path = found
            if not intervening_track(path, rows, scale):
                proposals.append((cost, lo, a, hi, b, path))
    redirects = {}
    used = set()
    for cost, lo, a, hi, b, path in sorted(proposals, key=lambda item: item[0]):
        aid, bid = a["track_id"], b["track_id"]
        if ("end", aid) in used or ("start", bid) in used:
            continue
        if any(
            other is not path
            and abs(c - cost) < 0.12
            and (x["track_id"] == aid or y["track_id"] == bid)
            for c, _, x, _, y, other in proposals
        ):
            continue
        used.update((("end", aid), ("start", bid)))
        redirects[bid] = aid
        for i, (box, appearance) in path.items():
            t = (i - lo) / (hi - lo)
            obj = deepcopy(a if t <= 0.5 else b)
            obj.update(
                track_id=aid,
                body_id=None,
                bbox=(box / scale).tolist(),
                bbox_kind="bounded_person_flow",
                detector_score=None,
            )
            for key in (
                "face",
                "person_region",
                "head_detection",
                "region_track",
                "tracked_face_landmarks",
            ):
                obj.pop(key, None)
            for name, j in obj["joints2d"].items():
                p, q = a["joints2d"][name], b["joints2d"][name]
                valid = all(v["visibility"] == "model_estimate" for v in (p, q))
                j.update(
                    u=(1 - t) * p["u"] + t * q["u"] if valid else None,
                    v=(1 - t) * p["v"] + t * q["v"] if valid else None,
                    visibility="inferred" if valid else "unknown",
                    score=0.0,
                    source="background_reprojection",
                )
            obj["interpolation_endpoints"] = (a, b, t)
            obj["temporal_support"] = {
                "source_frames": [lo, hi],
                "state": "inferred_from_background_motion",
                "association_cost": cost,
                "clothing_distance": appearance,
            }
            rows[i]["actors"].append(obj)
    for row in rows:
        for actor in row["actors"]:
            while actor["track_id"] in redirects:
                actor["track_id"] = redirects[actor["track_id"]]
