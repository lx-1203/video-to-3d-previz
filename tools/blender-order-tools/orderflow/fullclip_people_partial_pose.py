"""Constrain a repeatedly observed partial person's torso using its visible source silhouette."""

import numpy as np

if __package__:
    from .fullclip_people_partial_motion import stabilize_partial_motion
else:
    from fullclip_people_partial_motion import stabilize_partial_motion


def _unit(v):
    return v / max(1e-8, np.linalg.norm(v))


def _sample(actor):
    info = actor["partial_body_track"]
    box = np.array(info["visible_bbox"])
    wh = np.maximum(5.0, box[2:] - box[:2])
    pairs = {}
    quality = []
    for name in ("shoulder", "hip"):
        pts = []
        for side in ("left", "right"):
            j = actor["joints2d"][side + "_" + name]
            if j["u"] is None or j["score"] < 0.21:
                return None
            uv = np.array([j["u"], j["v"]])
            if np.any(uv < box[:2] - 0.29 * wh) or np.any(uv > box[2:] + 0.29 * wh):
                return None
            pts.append((uv - box[:2]) / wh)
            quality.append(j["score"])
        pairs[name] = np.array(pts)
    face = actor["joints2d"]["nose"]
    if face["u"] is None or face["score"] < 0.21:
        return None
    head = (np.array([face["u"], face["v"]]) - box[:2]) / wh
    if np.any(head < -0.24) or np.any(head > 1.24):
        return None
    sh = pairs["shoulder"].mean(0)
    hip = pairs["hip"].mean(0)
    # Lowered head inside the upper-body span; an upright fragment is a negative.
    if (head[1] - sh[1]) < -0.11 or np.linalg.norm((sh - hip) * wh) < 0.06 * np.linalg.norm(wh):
        return None
    return dict(
        shoulder=sh,
        hip=hip,
        head=head,
        lateral=pairs["shoulder"][0] - pairs["shoulder"][1],
        quality=float(np.median(quality)),
        profile=np.asarray(info.get("outline_profile", [])),
    )


def apply_partial_posture(entries, profile):
    """Retain fixed limb lengths, with inferred directions labelled on the source track."""
    subset = {i: a for i, a in entries if a.get("partial_body_track")}
    if not subset:
        return
    donors = {i: q for i, a in subset.items() if (q := _sample(a)) is not None}
    if len(donors) < 4:
        return
    negatives = []
    for a in subset.values():
        js = a["joints2d"]
        face = js["nose"]
        left = js["left_shoulder"]
        right = js["right_shoulder"]
        if min(v["score"] for v in (face, left, right)) < 0.40:
            continue
        info = a["partial_body_track"]
        box = info["visible_bbox"]
        if face["v"] < (left["v"] + right["v"]) * 0.5 - 0.15 * (box[3] - box[1]):
            negatives.append(np.asarray(info.get("outline_profile", [])))
    for i, a in subset.items():
        info = a["partial_body_track"]
        box = np.array(info["visible_bbox"])
        wh = np.maximum(5, box[2:] - box[:2])
        signature = np.asarray(info.get("outline_profile", []))
        options = []
        for j, d in donors.items():
            dt = abs(j - i)
            if dt > 30:
                continue
            discrepancy = (
                np.mean(np.abs(signature - d["profile"]))
                if len(signature) and len(d["profile"])
                else 0.0
            )
            if dt > 9 and (not len(signature) or discrepancy > 0.095):
                continue
            if discrepancy > 0.29:
                continue
            options.append((d, d["quality"] ** 2 * np.exp(-dt / 5.5 - 4 * discrepancy)))
        inferred_shape = False
        if len(options) < 2 and len(signature):
            # Poorly resolved hips may coincide with the shoulders. Transfer a
            # coarse pose only from the same continuously confirmed clothing
            # silhouette, with several independent, mutually consistent donors.
            alternatives = []
            for j, d in donors.items():
                if not len(d["profile"]) or any(
                    f not in subset for f in range(min(i, j), max(i, j) + 1)
                ):
                    continue
                b = subset[j]["partial_body_track"]["visible_bbox"]
                ref_aspect = (b[2] - b[0]) / max(1, b[3] - b[1])
                distance = np.mean(abs(signature - d["profile"])) + 0.11 * abs(
                    np.log(wh[0] / wh[1] / ref_aspect)
                )
                if distance < 0.23:
                    alternatives.append((distance, j, d))
            pool = sorted(alternatives, key=lambda v: v[:2])[:6]
            negative = min((np.mean(abs(v - signature)) for v in negatives if len(v)), default=1.0)
            if len(pool) >= 3 and pool[0][0] + 0.04 < negative:
                spread = np.max(np.std([d["shoulder"] - d["hip"] for _, _, d in pool], axis=0))
                if spread < 0.25:
                    options = [
                        (d, d["quality"] * np.exp(-15 * distance)) for distance, j, d in pool
                    ]
                    inferred_shape = True
        if len(options) < 2:
            continue

        def avg(k):
            return np.average([d[k] for d, _ in options], axis=0, weights=[w for _, w in options])

        sh, hip, face, lateral = [avg(k) for k in ("shoulder", "hip", "head", "lateral")]
        span = (sh - hip) * wh
        across = lateral * wh
        head = (face - sh) * wh
        if head[1] < -0.11 * wh[1] or np.linalg.norm(span) < 5:
            continue
        pixel_scale = max(
            np.linalg.norm(across) / profile["shoulder_width"],
            np.linalg.norm(span) / (profile["torso"] * 0.96),
        )
        if pixel_scale < 1:
            continue
        flat = np.array([span[0], 0.0, -span[1]]) / (pixel_scale * profile["torso"])
        # Anatomical left projecting to screen right supports a toward-camera bend.
        # Opposite/unresolved side views retain a chiefly lateral torso direction.
        front = float(np.clip(across[0] / max(1.0, np.linalg.norm(across)), -1.0, 1.0))
        depth = -np.sign(front) * np.sqrt(max(0.0, 1.0 - np.dot(flat, flat)))
        up = _unit(flat + np.array([0.0, depth, 0.0]))
        left = np.array([across[0], 0.0, -across[1]]) / (pixel_scale * profile["shoulder_width"])
        # In side view the shoulders' hidden separation is inferred from the direction of reach.
        left[1] = np.sign(span[0]) * np.sqrt(max(0.0, 1.0 - np.dot(left, left)))
        left = _unit(left - up * np.dot(left, up))
        if np.linalg.norm(left) < 0.5:
            continue
        joints = {k: np.array(p) for k, p in a["joints3d"].items() if p is not None}
        old = {k: p.copy() for k, p in joints.items()}
        pelvis = joints["hip"]
        shoulder = pelvis + up * profile["torso"]
        for side, sign in (("left", 1), ("right", -1)):
            proximal = side + "_shoulder"
            correction = shoulder + sign * left * 0.5 * profile["shoulder_width"] - old[proximal]
            for name in ("shoulder", "elbow", "wrist"):
                k = side + "_" + name
                joints[k] = old[k] + correction
        face_flat = np.array([head[0], 0.0, -head[1]]) / (pixel_scale * profile["head_offset"])
        if np.linalg.norm(face_flat) > 0.97:
            face_flat = _unit(face_flat) * 0.97
        hdir = _unit(
            face_flat
            + np.array(
                [
                    0.0,
                    -np.sign(front) * np.sqrt(max(0.0, 1.0 - np.dot(face_flat, face_flat))),
                    0.0,
                ]
            )
        )
        joints.update(shoulder=shoulder, head=shoulder + hdir * profile["head_offset"])
        a["joints3d"] = {
            **a["joints3d"],
            **{k: v.round(5).tolist() for k, v in joints.items()},
        }
        a["orientation"].update(
            torso_up_xyz=up.round(5).tolist(),
            face_up_uv=None,
            head_forward_xyz=None,
            facing_state="partial_bow_inferred",
        )
        info.update(
            torso_state=(
                "same_track_clothing_shape_inference"
                if inferred_shape
                else "source_lowered_face_and_foreshortening_inference"
            ),
            shoulder_uv=(box[:2] + sh * wh).round(2).tolist(),
            hip_uv=(box[:2] + hip * wh).round(2).tolist(),
            face_uv=(box[:2] + face * wh).round(2).tolist(),
            relative_depth_observed=False,
        )
    stabilize_partial_motion(entries, profile, _sample)
