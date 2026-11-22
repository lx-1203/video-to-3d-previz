"""Resolve weak monocular torso orientations along a contiguous partial-person track."""

import numpy as np


def _unit(v):
    return v / max(1e-8, np.linalg.norm(v))


def _path(axes, signatures, trusted, eligible):
    """Choose near-time orientation candidates, pinning independently observed changes."""
    n = len(axes)
    scores = np.empty(0)
    history = []
    indices = []
    for i in range(n):
        candidates = np.array([i]) if trusted[i] else np.arange(max(0, i - 10), min(n, i + 11))
        candidates = candidates[eligible[candidates]]
        emission = 4 * np.mean(
            np.abs(signatures[candidates] - signatures[i]), axis=1
        ) + 0.009 * np.abs(candidates - i)
        if i:
            diff = axes[indices[-1]][:, None] - axes[candidates][None]
            movement = (diff**2).sum(axis=-1) @ np.array([5.0, 2.0, 4.0])
            prev = scores[:, None] + movement
            back = np.argmin(prev, axis=0)
            scores = emission + prev[back, np.arange(len(candidates))]
            history.append(back)
        else:
            scores = emission
        indices.append(candidates)
    state = int(np.argmin(scores))
    path = []
    for i in reversed(range(n)):
        path.append(indices[i][state])
        if i:
            state = history[i - 1][state]
    return np.array(path[::-1])


def _stabilize_run(actors, profile, observe):
    if len(actors) < 4:
        return
    parts = [a["partial_body_track"] for a in actors]
    samples = [observe(a) for a in actors]
    trusted = [
        bool(p.get("torso_state")) and s is not None and s["quality"] >= 0.33
        for p, s in zip(parts, samples)
    ]
    if all(trusted):
        return
    joints = [{k: np.array(v) for k, v in a["joints3d"].items() if v is not None} for a in actors]
    axes = np.array(
        [
            [
                _unit(j["shoulder"] - j["hip"]),
                _unit(j["left_shoulder"] - j["right_shoulder"]),
                _unit(j["head"] - j["shoulder"]),
            ]
            for j in joints
        ]
    )
    boxes = np.array([p["visible_bbox"] for p in parts])
    sizes = np.maximum(5.0, boxes[:, 2:] - boxes[:, :2])
    signatures = np.array([p["outline_profile"] for p in parts])
    signatures = np.column_stack((signatures, np.log(sizes[:, 0] / sizes[:, 1])))
    keys = ("shoulder_uv", "hip_uv", "face_uv")
    eligible = np.array([bool(p.get("torso_state")) for p in parts])
    anchors = (
        np.array([[p.get(k, [0.0, 0.0]) for k in keys] for p in parts]) - boxes[:, None, :2]
    ) / sizes[:, None, :]
    path = _path(axes, signatures, trusted, eligible)
    for i, (a, old) in enumerate(zip(actors, joints)):
        if trusted[i]:
            continue
        nearby = np.arange(max(0, i - 2), min(len(actors), i + 3))
        weights = (3 - abs(nearby - i)) * np.exp(
            -2 * np.mean(abs(signatures[nearby] - signatures[i]), axis=1)
        )
        chosen = path[nearby]
        up, lateral, head = np.average(axes[chosen], axis=0, weights=weights)
        if np.linalg.norm(up) < 0.3:
            continue
        up = _unit(up)
        lateral = _unit(lateral - up * np.dot(lateral, up))
        if np.linalg.norm(lateral) < 0.9 or np.linalg.norm(head) < 0.3:
            continue
        shoulder = old["hip"] + profile["torso"] * up
        replace = {
            "shoulder": shoulder,
            "head": shoulder + profile["head_offset"] * _unit(head),
        }
        for side, sign in (("left", 1), ("right", -1)):
            key = side + "_shoulder"
            delta = shoulder + sign * 0.5 * profile["shoulder_width"] * lateral - old[key]
            for name in ("shoulder", "elbow", "wrist"):
                key = side + "_" + name
                replace[key] = old[key] + delta
        a["joints3d"].update({k: v.round(5).tolist() for k, v in replace.items()})
        a["orientation"]["torso_up_xyz"] = up.round(5).tolist()
        pos = boxes[i, :2] + np.average(anchors[chosen], axis=0, weights=weights) * sizes[i]
        parts[i].update({k: uv.round(2).tolist() for k, uv in zip(keys, pos)})
        if not eligible[i]:
            parts[i]["torso_state"] = "neighbor_contour_bridge_inference"
            parts[i]["relative_depth_observed"] = False
            a["orientation"].update(
                face_up_uv=None,
                head_forward_xyz=None,
                facing_state="partial_bow_inferred",
            )
        parts[i]["temporal_orientation"] = "local_contour_continuity_inference"


def stabilize_partial_motion(entries, profile, observe):
    """Only operate on contiguous, identified, source-supported partial-torso intervals."""
    entries = list(entries)
    pending = []
    for index, (i, a) in enumerate(entries):
        p = a.get("partial_body_track", {})
        supported = bool(p.get("torso_state")) and all(
            k in p for k in ("shoulder_uv", "hip_uv", "face_uv")
        )
        before = entries[index - 1] if index else None
        after = entries[index + 1] if index + 1 < len(entries) else None
        continuity = bool(
            before and before[0] + 1 == i and before[1].get("track_id") == a.get("track_id")
        )
        usable = len(p.get("outline_profile", ())) == 8
        if usable and not supported and continuity and after:
            neighbors = [
                before[1].get("partial_body_track", {}),
                after[1].get("partial_body_track", {}),
            ]
            # Allow only a single unresolved frame between compatible measured
            # coats, with little relative travel and no convincing joint evidence.
            if (
                after[0] == i + 1
                and after[1].get("track_id") == a.get("track_id")
                and all(
                    q.get("torso_state")
                    and len(q.get("outline_profile", ())) == 8
                    and "visible_centroid_uv" in q
                    and np.mean(np.abs(np.asarray(q["outline_profile"]) - p["outline_profile"]))
                    < 0.23
                    for q in neighbors
                )
                and "visible_centroid_uv" in p
                and max(
                    a["joints2d"][k]["score"] for k in ("nose", "left_shoulder", "right_shoulder")
                )
                < 0.32
            ):
                centers = [np.asarray(q["visible_centroid_uv"]) for q in neighbors]
                box = np.array(p["visible_bbox"])
                supported = np.linalg.norm(
                    np.mean(centers, axis=0) - p["visible_centroid_uv"]
                ) < 0.16 * np.linalg.norm(box[2:] - box[:2])
        if not continuity or not (usable and supported):
            _stabilize_run(pending, profile, observe)
            pending = []
        if usable and supported:
            pending.append(a)
    _stabilize_run(pending, profile, observe)
