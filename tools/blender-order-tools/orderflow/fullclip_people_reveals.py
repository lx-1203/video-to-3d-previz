"""Trace the visible clothes of a small person backwards to a same-shot foreground occlusion."""

from collections import defaultdict
from copy import deepcopy

import av
import cv2
import numpy as np


def torso_tiles(actor, rgb):
    points = []
    for name in ("left_shoulder", "right_shoulder", "left_hip", "right_hip"):
        j = actor["joints2d"].get(name, {})
        if j.get("score", 0) < 0.35:
            return []
        points.append(np.array([j["u"], j["v"]]))
    left, right, lower_l, lower_r = points
    radius = max(4, round(np.linalg.norm(right - left) * 0.19))
    centers = []
    for height in (0.06, 0.38, 0.73):
        for side in (0.06, 0.50, 0.94):
            top = left * (1 - side) + right * side
            bottom = lower_l * (1 - side) + lower_r * side
            centers.append(top * (1 - height) + bottom * height)
    for side in ("left", "right"):
        elbow = actor["joints2d"].get(side + "_elbow", {})
        if elbow.get("score", 0) >= 0.45:
            arm = np.array([elbow["u"], elbow["v"]])
            shoulder = left if side == "left" else right
            centers.extend((0.45 * arm + 0.55 * shoulder, 0.8 * arm + 0.2 * shoulder))
    tiles = []
    for center in centers:
        x, y = np.rint(center).astype(int)
        tile = rgb[max(0, y - radius) : y + radius + 1, max(0, x - radius) : x + radius + 1]
        if tile.shape[:2] == (2 * radius + 1, 2 * radius + 1) and tile.std(axis=(0, 1)).max() >= 5:
            tiles.append((np.array([x, y]), tile.copy()))
    return tiles


def clothing_step(rgb, tiles, offset, radius):
    offsets = []
    for center, patch in tiles:
        h = patch.shape[0] // 2
        x, y = np.rint(center + offset).astype(int)
        x0, y0 = max(0, x - radius - h), max(0, y - radius - h)
        region = rgb[y0 : y + radius + h + 1, x0 : x + radius + h + 1]
        if min(region.shape[:2]) < 2 * h + 1:
            continue
        _, corr, _, pos = cv2.minMaxLoc(cv2.matchTemplate(region, patch, cv2.TM_CCOEFF_NORMED))
        u, v = pos
        match = region[v : v + 2 * h + 1, u : u + 2 * h + 1]
        if corr >= 0.66 and np.abs(match.astype(float) - patch).mean() < 17:
            offsets.append(np.array([x0 + u + h, y0 + v + h]) - center)
    if len(offsets) < 2:
        return None
    offsets = np.asarray(offsets)
    distances = np.linalg.norm(offsets[:, None] - offsets[None], axis=2)
    agree = distances <= max(3, radius * 0.35)
    keep = agree[np.argmax(agree.sum(axis=1))]
    if keep.sum() < 2:
        return None
    return np.median(offsets[keep], axis=0), int(keep.sum())


def near_occluder(box, row, track):
    center = (box[:2] + box[2:]) / 2
    for a in row["actors"]:
        if a["track_id"] == track or a["bbox_kind"] != "person_detection":
            continue
        b = np.array(a["bbox"])
        height = b[3] - b[1]
        if height < 1.5 * (box[3] - box[1]):
            continue
        if b[0] <= center[0] <= b[2] and b[1] <= center[1] <= b[3]:
            return True
    return False


def trace_reveal(images, rows, start, actor, lower):
    box = np.array(actor["bbox"])
    tiles = torso_tiles(actor, images[start])
    if len(tiles) < 4:
        return []
    radius = max(7, round(0.15 * (box[2] - box[0])))
    offset = np.zeros(2)
    path = []
    steps = []
    for i in range(start - 1, lower - 1, -1):
        if rows[i]["shot_id"] != rows[start]["shot_id"]:
            break
        found = clothing_step(images[i], tiles, offset, radius)
        if found is None:
            break
        proposed, n = found
        if n == 2:
            # As a sleeve disappears, two independent surviving patches may
            # still support a small displacement predicted by the same track.
            if len(steps) < 4 or not near_occluder(
                box + np.tile(proposed, 2), rows[i], actor["track_id"]
            ):
                break
            prior = offset + np.median(steps[-4:], axis=0)
            if np.linalg.norm(proposed - prior) > 0.12 * (box[2] - box[0]):
                break
            proposed = (prior + proposed) / 2
        steps.append(proposed - offset)
        offset = proposed
        result_box = box + np.tile(offset, 2)
        # Another equally sized detection is an alternative identity.
        for other in rows[i]["actors"]:
            b = np.asarray(other["bbox"])
            if np.max(np.abs(b - result_box) / np.tile(box[2:] - box[:2], 2)) < 0.38:
                return []
        path.append((i, offset, n))
    # An arbitrary search limit or the disappearance of a texture in empty
    # space is insufficient evidence of an earlier entrance behind a person.
    if len(path) < 2 or path[-1][0] <= lower:
        return []
    return (
        path
        if near_occluder(box + np.tile(offset, 2), rows[path[-1][0]], actor["track_id"])
        else []
    )


def extend_reveals(video, rows, width, height):
    tracks = defaultdict(list)
    for row in rows:
        for a in row["actors"]:
            tracks[row["shot_id"], a["track_id"]].append((row["source_frame"], a))
    candidates = []
    for (shot, _), seq in tracks.items():
        start, a = seq[0]
        if a["bbox_kind"] != "person_detection" or len(seq) < 3 or start == 0:
            continue
        x0, y0, x1, y1 = a["bbox"]
        if y1 - y0 > height * 0.45 or x0 < 2 or x1 >= width - 2 or y0 < 2 or y1 >= height - 2:
            continue
        lower = start
        while lower and start - lower < 40 and rows[lower - 1]["shot_id"] == shot:
            lower -= 1
        if start - lower >= 3:
            candidates.append((start, a, lower))
    if not candidates:
        return
    wanted = {i for start, _, lower in candidates for i in range(lower, start + 1)}
    by_start = defaultdict(list)
    for start, actor, lower in candidates:
        by_start[start].append((actor, lower))
    images = {}
    paths = []
    last = max(wanted)
    with av.open(str(video)) as container:
        for i, frame in enumerate(container.decode(video=0)):
            images.pop(i - 41, None)
            if i in wanted:
                images[i] = frame.to_ndarray(format="bgr24")
            for donor, lower in by_start.get(i, ()):
                paths.append((i, donor, trace_reveal(images, rows, i, donor, lower)))
            if i >= last:
                break
    for start, donor, path in paths:
        for i, offset, count in path:
            a = {
                field: deepcopy(donor[field])
                for field in (
                    "track_id",
                    "anchors",
                    "joints2d",
                    "joints3d",
                    "joint3d_state",
                    "pixels_per_meter",
                    "orientation",
                    "identity_state",
                    "renderable",
                    "color_key",
                    "color_evidence",
                )
            }
            a.update(bbox_kind="backtracked_clothing", detector_score=None, body_id=None)
            a["bbox"] = (np.array(donor["bbox"]) + np.tile(offset, 2)).tolist()
            a["root_uv"] = (np.array(donor["root_uv"]) + offset).tolist()
            a["temporal_support"] = dict(
                state="source_clothing_translation_prior_pose",
                source_frames=[i, start],
                offset_uv=offset.tolist(),
                matching_tiles=count,
            )
            for group in ("anchors", "joints2d"):
                for j in a[group].values():
                    if not j:
                        continue
                    if j.get("u") is not None and j.get("v") is not None:
                        j["u"] += offset[0]
                        j["v"] += offset[1]
                    j["score"] = 0.0
                    if group == "anchors":
                        j["state"] = "backtracked_clothing"
                    else:
                        j.update(
                            visibility="inferred" if j.get("u") is not None else "unknown",
                            source="backtracked_clothing",
                        )
            a["joint3d_state"] = dict.fromkeys(a["joint3d_state"], "inferred_from_later_pose")
            a["orientation"].update(face_score=0, face_yaw_proxy=0)
            rows[i]["actors"].append(a)
