"""Fit the inferred legs of a partial track while locking the observed shoulder ray."""


def fit_plant(scene, camera, rig, mesh, track, width, height, view_for, rules):
    import bpy
    import numpy as np
    from mathutils import Vector
    from . import fullclip_contact_plant as plant
    from .fullclip_contact import SUPPORT, _mesh
    from .fullclip_lower_tier import _screen, _trace

    if not all("DEF-upper_arm." + s in rig.pose.bones for s in ("L", "R")):
        return None
    if any(not actor.get("partial_body_track") for _, actor in track):
        return None
    if set(rules) != {"L", "R"} or rig.parent:
        return None
    for _, actor in track:
        joints = actor.get("joints2d", {})
        if any(
            joints.get(side + "_" + part, {}).get("visibility")
            in ("visible", "observed")
            for side in ("left", "right")
            for part in ("knee", "ankle")
        ):
            return None
    scene.frame_set(track[0][0] + 1)
    deps = bpy.context.evaluated_depsgraph_get()
    solids = []
    support_groups = {}
    for obj in scene.objects:
        kind = obj.get("reconstruction_kind", "")
        if (
            obj.type != "MESH"
            or obj.hide_render
            or not (kind in SUPPORT or kind.startswith("Seat "))
        ):
            continue
        points, m = _mesh(obj, deps)
        eq = plant.solid_planes(points, [list(f.vertices) for f in m.polygons])
        if eq is None:
            continue
        solid = (obj.name, points.min(0), points.max(0), *eq)
        solids.append(solid)
        if kind in SUPPORT:
            key = (
                ("partition", round(float(points[:, 2].max()), 4))
                if obj.get("floor_partition")
                else ("object", obj.name)
            )
            support_groups.setdefault(key, []).append(solid)
    support = [
        (tuple(s[0] for s in patches), patches) for patches in support_groups.values()
    ]
    data = [[] for _ in rules]
    origins = []
    shoulders = []
    depth_limits = []
    roots = []
    quats = []
    for frame, actor in track:
        scene.frame_set(frame + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        vs, _ = _mesh(mesh, deps)
        shoulders.append(
            np.mean(
                [
                    np.array(
                        rig.matrix_world @ rig.pose.bones["DEF-upper_arm." + s].head
                    )
                    for s in rules
                ],
                axis=0,
            )
        )
        view = view_for(camera, deps, width, height)
        origins.append(view[0])
        box = np.asarray(actor["partial_body_track"].get("visible_bbox", []), float)
        visible_depths = []
        if box.shape == (4,):
            # The source silhouette supplies the visible upper band. A tolerance
            # for the model's front skin keeps ray tests from demanding its back
            # stand ahead of an observed seat edge.
            for x, y in (
                (0.40, 0.18),
                (0.60, 0.18),
                (0.38, 0.36),
                (0.60, 0.36),
                (0.50, 0.27),
            ):
                hit = _trace(scene, deps, box[:2] + (box[2:] - box[:2]) * [x, y], view)
                if hit and "seat_row" in hit[0]:
                    visible_depths.append(hit[1])
        if visible_depths:
            center_depth = _screen([shoulders[-1]], view)[1][0]
            relative = np.linalg.norm(shoulders[-1] - view[0]) / max(0.01, center_depth)
            depth_limits.append(
                (np.percentile(visible_depths, 35) + 0.055 - center_depth) * relative
            )
        else:
            depth_limits.append(np.inf)
        roots.append(np.array(rig.location))
        world = rig.matrix_world.to_quaternion()
        poses = []
        for i, side in enumerate(("L", "R")):
            names, indices, end, clip = rules[side]
            pb = [rig.pose.bones[n] for n in names]
            h, k = [np.array(rig.matrix_world @ b.head) for b in pb]
            data[i].append(
                (h, k, vs[end].mean(0), vs[indices[0]], vs[indices[1]], vs[end])
            )
            poses.append([world @ b.matrix.to_quaternion() for b in pb])
        quats.append(poses)
    data = [[np.stack(column) for column in zip(*leg)] for leg in data]
    center = np.median((data[0][0] + data[1][0]) * 0.5, axis=0)
    # Cull objects outside the entire camera-depth search and limb span.
    solids = [
        s for s in solids if np.all(center + 3.5 > s[1]) and np.all(center - 3.5 < s[2])
    ]
    if not np.any(np.abs(np.median(np.array(shoulders) - origins, axis=0)[:2]) > 0.01):
        return None
    solution = plant.plan(
        data,
        np.array(origins),
        np.array(shoulders),
        support,
        solids,
        depth_limits=np.array(depth_limits),
    )
    if solution is None:
        return None
    name, soles, poses = solution
    rig["orderflow_ground_contact_precision"] = True
    for i, (f, actor) in enumerate(track):
        scene.frame_set(f + 1)
        rig.location = roots[i] + poses[i, 0]
        rig.keyframe_insert(data_path="location", frame=f + 1)
        for si, s in enumerate(("L", "R")):
            hip, knee, tip = [a[i] for a in data[si][:3]]
            for j, (n, direction) in enumerate(
                zip(rules[s][0], (knee - hip, tip - knee))
            ):
                pose = rig.pose.bones[n]
                delta = Vector(direction).rotation_difference(
                    Vector(poses[i, 1 + si * 2 + j])
                )
                target = (
                    rig.matrix_world.to_quaternion().inverted()
                    @ delta
                    @ quats[i][si][j]
                )
                space = (
                    pose.parent.matrix
                    @ pose.parent.bone.matrix_local.inverted()
                    @ pose.bone.matrix_local
                )
                q = space.to_quaternion().inverted() @ target
                q.make_compatible(pose.rotation_quaternion)
                pose.rotation_quaternion = q
                pose.keyframe_insert(data_path="rotation_quaternion", frame=f + 1)
                bpy.context.view_layer.update()
    return {
        "state": "fixed_bilateral_tread",
        "support": name[0] if len(name) == 1 else list(name),
        "soles": soles.tolist(),
        "source_frames": len(track),
    }
