"""Fuse anonymous shot-local proposals and propagate short gaps with measured optical flow."""

from collections import defaultdict
import cv2
import numpy as np
import av

if __package__:
    from .fullclip_people_faces import overlap
else:
    from fullclip_people_faces import overlap
if __package__:
    from .fullclip_people_observe import JOINTS
else:
    from fullclip_people_observe import JOINTS


def midpoint(box):
    return (np.array(box[:2]) + box[2:]) / 2


def empty_joints():
    return {
        j: {"u": None, "v": None, "score": 0.0, "visibility": "unknown", "source": "missing"}
        for j in JOINTS
    }


def head_region(actor):
    for field in ("face", "head_detection"):
        if field in actor:
            return actor[field]["bbox"]
    box = actor["bbox"]
    width = box[2] - box[0]
    height = box[3] - box[1]
    nose = actor["joints2d"]["nose"]
    if nose["visibility"] == "model_estimate":
        cx, cy = nose["u"], nose["v"]
    else:
        cx, cy = (box[0] + box[2]) / 2, box[1] + height * 0.16
    size = max(12, min(width * 0.74, height * 0.36))
    return [cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2]


def merge_heads(rows, head_rows, width, height):
    if head_rows is None:
        return
    if len(rows) != len(head_rows):
        raise ValueError("Incomplete head detector frame map")
    for row, extra in zip(rows, head_rows):
        if any(row[k] != extra[k] for k in ("source_frame", "pts", "time_base")):
            raise ValueError("Head observations do not map to source frame")
        people = sorted(
            (v for v in extra["items"] if v["class_id"] == 0), key=lambda x: -x["score"]
        )
        heads = sorted(
            (v for v in extra["items"] if v["class_id"] == 1), key=lambda x: -x["score"]
        )
        retained = []
        for head in heads:
            box = head["bbox"]
            if (
                head["score"] < 0.32
                or (box[2] - box[0]) * (box[3] - box[1]) < 0.025 * width * height
            ):
                continue
            if any(overlap(box, b) > 0.24 for b in retained):
                continue
            center = midpoint(box)
            choices = []
            for actor in row["actors"]:
                hbox = head_region(actor)
                size = max(1, box[2] - box[0], box[3] - box[1])
                d = np.linalg.norm(midpoint(hbox) - center) / size
                covers = (
                    actor["bbox"][0] <= center[0] <= actor["bbox"][2]
                    and actor["bbox"][1] <= center[1] <= actor["bbox"][3]
                )
                if (
                    overlap(box, hbox) > 0.1
                    or d < 0.54
                    or (covers and actor["bbox_kind"] == "unboxed_pose_extent")
                ):
                    choices.append((d, actor))
            if choices:
                target = min(choices, key=lambda x: x[0])[1]
                if "head_detection" not in target:
                    target["head_detection"] = head
            else:
                target = {
                    "bbox": box,
                    "bbox_kind": "head_detection",
                    "detector_score": head["score"],
                    "body_id": None,
                    "head_detection": head,
                    "joints2d": empty_joints(),
                }
                row["actors"].append(target)
            for person in people:
                pbox = person["bbox"]
                if (
                    pbox[0] <= center[0] <= pbox[2]
                    and pbox[1] <= center[1] <= pbox[3]
                    and (pbox[2] - pbox[0]) * (pbox[3] - pbox[1])
                    > 1.5 * (box[2] - box[0]) * (box[3] - box[1])
                ):
                    target["person_region"] = person
                    break
            retained.append(box)


def assign(rows):
    for index, row in enumerate(rows):
        if not index or rows[index - 1]["shot_id"] != row["shot_id"]:
            live = {}
            count = 0
        actors = row["actors"]
        costs = []
        for k, actor in enumerate(actors):
            b = head_region(actor)
            for track, (last, old) in live.items():
                gap = index - last
                if gap > 20:
                    continue
                ob = head_region(old)
                extent = max(15.0, max(b[2] - b[0], b[3] - b[1], ob[2] - ob[0], ob[3] - ob[1]))
                d = np.linalg.norm(midpoint(ob) - midpoint(b)) / extent
                same = actor["body_id"] is not None and actor["body_id"] == old["body_id"]
                different = (
                    actor["body_id"] is not None
                    and old["body_id"] is not None
                    and actor["body_id"] != old["body_id"]
                )
                if not different and ((same and gap <= 8) or d < min(1.5, 0.74 + 0.045 * gap)):
                    cost = d + 0.02 * gap - 0.72 * same - 0.25 * overlap(ob, b)
                    costs.append((cost, track, k))
        taken = set()
        for _, track, k in sorted(costs):
            if track not in taken and "track_id" not in actors[k]:
                actors[k]["track_id"] = track
                taken.add(track)
        for actor in actors:
            if "track_id" not in actor:
                count += 1
                actor["track_id"] = f"{row['shot_id']}_P{count:02d}"
            live[actor["track_id"]] = (index, actor)


def follow(frames, start, end, box, scale):
    """Follow real local texture with forward/backward consistency on consecutive decoded frames."""
    tracked = np.array(box, dtype=float) * scale
    direction = 1 if end > start else -1
    out = {}
    for i in range(start, end, direction):
        gray, nxt = frames[i], frames[i + direction]
        h, w = gray.shape
        x1, y1, x2, y2 = tracked
        mask = np.zeros((h, w), np.uint8)
        center = tuple(np.rint([(x1 + x2) / 2, (y1 + y2) / 2]).astype(int))
        axes = tuple(np.maximum(1, np.rint([0.42 * (x2 - x1), 0.4 * (y2 - y1)]).astype(int)))
        cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
        pts = cv2.goodFeaturesToTrack(gray, 60, 0.012, 3, mask=mask)
        if pts is None or len(pts) < 6:
            break
        ahead, ok, _ = cv2.calcOpticalFlowPyrLK(gray, nxt, pts, None, winSize=(21, 21), maxLevel=3)
        back, bok, _ = cv2.calcOpticalFlowPyrLK(
            nxt, gray, ahead, None, winSize=(21, 21), maxLevel=3
        )
        valid = (
            (ok[:, 0] > 0) & (bok[:, 0] > 0) & (np.linalg.norm((back - pts)[:, 0], axis=1) < 1.4)
        )
        if np.sum(valid) < 6 or np.mean(valid) < 0.40:
            break
        delta = (ahead - pts)[valid, 0]
        shift = np.median(delta, axis=0)
        dispersion = np.median(np.linalg.norm(delta - shift, axis=1))
        if dispersion > max(3.0, 0.07 * max(x2 - x1, y2 - y1)):
            break
        tracked += np.tile(shift, 2)
        if tracked[2] <= 0 or tracked[3] <= 0 or tracked[0] >= w or tracked[1] >= h:
            break
        quality = float(np.mean(valid) / (1.0 + 0.25 * dispersion))
        out[i + direction] = (tracked.copy() / scale, quality)
    return out


def fill_short_gaps(video, rows, width, height):
    # Head-only optical flow is appropriate for large heads. A weak body observation
    # does not become a measured shoulder, knee or foot through this mechanism.
    tracks = defaultdict(list)
    for row in rows:
        for actor in row["actors"]:
            box = head_region(actor)
            if (actor.get("face") or actor.get("head_detection")) and max(
                box[2] - box[0], box[3] - box[1]
            ) > 0.18 * height:
                tracks[actor["track_id"]].append((row["source_frame"], actor))
    if not tracks:
        return
    scale = min(1.0, 400 / width)
    frames = []
    with av.open(str(video)) as c:
        for fr in c.decode(video=0):
            gray = fr.to_ndarray(format="gray")
            frames.append(cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
    candidates = {}
    for tid, obs in tracks.items():
        if len(obs) < 2:
            continue
        seen = {f for f, _ in obs}
        for frame, actor in obs:
            for direction in (-1, 1):
                end = frame
                while abs(end - frame) < 10:
                    j = end + direction
                    if (
                        not 0 <= j < len(rows)
                        or rows[j]["shot_id"] != rows[frame]["shot_id"]
                        or j in seen
                    ):
                        break
                    if any(a["track_id"] == tid for a in rows[j]["actors"]):
                        break
                    end = j
                if end == frame:
                    continue
                for j, (box, quality) in follow(
                    frames, frame, end, head_region(actor), scale
                ).items():
                    if quality < 0.35:
                        continue
                    key = (j, tid)
                    candidate = (abs(j - frame), -quality, frame, box, actor)
                    if key not in candidates or candidate[:2] < candidates[key][:2]:
                        candidates[key] = candidate
    for (j, tid), (age, negq, frame, box, source) in sorted(candidates.items()):
        if any(overlap(box, head_region(a)) > 0.14 for a in rows[j]["actors"]):
            continue
        row = {
            "track_id": tid,
            "body_id": None,
            "bbox": box.tolist(),
            "bbox_kind": "optical_flow_head",
            "detector_score": None,
            "head_detection": {"bbox": box.tolist(), "score": 0.0, "kind": "optical_flow"},
            "flow": {"source_frame": frame, "steps": age, "feature_consistency": -negq},
            "joints2d": empty_joints(),
        }
        rows[j]["actors"].append(row)
