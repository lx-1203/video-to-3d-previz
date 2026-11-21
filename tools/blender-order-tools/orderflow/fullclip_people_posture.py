"""Screen gravity and nearby sleeve evidence for a bent, partially cropped actor."""

from collections import defaultdict
import cv2
import numpy as np

if __package__:
    from .fullclip_people_solve import unit
else:
    from fullclip_people_solve import unit


def cloth_patch(lab, point, radius):
    x, y = np.rint(point).astype(int)
    h, w = lab.shape[:2]
    crop = lab[
        max(0, y - radius) : min(h, y + radius + 1), max(0, x - radius) : min(w, x + radius + 1)
    ]
    if crop.size < 12:
        return None
    return np.median(crop.reshape(-1, 3), axis=0)


def sleeve_sample(actor, lab, scale, side, start, end):
    joints = actor["joints2d"]
    a, b = [joints[side + "_" + k] for k in (start, end)]
    if min(a["score"], b["score"]) < 0.32 or a["u"] is None or b["u"] is None:
        return None
    region = actor.get("head_detection") or actor.get("face")
    if not region:
        return None
    box = np.asarray(region["bbox"], dtype=float)
    dim = max(box[2:] - box[:2])
    p0, p1 = (np.array([p["u"], p["v"]]) for p in (a, b))
    delta = p1 - p0
    length = np.linalg.norm(delta)
    if not 0.16 * dim <= length <= 1.85 * dim or (
        start == "shoulder" and delta[1] < 0.30 * length
    ):
        return None
    # A low-confidence hand sitting within a measured head cannot demonstrate
    # a support contact. Preserve strong, directly measured hands in the solver.
    center = (box[:2] + box[2:]) / 2
    if end == "wrist" and np.linalg.norm((p1 - center) / np.maximum(1, box[2:] - box[:2])) < 0.43:
        return None
    patches = [
        cloth_patch(lab, (p0 + t * delta) * scale, max(2, int(dim * scale * 0.025)))
        for t in (0.15, 0.35, 0.57, 0.76)
    ]
    if any(p is None for p in patches):
        return None
    diff = np.abs(np.array(patches) - patches[0])
    # Similar chroma at a run of positions supports a clothing-covered segment.
    if np.median(diff[:, 1:]) > 9.0 or np.median(diff[:, 0]) > 40.0:
        return None
    return delta / length


def image_vertical(gray):
    edges = cv2.Canny(gray, 38, 105)
    h, w = gray.shape
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 25, minLineLength=max(18, h // 5), maxLineGap=5)
    if lines is None:
        return None
    angles = []
    for x, y, xx, yy in lines[:, 0]:
        dx, dy = xx - x, yy - y
        if abs(dy) > 2.8 * abs(dx):
            angles.append(np.arctan(dx / dy))
    if len(angles) < 3 or np.median(np.abs(angles - np.median(angles))) > 0.12:
        return None
    return float(np.median(angles))


def apply(video, rows, width):
    import av

    targets = {
        r["source_frame"]: [a for a in r["actors"] if a.get("shoulder_support")] for r in rows
    }
    targets = {i: ls for i, ls in targets.items() if ls}
    if not targets:
        return
    scale = min(1.0, 400 / width)
    cues = defaultdict(list)
    verticals = defaultdict(list)
    with av.open(str(video)) as c:
        for i, f in enumerate(c.decode(video=0)):
            if i not in targets:
                continue
            img = cv2.resize(
                f.to_ndarray(format="bgr24"),
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            shot = rows[i]["shot_id"]
            if i % 5 == 0:
                angle = image_vertical(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
                if angle is not None:
                    verticals[shot].append(angle)
            for a in targets[i]:
                for side in ("left", "right"):
                    for parent, child in (("shoulder", "elbow"), ("elbow", "wrist")):
                        direction = sleeve_sample(a, lab, scale, side, parent, child)
                        if direction is not None:
                            cues[(a["track_id"], side + "_" + child)].append((i, direction))
    for i, actors in targets.items():
        shot = rows[i]["shot_id"]
        angles = verticals[shot]
        angle = float(np.median(angles)) if len(angles) >= 3 else 0.0
        for a in actors:
            gravity = [float(np.sin(angle)), 0.0, -float(np.cos(angle))]
            hints = {}
            for side in ("left", "right"):
                for end in ("elbow", "wrist"):
                    key = side + "_" + end
                    near = [(j, v) for j, v in cues[(a["track_id"], key)] if abs(j - i) <= 14]
                    if len(near) < 3:
                        continue
                    direction = np.median([v for _, v in near], axis=0)
                    if np.linalg.norm(direction) < 0.76:
                        continue
                    hints[key] = {
                        "uv": unit(direction, [0, 1]).round(5).tolist(),
                        "frames": [j for j, _ in near],
                        "state": "nearby_sleeve_direction_inferred",
                    }
            a["posture_priors"] = {
                "gravity_xyz": gravity,
                "gravity_source": "image_structural_vertical"
                if len(angles) >= 3
                else "image_vertical_prior",
                "sleeves": hints,
                "support_contact_verified": False,
            }
