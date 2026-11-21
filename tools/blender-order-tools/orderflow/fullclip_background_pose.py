"""Pose and place only shot-local, camera-motion-associated background fragments."""

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np


def _unit(v):
    return v / max(1e-8, np.linalg.norm(v))


def _joint(actor, key, score=0.26):
    point = actor["joints2d"].get(key, {})
    if (
        point.get("visibility") not in ("visible", "observed", "model_estimate")
        or point.get("score", 0) < score
    ):
        return None
    if point.get("u") is None or point.get("v") is None:
        return None
    return np.array([point["u"], point["v"]], float)


def _sample(actor):
    if actor["bbox_kind"] != "person_detection":
        return None
    box = np.array(actor["bbox"], float)
    size = box[2:] - box[:2]
    if min(size) < 10:
        return None
    values = {
        k: p
        for k in actor["joints2d"]
        if (p := _joint(actor, k)) is not None
        and np.all(p >= box[:2] - 0.05 * size)
        and np.all(p <= box[2:] + 0.05 * size)
    }

    def pair(name):
        a = [values[s + "_" + name] for s in ("left", "right") if s + "_" + name in values]
        return np.mean(a, axis=0) if a else None

    sh = pair("shoulder")
    hip = pair("hip")
    if sh is None or hip is None:
        return None
    delta = sh - hip
    span = np.linalg.norm(delta)
    if not 0.19 < span / np.linalg.norm(size) < 0.67:
        return None
    face = [values[n] for n in ("nose", "left_eye", "right_eye") if n in values]
    if not face:
        return None
    head = np.median(face, axis=0)
    # A lowered face beside the shoulders and the same-side reach distinguish a
    # bent profile from an upright seated pose viewed through another person.
    h = head - sh
    turned = abs(delta[0]) > 0.35 * span and delta[1] < 0.18 * span
    lowered = h[1] > 0.075 * span and h[0] * delta[0] > 0
    wrists = [values[s + "_wrist"] for s in ("left", "right") if s + "_wrist" in values]
    reaching = any(pt[1] > sh[1] + 0.24 * span for pt in wrists)
    if not (turned and lowered and reaching):
        return {"bent": False}
    # The model nose represents the visible face. The rigid proxy's head centre
    # lies above it, within the observed face-to-shoulder span.
    head = head - np.array([0.0, 0.12 * np.linalg.norm(h)])
    values.update(shoulder=sh, hip=hip, head=head)
    return {
        "bent": True,
        "uv": {k: (v - box[:2]) / size for k, v in values.items()},
        "scale": span,
        "direction": float(np.sign(delta[0])),
    }


def background_observations(sequence):
    """Gather frame-local upper-body observations only for a verified fragment association."""
    if not any(
        a.get("temporal_support", {}).get("state") == "inferred_from_background_motion"
        for _, a in sequence
    ):
        return {}
    raw = {i: r for i, a in sequence if (r := _sample(a)) is not None}
    bent = {i: r for i, r in raw.items() if r["bent"]}
    if len(bent) < 5 or len(bent) < 0.6 * len(raw):
        return {}
    offsets = defaultdict(list)
    for j, actor in sequence:
        if actor["bbox_kind"] != "person_detection":
            continue
        b = np.array(actor["bbox"])
        size = b[2:] - b[:2]
        for joint in ("hip", "shoulder"):
            left = _joint(actor, "left_" + joint)
            right = _joint(actor, "right_" + joint)
            if left is None or right is None or min(size) < 10:
                continue
            half = (right - left) / size * 0.5
            if np.linalg.norm(half) < 0.35:
                offsets[joint, "left"].append((j, half))
                offsets[joint, "right"].append((j, -half))
    results = {}
    for i, a in sequence:
        bounds = a.get("temporal_support", {}).get("source_frames", [])
        radius = max(9, min(32, abs(bounds[1] - bounds[0]) + 4)) if len(bounds) == 2 else 9
        near = [(j, r) for j, r in bent.items() if abs(i - j) <= radius]
        if len(near) < 2:
            continue
        # A known change to an upright pose stops the transfer of the bend.
        local = [r["bent"] for j, r in raw.items() if abs(j - i) <= 4]
        if len(local) >= 3 and np.mean(local) < 0.5:
            continue
        sign = np.median([r["direction"] for _, r in near])
        near = [(j, r) for j, r in near if r["direction"] == sign]
        if len(near) < 2:
            continue
        box = np.asarray(a["bbox"])
        wh = box[2:] - box[:2]
        uv = {}
        for k in {name for _, r in near for name in r["uv"]}:
            pts = [(j, r["uv"][k]) for j, r in near if k in r["uv"]]
            if len(pts) < 2:
                continue
            # Interpolate in box coordinates. The box itself follows camera
            # motion; a foreground occluder cannot drag a donor's old pixels along.
            med = np.median([v for _, v in pts], axis=0)
            pts = [(j, v) for j, v in pts if np.linalg.norm(med - v) < 0.20]
            if len(pts) < 2:
                continue
            rel = np.average(
                [v for _, v in pts],
                axis=0,
                weights=[1 / (2.5 + abs(j - i)) for j, _ in pts],
            )
            if i in bent and k in bent[i]["uv"]:
                direct = bent[i]["uv"][k]
                deviation = np.linalg.norm((direct - rel) * wh)
                # A current joint that agrees with its neighbours is a better
                # position anchor than an interpolated, partly occluded box.
                if deviation < 0.20 * np.linalg.norm(wh):
                    rel = 0.66 * direct + 0.34 * rel
            uv[k] = box[:2] + rel * wh
        if a["bbox_kind"] == "person_detection":
            for joint in ("hip", "shoulder"):
                valid = {
                    side: v
                    for side in ("left", "right")
                    if (v := _joint(a, side + "_" + joint)) is not None
                }
                if not valid or joint not in uv:
                    continue
                point = np.mean(list(valid.values()), axis=0)
                if len(valid) == 1:
                    side = next(iter(valid))
                    local_offsets = [d for j, d in offsets[joint, side] if abs(j - i) <= 24]
                    if len(local_offsets) < 2:
                        continue
                    point = point + np.median(local_offsets, axis=0) * wh
                if np.linalg.norm(point - uv[joint]) < 0.19 * np.linalg.norm(wh):
                    uv[joint] = 0.80 * point + 0.20 * uv[joint]
        if not all(n in uv for n in ("head", "hip", "shoulder")):
            continue
        results[i] = dict(
            uv=uv,
            bbox=box,
            source_state="direct_with_neighbor_support"
            if i in bent
            else "bounded_neighbor_inference",
        )
    return results


def _pose(actor, obs, parts, depth, focal, unit):
    from .fullclip_binding import local_pose

    uv = obs["uv"]
    up_uv = uv["shoulder"] - uv["hip"]
    up = _unit(np.array([up_uv[0], 0, -up_uv[1]]))
    normal = np.array([up[2], 0, -up[0]])
    width = (
        np.linalg.norm(
            np.array(parts["DEF-upper_arm.L"]["proximal_endpoint"])
            - parts["DEF-upper_arm.R"]["proximal_endpoint"]
        )
        * unit
    )
    pair = uv.get("left_shoulder", uv["shoulder"]) - uv.get("right_shoulder", uv["shoulder"])
    signed_width = np.dot([pair[0], 0, -pair[1]], normal)
    fraction = np.clip(signed_width * depth / (focal * width), -0.85, 0.85)
    left = normal * fraction + np.array(
        [0.0, np.sign(up[0]) * math.sqrt(1 - fraction * fraction), 0.0]
    )
    joints = dict(actor["joints3d"])
    # Retain 3-D branch evidence for a measured head-down gesture, replacing only
    # its screen-plane components. A face drop and a low sleeve are both observed.
    head = uv["head"] - uv["shoulder"]
    head_direction = _unit(np.array([head[0], 0.0, -head[1]]))
    joints["head"] = (np.asarray(joints["shoulder"]) + head_direction * 0.3).tolist()
    # Measured sleeves and wrists can remain below the shoulder even when the
    # input's unconstrained elbow depth is ambiguous.
    for side in ("left", "right"):
        for chain in (("shoulder", "elbow", "wrist"), ("hip", "knee", "ankle")):
            for begin, end in zip(chain, chain[1:]):
                k = side + "_" + begin
                endpoint = side + "_" + end
                opposite = "right" if side == "left" else "left"
                opposite_begin = opposite + "_" + begin
                opposite_end = opposite + "_" + end
                if k in uv and endpoint in uv:
                    d = uv[endpoint] - uv[k]
                elif opposite_begin in uv and opposite_end in uv:
                    d = uv[opposite_end] - uv[opposite_begin]
                else:
                    # No distal pixels survive. Keep a supported, hanging limb
                    # below the proximal joint, avoiding an inherited horizontal
                    # neutral leg or an arm pointing toward a foreground face.
                    d = np.array([0.0, 10.0])
                base = np.array(actor["joints3d"][endpoint]) - actor["joints3d"][k]
                if np.linalg.norm(d) < 5:
                    continue
                side_depth = float(np.clip(_unit(base)[1], -0.65, 0.65))
                flat = _unit(np.array([d[0], 0, -d[1]]))
                along = flat * math.sqrt(1 - side_depth**2) + np.array([0, side_depth, 0])
                joints[endpoint] = (
                    np.asarray(joints[k]) + along * max(0.05, np.linalg.norm(base))
                ).tolist()
    # A 2-D eye-line roll is ambiguous for a bowed profile. Here the
    # corroborated head-to-shoulder displacement supplies the pitch direction.
    orientation = dict(actor.get("orientation", {}), face_up_uv=None, head_forward_xyz=None)
    oriented = dict(actor, joints3d=joints, orientation=orientation)
    return local_pose(oriented, parts, (up, left))


def _seat_depths(scene, cam, projection, obs, width, height):
    from mathutils import Vector
    import bpy

    deps = bpy.context.evaluated_depsgraph_get()
    camera_matrix = cam.matrix_world.normalized()
    inverse = camera_matrix.inverted()
    unproject = projection.inverted()
    box = obs["bbox"]
    extent = box[2:] - box[:2]
    rows = defaultdict(list)
    for x in np.linspace(-0.12, 1.12, 7):
        for y in (0.66, 0.80, 0.96, 1.06):
            u, v = box[:2] + [x, y] * extent
            point = unproject @ Vector((2 * u / width - 1, 1 - 2 * v / height, 0, 1))
            ray = camera_matrix.to_quaternion() @ (point.to_3d() / point.w).normalized()
            hit, world, _, _, obj, _ = scene.ray_cast(deps, camera_matrix.translation, ray)
            if not hit or "seat_row" not in obj:
                continue
            depth = -(inverse @ world).z
            if depth > 0:
                rows[str(obj["seat_row"])].append(depth)
    return {k: float(np.median(z)) for k, z in rows.items() if len(z) >= 2}


def _depth_track(frames, apparent, seat_depths, torso_m):
    prior = float(np.median(list(apparent.values())))
    candidates = defaultdict(list)
    for frame, rows in seat_depths.items():
        for row, z in rows.items():
            if 0.70 * prior < z < 1.35 * prior:
                candidates[row].append((frame, z))
    ranked = []
    for row, hits in candidates.items():
        if len(hits) < max(4, 0.15 * len(frames)):
            continue
        depth = float(np.median([z for _, z in hits]))
        score = abs(math.log(depth / prior)) + 0.13 * (1 - len(hits) / len(frames))
        ranked.append((score, row, hits))
    selected = min(ranked, key=lambda v: v[0]) if ranked else None
    result = {}
    for i in frames:
        local = [z for j, z in apparent.items() if abs(i - j) <= 10]
        measured = float(np.median(local)) if local else prior
        depth = 0.8 * prior + 0.2 * np.clip(measured, 0.75 * prior, 1.25 * prior)
        if selected:
            _, _, hits = selected
            window = [z for j, z in hits if abs(i - j) <= 12]
            row_z = np.median(window or [z for _, z in hits]) + torso_m * 0.4
            depth = 0.23 * depth + 0.77 * row_z
        result[i] = float(depth)
    return result, selected[1] if selected else None


def refine_background_pose(scene, camera, people, binding_info, component_contract):
    """Refit only supported background rigs after binding and before key reduction."""
    import bpy
    from mathutils import Matrix, Vector

    data = people if isinstance(people, dict) else json.loads(Path(people).read_bytes())
    spec = (
        component_contract
        if isinstance(component_contract, dict)
        else json.loads(Path(component_contract).read_bytes())
    )
    parts = {v["weighted_group"]: v for v in spec["components"]}
    by_track = defaultdict(list)
    for frame in data["frames"]:
        for actor in frame["actors"]:
            by_track[str(frame["shot_id"]), str(actor["track_id"])].append(
                (frame["source_frame"], actor)
            )
    torso_length = (
        np.linalg.norm(
            np.array(parts["DEF-upper_arm.L"]["proximal_endpoint"])
            + parts["DEF-upper_arm.R"]["proximal_endpoint"]
            - np.array(parts["DEF-thigh.L"]["proximal_endpoint"])
            - parts["DEF-thigh.R"]["proximal_endpoint"]
        )
        / 2
    )
    w, h = data["width"], data["height"]
    res = {}
    initial = scene.frame_current
    try:
        for name in binding_info["editable_tracks"].values():
            rig = bpy.data.objects.get(name)
            if rig is None:
                continue
            seq = by_track[str(rig["shot_id"]), str(rig["track_id"])]
            observations = background_observations(seq)
            if not observations:
                continue
            unit = rig.scale.x
            torso_m = torso_length * unit
            apparent = {}
            seat_depths = {}
            views = {}
            for i, obs in observations.items():
                scene.frame_set(i + 1)
                deps = bpy.context.evaluated_depsgraph_get()
                cam = camera.evaluated_get(deps)
                if cam.data.type != "PERSP":
                    continue
                proj = cam.calc_matrix_camera(
                    deps,
                    x=w,
                    y=h,
                    scale_x=scene.render.pixel_aspect_x,
                    scale_y=scene.render.pixel_aspect_y,
                )
                fx, fy = proj[0][0] * w / 2, proj[1][1] * h / 2
                cx, cy = (1 - proj[0][2]) * w / 2, (1 + proj[1][2]) * h / 2
                vis = obs["uv"]["shoulder"] - obs["uv"]["hip"]
                apparent[i] = torso_m / max(1e-5, np.linalg.norm(vis / [fx, fy]))
                seat_depths[i] = _seat_depths(scene, cam, proj, obs, w, h)
                views[i] = fx, fy, cx, cy, cam.matrix_world.normalized().copy()
            if not views:
                continue
            depths, row = _depth_track(list(views), apparent, seat_depths, torso_m)
            actors = dict(seq)
            previous = {}
            frames = []
            for i, (fx, fy, cx, cy, camera_matrix) in views.items():
                scene.frame_set(i + 1)
                obs = observations[i]
                z = depths[i]
                pose, lm = _pose(actors[i], obs, parts, z, fx, unit)
                rays = []
                weights = []
                for k, weight in (("hip", 1.0), ("shoulder", 1.2), ("head", 0.65)):
                    u, v = obs["uv"][k]
                    point = np.asarray(lm[k]) * unit
                    ray = np.array([(u - cx) / fx, 1, (cy - v) / fy])
                    rays.append(ray * (z + point[1]) - point)
                    weights.append(weight)
                root = np.average(rays, axis=0, weights=weights)
                rot = camera_matrix.to_quaternion().to_matrix() @ Matrix(
                    ((1, 0, 0), (0, 0, 1), (0, -1, 0))
                )
                rig.location = camera_matrix.translation + rot @ Vector(root)
                rig.keyframe_insert(data_path="location", frame=i + 1)
                # The calibrated rig has rigid weighted components. Update rotations
                # about their existing rest endpoints, with fixed scale at every key.
                matrices = {
                    n: Matrix.Translation(pos)
                    @ r.to_4x4()
                    @ Matrix.Translation(-rig.data.bones[n].head_local)
                    for n, (pos, r) in pose.items()
                }
                for n in pose:
                    pb = rig.pose.bones[n]
                    rest = pb.bone.matrix_local
                    desired = matrices[n] @ rest
                    local = (
                        rest.inverted() @ desired
                        if not pb.parent
                        else rest.inverted()
                        @ pb.parent.bone.matrix_local
                        @ (
                            (matrices[pb.parent.name] @ pb.parent.bone.matrix_local).inverted()
                            @ desired
                        )
                    )
                    pos, q, _ = local.decompose()
                    pb.location = pos
                    if n in previous:
                        q.make_compatible(previous[n])
                    previous[n] = q.copy()
                    pb.rotation_quaternion = q
                    for attr in ("location", "rotation_quaternion"):
                        pb.keyframe_insert(data_path=attr, frame=i + 1)
                frames.append(
                    dict(
                        frame=i,
                        depth_scene_units=round(z, 5),
                        depth_m=round(
                            z * binding_info.get("scale_m_per_template_unit", unit) / unit,
                            5,
                        ),
                        source_state=obs["source_state"],
                    )
                )
            res[str(rig["track_id"])] = dict(
                shot=str(rig["shot_id"]),
                frames=frames,
                seat_row=row,
                mechanism="camera_projected_body_span_and_observed_seat_row",
                absolute_position_observed=False,
            )
    finally:
        scene.frame_set(initial)
    return res
