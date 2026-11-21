"""Bind measured rail evidence to the evaluated scene; preserve the static world transform."""

import math
import numpy as np


def sample_scene(scene, camera, rows, width, height, actors):
    import bpy
    from mathutils import Vector

    samples = []
    pixel = np.array([[width / 2, 0, 0, width / 2], [0, -height / 2, 0, height / 2], [0, 0, 0, 1]])
    for r in rows:
        scene.frame_set(r["frame"] + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        cam = camera.evaluated_get(deps)
        if cam.data.type != "PERSP":
            return []
        mw = cam.matrix_world.normalized()
        inverse = mw.inverted()
        projection = cam.calc_matrix_camera(
            deps,
            x=width,
            y=height,
            scale_x=scene.render.pixel_aspect_x,
            scale_y=scene.render.pixel_aspect_y,
        )
        unproject = projection.inverted()
        p = pixel @ np.asarray(projection @ inverse)
        plane = np.array([-r["slope"], 1, -r["intercept"]]) @ p
        plane /= np.linalg.norm(plane[:3])
        front = []
        for crossing in r["crossings"]:
            center = crossing["u"]
            halfwidth = crossing.get("core_halfwidth", max(6.0, r["thickness"] * 2))
            # A center ray can miss the gap between two legs. Sample the actor's
            # width and upper/lower edges of the source-supported rail silhouette.
            for u in center + np.linspace(-1.65, 1.65, 9) * halfwidth:
                if not r["x_min"] <= u <= r["x_max"]:
                    continue
                depth = []
                for v in (
                    r["slope"] * u + r["intercept"] + np.array([-0.65, 0, 0.65]) * r["thickness"]
                ):
                    q = unproject @ Vector((2 * u / width - 1, 1 - 2 * v / height, 0, 1))
                    ray = mw.to_quaternion() @ q.to_3d().normalized()
                    for mesh in actors.get((r["shot"], crossing["track_id"]), []):
                        ob = mesh.evaluated_get(deps)
                        if ob.hide_render:
                            continue
                        inv = ob.matrix_world.inverted()
                        hit, pos, _, _ = ob.ray_cast(
                            inv @ mw.translation, (inv.to_3x3() @ ray).normalized()
                        )
                        if hit:
                            z = -(inverse @ (ob.matrix_world @ pos)).z
                            if z > cam.data.clip_start:
                                depth.append(z)
                if depth:
                    front.append((u, min(depth)))
        samples.append(
            dict(
                row=r,
                plane=plane,
                projection=p,
                front=front,
                fx=projection[0][0] * width * 0.5,
            )
        )
    return samples


def duplicate(scene, result, shot):
    ends = result["ends"]
    radius = result["radius"]
    axis = ends[1] - ends[0]
    length = np.linalg.norm(axis)
    axis /= length
    for ob in scene.objects:
        if ob.type != "MESH" or ob.hide_render:
            continue
        label = str(ob.get("reconstruction_kind", "")).lower()
        if "rail" not in label or (ob.get("shot_id") is not None and ob.get("shot_id") != shot):
            continue
        if not 4 <= len(ob.data.vertices) <= 128:
            continue
        pts = np.asarray([tuple(ob.matrix_world @ v.co) for v in ob.data.vertices]) - ends[0]
        t = pts @ axis
        offset = np.linalg.norm(pts - t[:, None] * axis, axis=1)
        if (
            np.max(offset) < 2.7 * radius
            and np.ptp(t) > 0.93 * length
            and t.min() < length * 0.04
            and t.max() > 0.96 * length
        ):
            return ob.name


def make_member(scene, fit, shot, idx, first, last, source_sha):
    import bpy
    from mathutils import Vector

    a, b = map(Vector, fit["ends"])
    radius = fit["radius"]
    length = (b - a).length
    sides = 16
    xy = [
        (
            radius * math.cos(t * 2 * math.pi / sides),
            radius * math.sin(t * 2 * math.pi / sides),
        )
        for t in range(sides)
    ]
    vertices = [(x, y, z) for z in (-length / 2, length / 2) for x, y in xy]
    faces = [(i, (i + 1) % sides, sides + (i + 1) % sides, sides + i) for i in range(sides)]
    faces.extend([tuple(reversed(range(sides))), tuple(range(sides, 2 * sides))])
    label = f"Observed guardrail {idx + 1:02} • {shot}"
    mesh = bpy.data.meshes.new(label)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for face in list(mesh.polygons)[:sides]:
        face.use_smooth = True
    obj = bpy.data.objects.new(label, mesh)
    scene.collection.objects.link(obj)
    obj.location = (a + b) * 0.5
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (b - a).to_track_quat("Z", "Y")
    mat = bpy.data.materials.get("Observed rail • brushed metal") or bpy.data.materials.new(
        "Observed rail • brushed metal"
    )
    mat.diffuse_color = (0.28, 0.25, 0.22, 1)
    mat.metallic = 0.38
    mat.roughness = 0.47
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = mat.diffuse_color
    shader.inputs["Metallic"].default_value = mat.metallic
    shader.inputs["Roughness"].default_value = mat.roughness
    mesh.materials.append(mat)
    obj["observed_guardrail"] = True
    obj["reconstruction_kind"] = "Measured foreground guardrail"
    obj["shot_id"] = shot
    obj["source_sha256"] = source_sha
    obj["diameter_scene_units"] = 2 * radius
    obj["static_world_geometry"] = True
    keys = {1: True, first + 1: False, last + 2: True}
    for key, hidden in sorted(keys.items()):
        for field in ("hide_render", "hide_viewport"):
            setattr(obj, field, hidden)
            obj.keyframe_insert(data_path=field, frame=key)
    return obj.name


def actor_meshes(binding_info):
    import bpy

    actors = {}
    for name in binding_info["editable_tracks"].values():
        rig = bpy.data.objects[name]
        actors[str(rig["shot_id"]), str(rig["track_id"])] = [
            ob for ob in rig.children if ob.type == "MESH"
        ]
    return actors


def bind(scene, camera, data, binding_info):
    if __package__:
        from .fullclip_guardrail_fit import fit
        from .fullclip_guardrail_clearance import resolve
        from .fullclip_guardrail_panel_bind import bind_panel
    else:
        from fullclip_guardrail_fit import fit
        from fullclip_guardrail_clearance import resolve
        from fullclip_guardrail_panel_bind import bind_panel
    if camera.get("source_sha256", data["source_sha256"]) != data["source_sha256"]:
        raise ValueError("Guardrail and scene camera come from different sources")
    actors = actor_meshes(binding_info)
    initial = scene.frame_current
    result = dict(
        objects=[],
        fits=[],
        panels=[],
        reused=[],
        unresolved=[],
        source_sha256=data["source_sha256"],
    )
    try:
        for idx, track in enumerate(data["tracks"]):
            shot = track[0]["shot"]
            sample = sample_scene(scene, camera, track, data["width"], data["height"], actors)
            solved = fit(sample, data["width"], data["height"]) if len(sample) >= 4 else None
            if solved is None:
                result["unresolved"].append(
                    dict(
                        shot=shot,
                        first=track[0]["frame"],
                        last=track[-1]["frame"],
                        reason="insufficient rigid projection or body-depth support",
                    )
                )
                continue
            resolve(scene, track, actors, sample, solved, data["width"], data["height"])
            if solved["intersecting_actor_frames"]:
                result["unresolved"].append(
                    dict(
                        shot=shot,
                        reason="rigid member intersects evaluated actor mesh",
                        actor_frames=solved["intersecting_actor_frames"],
                    )
                )
            name = duplicate(scene, solved, shot)
            if name:
                result["reused"].append(name)
            else:
                name = make_member(
                    scene,
                    solved,
                    shot,
                    idx,
                    track[0]["frame"],
                    track[-1]["frame"],
                    data["source_sha256"],
                )
            result["objects"].append(name)
            result["fits"].append(
                dict(
                    **{k: v for k, v in solved.items() if k != "ends"},
                    end_points=solved["ends"].round(6).tolist(),
                    shot=shot,
                )
            )
            if any(r.get("panel_profile") for r in track):
                panel = bind_panel(
                    scene,
                    sample,
                    actors,
                    solved,
                    data["width"],
                    data["height"],
                    data["source_sha256"],
                    idx,
                )
                result["panels"].append(panel)
    finally:
        scene.frame_set(initial)
    return result
