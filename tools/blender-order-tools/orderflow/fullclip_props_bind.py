"""Build editable visible contour meshes at source-derived locations and in-plane angles."""

import math


def outline_points(polygon, axes, center, angle, count):
    """Resample a consistently wound perimeter with its first point near the major-axis tip."""
    import numpy as np

    points = np.asarray(polygon, dtype=float) - center
    c, s = math.cos(angle), math.sin(angle)
    local = points @ np.array([[c, -s], [s, c]]) / axes
    local[:, 1] *= -1
    signed = np.sum(
        local[:, 0] * np.roll(local[:, 1], -1) - local[:, 1] * np.roll(local[:, 0], -1)
    )
    if signed < 0:
        local = local[::-1]
    start = np.argmax(local[:, 0] - 0.04 * np.abs(local[:, 1]))
    local = np.concatenate((local[start:], local[:start], local[start : start + 1]))
    arc = np.r_[0, np.cumsum(np.linalg.norm(np.diff(local, axis=0), axis=1))]
    lengths = np.linspace(0, arc[-1], count, endpoint=False)
    return np.stack([np.interp(lengths, arc, local[:, i]) for i in (0, 1)], axis=1)


def apply_interpolation(block):
    action = block.animation_data.action if block.animation_data else None
    if action is None:
        return
    curves = getattr(action, "fcurves", None)
    if curves is None:
        curves = [
            c
            for layer in action.layers
            for strip in layer.strips
            for bag in strip.channelbags
            for c in bag.fcurves
        ]
    for curve in curves:
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT" if curve.data_path.startswith("hide_") else "LINEAR"


def bind(scene, camera, data, binding_info):
    import bpy
    import numpy as np
    from mathutils import Quaternion, Vector

    w, h = data["width"], data["height"]
    rigs = {}
    for name in binding_info["editable_tracks"].values():
        rig = bpy.data.objects[name]
        rigs[str(rig["shot_id"]), str(rig["track_id"])] = rig
    report = {
        "objects": [],
        "bound_samples": 0,
        "unbound_samples": 0,
        "multi_hand_samples": 0,
        "surface_depth_samples": 0,
        "inconsistent_contact_depth_samples": 0,
        "max_contact_depth_spread_scene_units": 0.0,
        "uncertain_contact_depth_frames": [],
    }
    initial = scene.frame_current
    try:
        for track in data["tracks"]:
            frames = []
            for sample in track["samples"]:
                i = sample["frame"]
                scene.frame_set(i + 1)
                deps = bpy.context.evaluated_depsgraph_get()
                cam = camera.evaluated_get(deps)
                camera_matrix = cam.matrix_world.normalized()
                inverse = camera_matrix.inverted()
                anchors = []
                for contact in sample["contacts"]:
                    rig = rigs.get((str(track["shot_id"]), contact["track_id"]))
                    if rig is None:
                        continue
                    evaluated = rig.evaluated_get(deps)
                    bone = evaluated.pose.bones.get(
                        "DEF-forearm." + ("L" if contact["side"] == "left" else "R")
                    )
                    if bone:
                        wrist = evaluated.matrix_world @ bone.tail
                        elbow = evaluated.matrix_world @ bone.head
                        d = -(inverse @ wrist).z
                        if cam.data.clip_start < d < cam.data.clip_end:
                            anchors.append((d, (elbow - wrist).length, rig.name))
                if not anchors:
                    report["unbound_samples"] += 1
                    continue
                depth, length, _ = min(anchors)
                spread = max(a[0] for a in anchors) - depth
                report["max_contact_depth_spread_scene_units"] = max(
                    report["max_contact_depth_spread_scene_units"], spread
                )
                if spread > max(a[1] for a in anchors):
                    report["inconsistent_contact_depth_samples"] += 1
                    report["uncertain_contact_depth_frames"].append(
                        dict(
                            prop_id=track["id"],
                            source_frame=i,
                            depth_spread=round(spread, 5),
                            state="unknown",
                        )
                    )
                projection = cam.calc_matrix_camera(
                    deps,
                    x=w,
                    y=h,
                    scale_x=scene.render.pixel_aspect_x,
                    scale_y=scene.render.pixel_aspect_y,
                )
                if cam.data.type not in ("PERSP", "ORTHO"):
                    raise ValueError("Prop binding requires a perspective or orthographic camera")
                unproject = projection.inverted()

                def to_world(u, v, at_depth=None):
                    z = depth if at_depth is None else at_depth
                    clip = projection @ Vector((0, 0, -z, 1))
                    p = unproject @ Vector(
                        ((2 * u / w - 1) * clip.w, (1 - 2 * v / h) * clip.w, clip.z, clip.w)
                    )
                    return camera_matrix @ (p.to_3d() / p.w)

                # A surviving source contour is visible. Resolve any contact-actor surface
                # covering its pixels at the wrist's depth, without pulling it past the set
                # or an unrelated actor. Only inspect surfaces within a forearm's reach.
                owners = {a[2] for a in anchors}
                screen = np.asarray(sample["polygon_uv"], float)
                tests = np.vstack(
                    (sample["center_uv"], screen, (screen + sample["center_uv"]) * 0.5)
                )
                surface = depth
                for u, v in tests:
                    start = to_world(u, v, cam.data.clip_start)
                    direction = (to_world(u, v, depth) - start).normalized()
                    hit, position, _, _, occluder, _ = scene.ray_cast(deps, start, direction)
                    if not hit or not occluder.parent or occluder.parent.name not in owners:
                        continue
                    z = -(inverse @ position).z
                    if 0 <= depth - z < 1.35 * length:
                        surface = min(surface, z - 0.012 * length)
                if surface < depth and surface > cam.data.clip_start:
                    depth = surface
                    report["surface_depth_samples"] += 1
                cx, cy = sample["center_uv"]
                angle = math.radians(sample["angle_deg"])
                if frames:
                    prev = frames[-1][-1]
                    angle += math.pi * round((prev - angle) / math.pi)
                center = to_world(cx, cy)
                long, short = sample["axes_px"]
                major = to_world(cx + long * math.cos(angle), cy + long * math.sin(angle)) - center
                minor = (
                    to_world(cx - short * math.sin(angle), cy + short * math.cos(angle)) - center
                )
                # Camera's +Y points screen-up and positive source angle is clockwise.
                rotation = camera_matrix.to_quaternion() @ Quaternion((0, 0, 1), -angle)
                outline = outline_points(
                    sample["polygon_uv"],
                    np.asarray([long, short]),
                    np.asarray([cx, cy]),
                    angle,
                    32,
                )
                frames.append(
                    (i, center, rotation, (major.length, minor.length, 1), outline, angle)
                )
                report["multi_hand_samples"] += len(sample["contacts"]) > 1
            if not frames:
                continue
            mesh = bpy.data.meshes.new(track["id"] + " visible contour")
            # Use fixed wall thickness estimated from the least-occluded minor span.
            # The visible face keeps the measured boundary; the sides remain editable.
            thickness = float(np.percentile([r[3][1] for r in frames], 65)) * 0.18
            ring = frames[0][4]
            count = len(ring)
            vertices = [(float(x), float(y), z) for z in (0.0, thickness) for x, y in ring]
            faces = [tuple(reversed(range(count))), tuple(range(count, 2 * count))]
            faces.extend(
                (i, (i + 1) % count, (i + 1) % count + count, i + count) for i in range(count)
            )
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            obj = bpy.data.objects.new("Observed " + track["id"], mesh)
            scene.collection.objects.link(obj)
            obj.rotation_mode = "QUATERNION"
            obj["source_sha256"] = data["source_sha256"]
            obj["shot_id"] = str(track["shot_id"])
            obj["geometry_basis"] = (
                "solid outline proxy; hidden thickness estimated from visible minor span"
            )
            obj["estimated_thickness_m"] = thickness
            mat = bpy.data.materials.new(obj.name + " measured color")
            color = np.median([s["rgb"] for s in track["samples"]], axis=0) / 255.0
            linear = np.where(color <= 0.04045, color / 12.92, ((color + 0.055) / 1.055) ** 2.4)
            # Matte white-model albedo keeps a pale held object legible against the hand.
            pale = np.max(color) - np.min(color) < 0.28 and np.mean(color) > 0.49
            preview_color = 0.45 + 0.55 * linear if pale else linear
            mat.diffuse_color = (*preview_color, 1.0)
            mat.use_nodes = True
            mat.node_tree.nodes.get("Principled BSDF").inputs[
                "Base Color"
            ].default_value = mat.diffuse_color
            mesh.materials.append(mat)
            visible = {r[0] + 1 for r in frames}
            keys = {1: 1 not in visible}
            for f in visible:
                if f - 1 not in visible:
                    keys[f] = False
                if f + 1 not in visible:
                    keys[f + 1] = True
            for f, hidden in sorted(keys.items()):
                for attr in ("hide_render", "hide_viewport"):
                    setattr(obj, attr, hidden)
                    obj.keyframe_insert(data_path=attr, frame=f)
            last = None
            for i, center, rotation, scale, outline, _ in frames:
                if last:
                    rotation.make_compatible(last)
                last = rotation.copy()
                obj.location = center
                obj.rotation_quaternion = rotation
                obj.scale = scale
                for attr in ("location", "rotation_quaternion", "scale"):
                    obj.keyframe_insert(data_path=attr, frame=i + 1)
                for n, vertex in enumerate(mesh.vertices):
                    xy = outline[n % count]
                    vertex.co = (*xy, thickness if n >= count else 0.0)
                    vertex.keyframe_insert(data_path="co", frame=i + 1)
            apply_interpolation(obj)
            apply_interpolation(mesh)
            report["objects"].append(obj.name)
            report["bound_samples"] += len(frames)
    finally:
        scene.frame_set(initial)
    return report
