"""Fixed-length monocular pose proposals, expressed in the template's original units."""

import json
import math
from pathlib import Path

import numpy as np


def unit(vec, fallback):
    vec = np.asarray(vec, dtype=float)
    length = np.linalg.norm(vec)
    return vec / length if length > 1e-6 else np.asarray(fallback, dtype=float)


def point(p):
    return np.array([p["u"], p["v"]])


def read_profile(path):
    if not path:
        return dict(
            shoulder_width=0.40,
            hip_width=0.24,
            torso=0.50,
            neck=0.075,
            head_offset=0.20,
            head_diameter=0.24,
            arm=0.30,
            forearm=0.28,
            thigh=0.44,
            shin=0.43,
        )
    parts = json.loads(Path(path).read_text(encoding="utf-8-sig"))["components"]
    by_name = {p["weighted_group"]: p for p in parts}

    def proximal(name):
        return np.array(by_name[name]["proximal_endpoint"])

    shoulders = [proximal("DEF-upper_arm." + s) for s in ("L", "R")]
    hips = [proximal("DEF-thigh." + s) for s in ("L", "R")]
    shoulder = np.mean(shoulders, axis=0)
    head = by_name["DEF-spine.006"]
    return dict(
        shoulder_width=math.dist(*shoulders),
        hip_width=math.dist(*hips),
        torso=math.dist(shoulder, np.mean(hips, axis=0)),
        neck=float(head["proximal_endpoint"][2] - shoulder[2]),
        head_offset=math.dist(shoulder, head["center"]),
        head_diameter=head["axis_length"],
        **{
            name: float(np.mean([by_name[bone + "." + s]["axis_length"] for s in ("L", "R")]))
            for name, bone in (
                ("arm", "DEF-upper_arm"),
                ("forearm", "DEF-forearm"),
                ("thigh", "DEF-thigh"),
                ("shin", "DEF-shin"),
            )
        },
    )


def anchor(pos, score, state):
    return {
        "u": round(float(pos[0]), 3),
        "v": round(float(pos[1]), 3),
        "score": round(float(score), 5),
        "state": state,
    }


def observed(j):
    return j and j["visibility"] == "model_estimate"


def pair(joints, name):
    a, b = (joints[s + "_" + name] for s in ("left", "right"))
    if observed(a) and observed(b):
        return anchor((point(a) + point(b)) / 2, min(a["score"], b["score"]), "model_estimate")


def measure(actor, profile):
    if "interpolation_endpoints" in actor:
        left, right, alpha = actor["interpolation_endpoints"]
        start, end = measure(left, profile), measure(right, profile)
        out = {}
        for key in ("head", "shoulder", "hip"):
            a, b = start[key], end[key]
            out[key] = (
                anchor(point(a) * (1 - alpha) + point(b) * alpha, 0.0, "bounded_person_flow")
                if a and b
                else None
            )
        for key in ("yaw", "ppm"):
            out[key] = float(start[key] * (1 - alpha) + end[key] * alpha)
        out["face_score"] = 0.0
        out["up"] = unit(start["up"] * (1 - alpha) + end["up"] * alpha, start["up"])
        return out
    j = actor["joints2d"]
    sh, hip = pair(j, "shoulder"), pair(j, "hip")
    face = actor.get("face")
    up, yaw, face_score = np.array([0.0, -1.0]), 0.0, 0.0
    facial_scale = None
    head = None
    if face is None and actor.get("tracked_face_landmarks") is not None:
        face = {
            "landmarks": actor["tracked_face_landmarks"],
            "bbox": actor["head_detection"]["bbox"],
            "score": 0.0,
        }
    if face:
        re, le, nose, rm, lm = np.array(face["landmarks"])
        eye, mouth = (le + re) / 2, (lm + rm) / 2
        up = unit(eye - mouth, [0, -1])
        face_score = face["score"]
        right = unit(le - re, [1, 0])
        yaw = np.clip(np.dot(nose - eye, right) / max(np.linalg.norm(le - re) * 0.62, 1.0), -1, 1)
        fw, fh = np.subtract(face["bbox"][2:], face["bbox"][:2])
        # For rolled heads, measure along the eye-mouth axis, not screen height.
        cross_width = (fw if face.get("turns", 0) % 2 == 0 else fh) if face_score else min(fw, fh)
        head_px = max(
            np.linalg.norm(eye - mouth) * 2.35, np.linalg.norm(le - re) * 2.14, cross_width * 1.045
        )
        facial_scale = head_px / profile["head_diameter"]
        head = anchor(
            eye + up * 0.025 * head_px,
            face_score,
            "face_landmarks" if face_score else "tracked_face_estimate",
        )
    else:
        eyes = pair(j, "eye")
        ears = pair(j, "ear")
        nose = j["nose"]
        if eyes:
            head = anchor(point(eyes), eyes["score"], "eye_midpoint")
            le, re = j["left_eye"], j["right_eye"]
            across = point(le) - point(re)
            up = unit([across[1], -across[0]], [0, -1])
            if observed(nose):
                if np.dot(point(nose) - point(eyes), up) > 0:
                    up = -up
                yaw = np.clip(
                    np.dot(point(nose) - point(eyes), unit(across, [1, 0]))
                    / max(1, np.linalg.norm(across) * 0.62),
                    -1,
                    1,
                )
                face_score = min(eyes["score"], nose["score"])
        elif ears:
            head = anchor(point(ears), ears["score"], "ear_midpoint")
        elif observed(nose):
            head = anchor(point(nose), nose["score"], "nose_estimate")
    hd = actor.get("head_detection")
    if hd and not face:
        x1, y1, x2, y2 = hd["bbox"]
        center = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
        tracking = hd.get("kind")
        head = anchor(
            center, hd["score"], tracking + "_estimate" if tracking else "head_detection"
        )
        facial_scale = max(1.0, 0.5 * ((x2 - x1) + (y2 - y1))) / profile["head_diameter"]
        if actor.get("tracked_neck_axis") is not None:
            up = unit(actor["tracked_neck_axis"], up)
        region = actor.get("person_region")
        if region and not sh:
            px, py, px2, py2 = region["bbox"]
            delta = center - np.array([(px + px2) / 2, (py + py2) / 2])
            if np.linalg.norm(delta) > 0.12 * max(x2 - x1, y2 - y1):
                up = unit(delta, up)
    if actor.get("border_motion"):
        exit_motion = actor["border_motion"]
        head = anchor(exit_motion["head_uv"], 0.0, "partial_exit_flow_inferred")
        facial_scale = exit_motion["last_measured_face_width"] * 1.045 / profile["head_diameter"]
    shoulder_support = actor.get("shoulder_support")
    if shoulder_support:
        up = np.asarray(shoulder_support["up_uv"])
        face_score = 0.0
        sh = None
        hip = None
    scale_samples = []
    if sh and hip:
        scale_samples.append(np.linalg.norm(point(sh) - point(hip)) / profile["torso"])
    if sh:
        scale_samples.append(
            np.linalg.norm(point(j["left_shoulder"]) - point(j["right_shoulder"]))
            / profile["shoulder_width"]
        )
    if head and sh:
        offset = np.linalg.norm(point(head) - point(sh)) / profile["head_offset"]
        # Head tilt and perspective make this more variable than torso length.
        scale_samples.append(offset * 0.88)
    box = actor["bbox"]
    box_height = max(1.0, box[3] - box[1])
    fallback_scale = box_height / (
        profile["torso"] + profile["head_offset"] + profile["thigh"] + profile["shin"]
    )
    valid = [v for v in scale_samples if v > 1]
    if facial_scale:
        ppm = (
            facial_scale
            if not sh
            else np.median([facial_scale, facial_scale, max(valid or [facial_scale])])
        )
    else:
        ppm = max(valid or [fallback_scale])
    fit = actor.get("head_motion_fit")
    if fit:
        head = anchor(fit["head_uv"], head["score"], "source_motion_filtered")
        ppm = fit["pixels_per_meter"]
        yaw = fit.get("yaw_proxy", yaw)
    return {
        "head": head,
        "shoulder": sh,
        "hip": hip,
        "up": up,
        "yaw": float(yaw),
        "face_score": face_score,
        "ppm": float(max(1.0, ppm)),
    }


def stabilized_direction(projected, previous, rest, depth_sign, rate=0.64):
    dx, dz = projected
    extent = math.hypot(dx, dz)
    if extent > 1:
        dx, dz = dx / extent, dz / extent
    dy = depth_sign * math.sqrt(max(0.0, 1.0 - dx * dx - dz * dz))
    proposed = unit([dx, dy, dz], rest)
    if previous is None:
        return proposed
    return unit((1 - rate) * previous + rate * proposed, previous)


def stabilize_close_roll(rows, height):
    """Reject isolated four-way detector orientation aliases in single-subject close shots."""
    from collections import defaultdict

    by_shot = defaultdict(list)
    for r in rows:
        by_shot[r["shot_id"]].append(r)
    for part in by_shot.values():
        if np.mean([len(r["actors"]) <= 1 for r in part]) < 0.85:
            continue
        tagged = []
        for row in part:
            for actor in row["actors"]:
                face = actor.get("face")
                if not face or face["score"] < 0.72:
                    continue
                x1, y1, x2, y2 = face["bbox"]
                if max(x2 - x1, y2 - y1) < 0.18 * height:
                    continue
                re, le, _, rm, lm = np.asarray(face["landmarks"])
                vec = unit(re + le - rm - lm, [0, -1])
                tagged.append((row["source_frame"], vec, face["score"]))
        if len(tagged) < 3:
            continue
        global_roll = np.median(np.array([t[1] for t in tagged]), axis=0)
        if np.linalg.norm(global_roll) < 0.68:
            continue
        for r in part:
            local = [v for i, v, s in tagged if abs(i - r["source_frame"]) <= 9 and s >= 0.78]
            vec = np.median(local, axis=0) if len(local) >= 5 else global_roll
            if np.linalg.norm(vec) < 0.7:
                vec = global_roll
            for a in r["actors"]:
                a["shot_roll_support"] = unit(vec, global_roll).round(5).tolist()


def solve_track(entries, profile):
    measures = [measure(a, profile) for _, a in entries]
    directions = {}
    previous = None
    for idx, (frame_no, actor) in enumerate(entries):
        m = measures[idx]
        window = [
            t["ppm"]
            for (f, _), t in zip(
                entries[max(0, idx - 2) : idx + 3], measures[max(0, idx - 2) : idx + 3]
            )
            if abs(f - frame_no) <= 2
        ]
        ppm = float(np.median(window))
        if previous is not None and frame_no - previous > 4:
            directions = {}
        previous = frame_no
        joints = actor["joints2d"]
        head, shoulder, hip = (m[k] for k in ("head", "shoulder", "hip"))
        up = m["up"]
        # Reject brief upside-down face hypotheses when the same measured head
        # has a consistent roll on both sides. Keep genuine sustained turns.
        neigh = [
            mm["up"]
            for (f, _), mm in zip(entries, measures)
            if abs(f - frame_no) <= 7 and mm["face_score"] > 0.72
        ]
        if len(neigh) >= 3:
            consensus = np.median(neigh, axis=0)
            if np.linalg.norm(consensus) > 0.75 and (
                m["face_score"] < 0.78 or np.dot(up, consensus) < -0.2
            ):
                up = unit(consensus, up)
        if actor.get("shot_roll_support") is not None:
            supported = np.array(actor["shot_roll_support"])
            if np.dot(up, supported) < 0.40 and (
                m["face_score"] < 0.84 or np.dot(up, supported) < -0.2
            ):
                up = supported
        torso_up = unit(point(shoulder) - point(hip), up) if shoulder and hip else up
        if (
            actor.get("shot_roll_support") is not None
            and actor["bbox_kind"] != "person_detection"
            and np.dot(torso_up, up) < 0
        ):
            torso_up = up
        if not shoulder:
            if head:
                shoulder = anchor(
                    point(head) - torso_up * profile["head_offset"] * ppm,
                    0.0,
                    "anatomical_prior_from_head",
                )
            elif hip:
                shoulder = anchor(
                    point(hip) + torso_up * profile["torso"] * ppm,
                    0.0,
                    "anatomical_prior_from_hip",
                )
            else:
                box = actor["bbox"]
                shoulder = anchor(
                    [(box[0] + box[2]) / 2, box[1] + 0.25 * (box[3] - box[1])], 0.0, "bbox_prior"
                )
        if not hip:
            hip = anchor(
                point(shoulder) - torso_up * ppm * profile["torso"],
                0.0,
                "anatomical_prior_from_shoulder",
            )
        if not head:
            head = anchor(
                point(shoulder) + torso_up * ppm * profile["head_offset"],
                0.0,
                "anatomical_prior_from_shoulder",
            )
        # Keep an orthonormal basis so the two torso sockets stay rigid together.
        vy = unit([torso_up[0], 0.0, -torso_up[1]], [0, 0, 1])
        across = directions.get("shoulder_axis", np.array([1.0, 0.0, 0.0]))
        if m["shoulder"]:
            delta = (point(joints["left_shoulder"]) - point(joints["right_shoulder"])) / (
                profile["shoulder_width"] * ppm
            )
            # Nose offset is evidence for lateral facing. Its depth magnitude is a prior.
            prior_depth = across[1]
            sign = np.sign(m["yaw"]) if abs(m["yaw"]) > 0.16 else (np.sign(prior_depth) or 1.0)
            across = stabilized_direction(
                [delta[0], -delta[1]], directions.get("shoulder_axis"), across, sign
            )
        elif m["face_score"] or (actor.get("occlusion_branch") or {}).get("yaw_proxy") is not None:
            side = unit([-up[1], up[0]], [1, 0])
            yaw = m["yaw"] * 0.78
            across = unit(
                [side[0] * math.sqrt(1 - yaw * yaw), yaw, -side[1] * math.sqrt(1 - yaw * yaw)],
                across,
            )
        support = actor.get("shoulder_support")
        if support:
            # Head and shoulder regions support the projected torso axis. The
            # anatomical side and out-of-plane pitch remain single-view priors.
            across = unit([-vy[2], 0.0, vy[0]], [-1, 0, 0])
        across = unit(across - vy * np.dot(across, vy), [1, 0, 0])
        directions["shoulder_axis"] = across
        forward = unit(np.cross(across, vy), [0, -1, 0])
        torso = vy * profile["torso"]
        pos = {"hip": np.zeros(3), "shoulder": torso, "neck": torso + vy * profile["neck"]}
        head_delta = (point(head) - point(shoulder)) / (ppm * profile["head_offset"])
        head_dir = stabilized_direction(
            [head_delta[0], -head_delta[1]], directions.get("head_dir"), vy, -1.0, 0.70
        )
        if head["score"] == 0:
            head_dir = vy
        directions["head_dir"] = head_dir
        pos["head"] = torso + head_dir * profile["head_offset"]
        states = {}
        for side, sgn in (("left", 1.0), ("right", -1.0)):
            pos[side + "_shoulder"] = torso + sgn * across * profile["shoulder_width"] / 2
            pos[side + "_hip"] = sgn * across * profile["hip_width"] / 2
            for parent, child, length, anterior in (
                ("shoulder", "elbow", "arm", -0.08),
                ("elbow", "wrist", "forearm", 0.35),
                ("hip", "knee", "thigh", 0.18),
                ("knee", "ankle", "shin", -0.15),
            ):
                a, b = side + "_" + parent, side + "_" + child
                default = unit(
                    -vy + forward * anterior + sgn * across * (0.18 if child == "elbow" else 0.06),
                    [0, 0, -1],
                )
                key = side + "_" + child
                prev = directions.get(key)
                inferred = all(joints[k]["visibility"] == "inferred" for k in (a, b))
                posture = actor.get("posture_priors")
                measured = observed(joints[a]) and observed(joints[b])
                weak_bent = (
                    posture is not None and min(joints[a]["score"], joints[b]["score"]) < 0.58
                )
                if posture and (not measured or weak_bent):
                    gravity = np.array(posture["gravity_xyz"])
                    side_drift = sgn * across * (0.11 if child in ("elbow", "knee") else 0.03)
                    axis = unit(
                        gravity
                        + side_drift
                        + forward * (0.10 if child in ("wrist", "knee") else -0.04),
                        gravity,
                    )
                    hint = posture["sleeves"].get(key)
                    if hint:
                        ux, uy = hint["uv"]
                        projected = unit([ux, axis[1], -uy], axis)
                        weight = 0.82 if child == "elbow" else 0.74
                        axis = unit((1 - weight) * axis + weight * projected, axis)
                    if prev is not None:
                        axis = unit(0.26 * prev + 0.74 * axis, axis)
                    state = (
                        "nearby_sleeve_direction_inferred"
                        if hint
                        else "gravity_direction_inferred"
                    )
                elif inferred or measured:
                    delta = (point(joints[b]) - point(joints[a])) / (ppm * profile[length])
                    # A monocular segment has two depth branches. Choose a conservative
                    # arm-forward/knee-forward prior, preserving the branch during a track.
                    sign = (
                        np.sign(prev[1])
                        if prev is not None and abs(prev[1]) > 0.15
                        else np.sign(default[1]) or -1
                    )
                    axis = stabilized_direction([delta[0], -delta[1]], prev, default, sign)
                    state = (
                        "bounded_person_flow_depth_prior"
                        if inferred
                        else "2d_estimate_depth_prior"
                    )
                else:
                    axis = prev if prev is not None else default
                    state = "held_direction" if prev is not None else "anatomical_prior"
                directions[key] = axis
                pos[b] = pos[a] + axis * profile[length]
                states[key] = state
        root_screen = point(hip).copy()
        if head["state"] not in ("anatomical_prior_from_shoulder", "bbox_prior"):
            # The head is the most stable measured attachment in a cropped frame.
            # Refit translation after fixed metric lengths rather than moving the
            # visual head to absorb differences in template and performer proportions.
            root_screen = point(head) - np.array([pos["head"][0], -pos["head"][2]]) * ppm
        head_fwd = None
        if support:
            # The projected head-to-torso direction measures how the bow
            # changes through the shot. Out-of-plane pitch is an interval prior.
            downward = np.clip(
                50.0 + 1.20 * np.degrees(np.arcsin(np.clip(support["up_uv"][1], -1.0, 1.0))),
                25.0,
                80.0,
            )
            pitch = np.radians(downward)
            head_fwd = [0.0, float(np.cos(pitch)), -float(np.sin(pitch))]
            support["pitch_deg_inferred"] = float(downward)
            support["pitch_interval_deg"] = [
                max(0.0, float(downward) - 17),
                min(90.0, float(downward) + 17),
            ]
        actor.update(
            anchors={"head": head, "shoulder": shoulder, "hip": hip},
            root_uv=root_screen.round(3).tolist(),
            pixels_per_meter=round(ppm, 5),
            joints3d={k: v.round(6).tolist() for k, v in pos.items()},
            joint3d_state=states,
            orientation={
                "face_up_uv": up.round(5).tolist(),
                "face_yaw_proxy": m["yaw"],
                "face_score": m["face_score"],
                "shoulder_axis_xyz": across.round(5).tolist(),
                "forward_xyz": forward.round(5).tolist(),
                "torso_up_xyz": vy.round(5).tolist(),
                "head_forward_xyz": head_fwd,
                "facing_state": "back_or_down_inferred"
                if support
                else ("face_landmarks" if m["face_score"] > 0.6 else "uncertain"),
                "facing_evidence": support["evidence"] if support else None,
                "state": "monocular_estimate"
                if m["face_score"] or m["shoulder"]
                else "anatomical_prior",
            },
        )
