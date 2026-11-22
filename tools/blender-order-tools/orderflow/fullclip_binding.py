"""Bind a rigid-component template to shot-local pose tracks in the actual scene camera.

The neutral triangle mesh and its weights come from the supplied asset. Each independently
weighted component is posed by a rigid transform around its measured mesh endpoint.
"""

import colorsys
from collections import defaultdict
from pathlib import Path
import json
import numpy as np
import bpy
from mathutils import Vector, Matrix


def norm(v, fallback):
    v = Vector(v)
    return v.normalized() if v.length > 1e-6 else Vector(fallback)


def basis(right, up):
    z = norm(up, (0, 0, 1))
    x = Vector(right) - z * Vector(right).dot(z)
    x = norm(x, (1, 0, 0))
    y = norm(z.cross(x), (0, 1, 0))
    x = y.cross(z)
    return Matrix((x, y, z)).transposed()


def srgb(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def make_rig(scene, source, parts, bucket, label, color, material_cache):
    mesh = source.copy()
    mesh.data = source.data.copy()
    mesh.name = label
    mesh.animation_data_clear()
    mesh.parent = None
    mesh.matrix_world = Matrix.Identity(4)
    for modifier in list(mesh.modifiers):
        mesh.modifiers.remove(modifier)
    bucket.objects.link(mesh)
    mesh.hide_viewport = mesh.hide_render = False
    mesh.hide_set(False)
    arm = bpy.data.armatures.new(label + " armature")
    rig = bpy.data.objects.new(label + " editable pose", arm)
    bucket.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for key, part in parts.items():
        bone = arm.edit_bones.new(key)
        bone.head = part["proximal_endpoint"]
        bone.tail = part["distal_endpoint"]
    torso = arm.edit_bones["DEF-spine"]
    for bone in arm.edit_bones:
        if bone == torso:
            continue
        if bone.name.startswith("DEF-forearm."):
            bone.parent = arm.edit_bones[bone.name.replace("forearm", "upper_arm")]
        elif bone.name.startswith("DEF-shin."):
            bone.parent = arm.edit_bones[bone.name.replace("shin", "thigh")]
        else:
            bone.parent = torso
        bone.use_connect = False
    arm.display_type = "STICK"
    rig.show_in_front = True
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.select_set(False)
    mesh.modifiers.new("Rigid component pose", "ARMATURE").object = rig
    mesh.parent = rig
    mesh.lock_scale = rig.lock_scale = (True, True, True)
    for bone in rig.pose.bones:
        bone.lock_scale = (True, True, True)
    marker_faces = {i for part in parts.values() for i in part["material_mark_polygon_indices"]}
    for index in range(len(mesh.data.materials)):
        faces = {face.index for face in mesh.data.polygons if face.material_index == index}
        if faces & marker_faces and not faces <= marker_faces:
            raise ValueError("Marker and body must use separate template material slots")
        marker = bool(faces & marker_faces)
        key = (color, marker)
        if key not in material_cache:
            from .palette import complement

            rgba = tuple(srgb(c) for c in color) + (1.0,)
            material = bpy.data.materials.new("Track color " + str(len(material_cache) + 1))
            material.diffuse_color = complement(rgba) if marker else rgba
            material.use_nodes = True
            material.node_tree.nodes.get("Principled BSDF").inputs[
                "Base Color"
            ].default_value = material.diffuse_color
            material_cache[key] = material
        material_cache[key]["orderflow_role"] = "marker" if marker else "body"
        mesh.data.materials[index] = material_cache[key]
    return rig, mesh


def local_pose(actor, parts, torso_axes=None):
    joints = {name: Vector(v) for name, v in actor["joints3d"].items() if v is not None}
    hip = (joints["left_hip"] + joints["right_hip"]) * 0.5
    shoulder = (joints["left_shoulder"] + joints["right_shoulder"]) * 0.5
    body = basis(joints["left_shoulder"] - joints["right_shoulder"], shoulder - hip)
    if torso_axes is not None:
        body = basis(torso_axes[1], torso_axes[0])
    prox = {name: Vector(p["proximal_endpoint"]) for name, p in parts.items()}
    distal = {name: Vector(p["distal_endpoint"]) for name, p in parts.items()}
    anchor = (prox["DEF-thigh.L"] + prox["DEF-thigh.R"]) * 0.5
    posed = {name: (body @ (pos - anchor), body.copy()) for name, pos in prox.items()}
    landmarks = {}
    for side, end in [("left", ".L"), ("right", ".R")]:
        for names, chain in [
            (("DEF-upper_arm", "DEF-forearm"), ("shoulder", "elbow", "wrist")),
            (("DEF-thigh", "DEF-shin"), ("hip", "knee", "ankle")),
        ]:
            start = posed[names[0] + end][0]
            for idx, name in enumerate(names):
                key = name + end
                length = distal[key] - prox[key]
                a, b = (side + "_" + n for n in chain[idx : idx + 2])
                initial = body @ length
                along = norm(joints[b] - joints[a], initial.normalized())
                rot = initial.rotation_difference(along).to_matrix() @ body
                posed[key] = (start, rot)
                landmarks[a] = start
                start = start + rot @ length
                landmarks[b] = start
    head = "DEF-spine.006"
    neck, head_rot = posed[head]
    # Use the measured roll and facing proxy, including rotated close-ups. Missing face
    # evidence leaves a head aligned to the inferred neck direction.
    direction = norm(joints["head"] - joints["shoulder"], body.col[2])
    orientation = actor.get("orientation", {})
    face_up = orientation.get("face_up_uv")
    reverse_facing = orientation.get("facing_state") == "back_or_down_inferred"
    if reverse_facing and orientation.get("head_forward_xyz"):
        forward = norm(orientation["head_forward_xyz"], (0, 1, 0))
        up = (
            body.col[2]
            if torso_axes is not None
            else norm(orientation.get("torso_up_xyz") or direction, direction)
        )
        head_rot = basis(up.cross(forward), up)
    elif face_up and orientation.get("face_score", 0) >= 0.35:
        roll = norm((face_up[0], 0.0, -face_up[1]), direction)
        left_axis = norm((roll.z, 0.0, -roll.x), body.col[0])
        yaw = max(-0.90, min(0.90, float(orientation.get("face_yaw_proxy", 0))))
        left_axis = left_axis * (1 - yaw**2) ** 0.5 + Vector((0, yaw, 0))
        head_rot = basis(left_axis, roll)
    else:
        head_rot = basis(body.col[0], direction)
    posed[head] = (neck, head_rot)
    headcenter = neck + head_rot @ ((distal[head] - prox[head]) * 0.5)
    landmarks["head"] = headcenter
    landmarks["shoulder"] = (landmarks["left_shoulder"] + landmarks["right_shoulder"]) * 0.5
    landmarks["hip"] = (landmarks["left_hip"] + landmarks["right_hip"]) * 0.5
    local = {
        "nose": (0, -0.13, 0.005),
        "left_eye": (0.055, -0.12, 0.026),
        "right_eye": (-0.055, -0.12, 0.026),
        "left_ear": (0.135, 0, 0.005),
        "right_ear": (-0.135, 0, 0.005),
    }
    if not reverse_facing:
        for name, ofs in local.items():
            landmarks[name] = headcenter + head_rot @ Vector(ofs)
    return posed, landmarks


def head_led(actor, image_height, parts):
    head = actor.get("anchors", {}).get("head") or {}
    if head.get("u") is None or head.get("v") is None:
        return False
    observed = (
        head.get("score", 0) > 0.25
        or head.get("state") == "source_motion_filtered"
        or actor.get("border_motion") is not None
    )
    if not observed:
        return False
    diameter = parts["DEF-spine.006"]["axis_length"] * actor["pixels_per_meter"]
    return diameter > 0.31 * image_height


def bridge_short_observations(sequence):
    """Interpolate armature-space anatomy only across confirmed, bounded optical-flow gaps."""
    result = {}
    i = 0
    while i < len(sequence):
        if sequence[i][1]["bbox_kind"] != "bounded_person_flow":
            i += 1
            continue
        start = i
        while i < len(sequence) and sequence[i][1]["bbox_kind"] == "bounded_person_flow":
            i += 1
        if start == 0 or i == len(sequence):
            continue
        lo, hi = start - 1, i
        f0, a = sequence[lo]
        f1, b = sequence[hi]
        for n in range(start, hi):
            f, current = sequence[n]
            t = (f - f0) / (f1 - f0)
            actor = dict(current)
            actor["joints3d"] = {
                k: np.asarray(a["joints3d"][k]) * (1 - t) + np.asarray(b["joints3d"][k]) * t
                for k in current["joints3d"]
                if a["joints3d"].get(k) is not None and b["joints3d"].get(k) is not None
            }
            ori = dict(current.get("orientation", {}))
            for key in (
                "face_up_uv",
                "face_score",
                "face_yaw_proxy",
                "head_forward_xyz",
                "torso_up_xyz",
            ):
                va = a["orientation"].get(key)
                vb = b["orientation"].get(key)
                if va is not None and vb is not None:
                    combined = np.asarray(va) * (1 - t) + np.asarray(vb) * t
                    ori[key] = combined.tolist()
            actor["orientation"] = ori
            result[n] = (lo, hi, t, actor)
    return result


def camera_root_locations(samples, bridges, backtracked=()):
    """Fit observed root positions first, then join those final positions across verified gaps."""
    fitted = []
    for n, (frame, _, _, _, loc, anchor_head) in enumerate(samples):
        if n not in bridges and not anchor_head:
            neighbors = [
                s[4].y
                for s in samples[max(0, n - 2) : n + 3]
                if abs(s[0] - frame) <= 2 and s[0] not in backtracked
            ]
            if len(neighbors) > 1:
                median = float(np.median(neighbors))
                if abs(median - loc.y) < 0.25 * loc.y:
                    loc = loc * (median / max(loc.y, 1e-6))
        fitted.append(loc)
    # Both ends must be exactly the camera-local positions that will be keyed.
    # Using the raw depths here can briefly send the rig behind a static wall.
    for n, (lo, hi, t, _) in bridges.items():
        fitted[n] = fitted[lo].lerp(fitted[hi], t)
    return fitted


def fit_position(actor, landmarks, data, projection, human_height, anchor_head):
    w, h = data["width"], data["height"]
    p = projection
    # The camera-local coordinate convention below is x right, y forward, z up.
    fx, fy = p[0][0] * w / 2, p[1][1] * h / 2
    cx, cy = (1 - p[0][2]) * w / 2, (1 + p[1][2]) * h / 2
    uv = actor["root_uv"]
    depth = max(0.1, fx / max(float(actor["pixels_per_meter"]), 1.0))
    init = np.array([(uv[0] - cx) / fx * depth, depth, (cy - uv[1]) / fy * depth])
    # In a head-dominant shot most of the body is inferred outside the frame.
    # Translate the *real posed head centre* to the observed camera ray.
    if anchor_head:
        head = actor["anchors"]["head"]
        ray = Vector(((head["u"] - cx) / fx, 1.0, (cy - head["v"]) / fy))
        return ray * depth - landmarks["head"]

    A = []
    B = []
    weights = []
    for key, pt in landmarks.items():
        obs = actor["joints2d"].get(key) or actor.get("anchors", {}).get(key)
        if (
            not obs
            or obs.get("visibility") in ("unknown", "missing", "occluded")
            or obs["score"] < 0.35
        ):
            continue
        u = (obs["u"] - cx) / fx
        v = (cy - obs["v"]) / fy
        x, y, z = pt
        A.extend([(1, -u, 0), (0, -v, 1)])
        B.extend([u * y - x, v * y - z])
        weight = float(obs["score"]) * (1.0 if key.endswith(("shoulder", "hip")) else 0.55)
        if key == "head":
            weight *= 2.5
        weights.extend([weight, weight])
    if len(A) < 4:
        return Vector(init)
    A, B, weights = map(np.asarray, (A, B, weights))
    # Regularize depth toward the person's own measured size. Robust fitting limits drifting
    # wrists and dropped ankle observations without changing any mesh dimension.
    prior = np.diag([0.01, 0.18, 0.01])
    state = init
    for _ in range(3):
        residual = np.abs(A @ state - B)
        wr = np.sqrt(weights * np.minimum(1.0, (0.05 * human_height) / np.maximum(residual, 1e-6)))
        state = np.linalg.lstsq(
            np.vstack([A * wr[:, None], prior]), np.r_[B * wr, prior @ init], rcond=None
        )[0]
    if state[1] <= 0.12 or not np.all(np.isfinite(state)):
        return Vector(init)
    return Vector(state)


def bind(
    scene,
    camera,
    data,
    template,
    contract,
    height_m=1.75,
    meters_per_unit=1.0,
    manifest=None,
    body_sizes=None,
):
    from . import core

    spec = json.loads(Path(contract).read_bytes())
    if core.sha256(template) != spec["asset_sha256"]:
        raise ValueError("Template and component profile mismatch")
    parts = {part["weighted_group"]: part for part in spec["components"]}
    with bpy.data.libraries.load(str(template), link=False) as (available, selected):
        eligible = [n for n in available.collections if n.startswith("ClientCharacter_")]
        if len(eligible) != 1:
            raise ValueError(
                "Specify a library containing one calibrated client character collection"
            )
        selected.collections = eligible
    origin = selected.collections[0]
    scene.collection.children.link(origin)
    origin.hide_render = origin.hide_viewport = True
    models = [
        o for o in origin.objects if o.type == "MESH" and all(k in o.vertex_groups for k in parts)
    ]
    if len(models) != 1:
        raise ValueError("The supplied collection has no unique profiled render mesh")
    from .fullclip_body_size import (
        neutral_head_offsets,
        posed_head_vertices,
        adjust_body_depth,
        interaction_torso_axes,
    )

    mesh = models[0]
    head_offsets = neutral_head_offsets(mesh, parts)
    size_evidence = {}
    template_height = max(v.co.z for v in mesh.data.vertices) - min(
        v.co.z for v in mesh.data.vertices
    )
    unit_scale = height_m / template_height
    colors = (
        (0.80, 0.19, 0.18),
        (0.15, 0.45, 0.82),
        (0.13, 0.63, 0.40),
        (0.70, 0.42, 0.65),
        (0.59, 0.33, 0.71),
        (0.15, 0.64, 0.67),
    )
    tracks = {}
    material_cache = {}
    rows = defaultdict(list)
    for i, frame in enumerate(data["frames"]):
        for actor in frame["actors"]:
            if actor.get("renderable", True):
                rows[(frame["shot_id"], actor["track_id"])].append((i, actor))
    palette_ids = {}
    for index, (identity, seq) in enumerate(rows.items()):
        color_key = seq[0][1].get("color_key") or identity
        if color_key == "neutral-close":
            # A shared unverified close-up palette does not establish a named identity.
            color = (0.90, 0.67, 0.22)
        else:
            color_index = palette_ids.setdefault(color_key, len(palette_ids))
            if color_index < len(colors):
                color = colors[color_index]
            else:
                hue = (0.19 + 0.61803398875 * color_index) % 1.0
                color = colorsys.hsv_to_rgb(hue, 0.37, 0.76)

        collection = bpy.data.collections.new("Track " + str(identity[1]))
        scene.collection.children.link(collection)
        rig, me = make_rig(
            scene,
            mesh,
            parts,
            collection,
            "Track " + str(index + 1),
            color,
            material_cache,
        )
        rig["palette_key"] = str(color_key)
        rig["shot_id"] = str(identity[0])
        rig["track_id"] = str(identity[1])
        tracks[index] = rig.name
        visible_frames = {i for i, _ in seq}
        flags = {1: True}
        for i in sorted(visible_frames):
            if i - 1 not in visible_frames:
                flags[i + 1] = False
            if i + 1 not in visible_frames:
                flags[i + 2] = True
        for f, hide in sorted(flags.items()):
            for prop in ("hide_render", "hide_viewport"):
                for obj in (me, rig):
                    setattr(obj, prop, hide)
                    obj.keyframe_insert(data_path=prop, frame=f)
        rig.scale = (unit_scale / meters_per_unit,) * 3
        samples = []
        intrinsics = []
        bridges = bridge_short_observations(seq)
        from .fullclip_torso_depth import torso_track
        from .fullclip_scene import _shot

        torso_axes = (
            torso_track(_shot(manifest, seq[0][0]), seq, data["width"], data["height"])
            if manifest is not None
            else {}
        )
        from .fullclip_close_shoulders import refine as refine_close_shoulders

        if torso_axes:
            torso_axes, shoulder_evidence = refine_close_shoulders(
                seq,
                torso_axes,
                parts,
                _shot(manifest, seq[0][0]),
                data["width"],
                data["height"],
                mesh,
            )
            if shoulder_evidence:
                rig["shoulder_fit_evidence"] = json.dumps(shoulder_evidence)
        depth_fits = {}
        size_evidence[rig.name] = depth_fits
        for sample_index, (i, actor) in enumerate(seq):
            if sample_index in bridges:
                actor = bridges[sample_index][3]
            scene.frame_set(i + 1)
            deps = bpy.context.evaluated_depsgraph_get()
            ce = camera.evaluated_get(deps)
            proj = ce.calc_matrix_camera(deps, x=data["width"], y=data["height"])
            intrinsics.append(
                (
                    proj[0][0] * data["width"] / 2,
                    proj[1][1] * data["height"] / 2,
                    (1 - proj[0][2]) * data["width"] / 2,
                    (1 + proj[1][2]) * data["height"] / 2,
                )
            )
            axes = torso_axes.get(i)
            observation = {}
            if body_sizes is not None:
                row = body_sizes["frames"][i]
                if row["source_frame"] != i or row["shot_id"] != identity[0]:
                    raise ValueError("Body-size measurement frame/shot mismatch")
                observation = row["actors"].get(str(actor["track_id"]), {})
                if axes is None:
                    axes = interaction_torso_axes(actor, observation, parts)
            pose, lm = local_pose(actor, parts, axes)
            anchor_head = head_led(actor, data["height"], parts)
            loc = fit_position(actor, lm, data, proj, template_height, anchor_head)
            if body_sizes is not None and not anchor_head and sample_index not in bridges:
                xyz, report = adjust_body_depth(
                    actor,
                    observation,
                    lm,
                    posed_head_vertices(head_offsets, pose),
                    loc,
                    proj,
                    data["width"],
                    data["height"],
                )
                loc = Vector(xyz)
                depth_fits[str(i)] = report
            # Fit is in template units; the rig's single fixed scale maps it to metres.
            rotation = ce.matrix_world.to_quaternion().to_matrix() @ Matrix(
                ((1, 0, 0), (0, 0, 1), (0, -1, 0))
            )
            samples.append(
                (i, pose, rotation, ce.matrix_world.translation.copy(), loc, anchor_head)
            )
        positions = camera_root_locations(
            samples, bridges, {i for i, a in seq if a["bbox_kind"] == "backtracked_clothing"}
        )
        lookup = {i: n for n, (i, _) in enumerate(seq)}
        for n, (_, actor) in enumerate(seq):
            if actor["bbox_kind"] != "backtracked_clothing":
                continue
            support = actor["temporal_support"]
            donor = lookup[support["source_frames"][1]]
            # Preserve the fixed rig size. The source-derived displacement is
            # a screen translation of the same body, with unknown depth.
            loc = positions[donor].copy()
            du, dv = support["offset_uv"]
            fx, fy, cx, cy = intrinsics[n]
            fx0, fy0, cx0, cy0 = intrinsics[donor]
            u, v = cx0 + fx0 * loc.x / loc.y, cy0 - fy0 * loc.z / loc.y
            loc.x = (u + du - cx) * loc.y / fx
            loc.z = (cy - v - dv) * loc.y / fy
            positions[n] = loc
        for (i, pose, rotation, origin_world, _, _), loc in zip(samples, positions):
            rig.location = origin_world + rotation @ (loc * rig.scale.x)
            rig.rotation_mode = "QUATERNION"
            old = rig.rotation_quaternion.copy()
            rig.rotation_quaternion = rotation.to_quaternion()
            rig.rotation_quaternion.make_compatible(old)
            for prop in ("location", "rotation_quaternion"):
                rig.keyframe_insert(data_path=prop, frame=i + 1)
            # The solver gives armature-space rigid poses. Store them relative to their
            # real parents so an animator can turn a shoulder and take the forearm with it.
            matrices = {}
            for name, (pos, rot) in pose.items():
                rest = rig.data.bones[name].matrix_local
                target = (rot @ rest.to_3x3()).to_4x4()
                target.translation = pos
                matrices[name] = target
            for name, target in matrices.items():
                pb = rig.pose.bones[name]
                rest = pb.bone.matrix_local
                if pb.parent:
                    delta = (
                        rest.inverted()
                        @ pb.parent.bone.matrix_local
                        @ matrices[pb.parent.name].inverted()
                        @ target
                    )
                else:
                    delta = rest.inverted() @ target
                old = pb.rotation_quaternion.copy()
                pb.location = delta.to_translation()
                pb.rotation_quaternion = delta.to_quaternion()
                pb.rotation_quaternion.make_compatible(old)
                for prop in ("location", "rotation_quaternion"):
                    pb.keyframe_insert(data_path=prop, frame=i + 1)
    return dict(
        editable_tracks=tracks, scale_m_per_template_unit=unit_scale, body_depth_fits=size_evidence
    )
