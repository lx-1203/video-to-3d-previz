"""Build Blender-native editable set meshes with their source-registered camera."""

import functools
import hashlib
import json
from pathlib import Path

import numpy as np


_CV_TO_WORLD = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)
_LOCAL_TO_CV = np.diag([1.0, -1.0, -1.0])


def _shot(document, source_frame):
    for shot in document["shots"]:
        if shot["first"] <= source_frame <= shot["last"]:
            return shot
    raise ValueError(f"source frame {source_frame} outside measured scene")


def world_from_image(manifest_data, source_frame, u, v, depth):
    """Back-project top-left normalized image coordinates at positive axial depth in metres."""
    if not np.isfinite([u, v, depth]).all() or depth <= 0:
        raise ValueError("finite image coordinates and positive camera depth required")
    shot = _shot(manifest_data, source_frame)
    k = np.asarray(shot["intrinsics"])
    cam = np.asarray(shot["camera_to_world"][str(source_frame)])
    local = np.array([(u - k[0, 2]) / k[0, 0], (v - k[1, 2]) / k[1, 1], 1]) * depth
    return tuple(
        _CV_TO_WORLD @ (cam[:3, :3] @ local + cam[:3, 3] * float(shot.get("world_scale", 1.0)))
    )


@functools.lru_cache(maxsize=8)
def _depth_samples(path, sha):
    path = Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError("depth sample archive hash mismatch")
    with np.load(path, allow_pickle=False) as cache:
        return cache["depth"], cache["frames"]


def camera_depth_hint(manifest_data, source_frame, u, v):
    """Axial depth near the same screen location in the nearest measured view; may be unknown."""
    shot = _shot(manifest_data, source_frame)
    if "depth_samples" not in shot:
        return None
    depths, ids = _depth_samples(shot["depth_samples"], shot["depth_sha256"])
    depth = depths[np.argmin(np.abs(ids - source_frame))]
    h, w = depth.shape
    x, y = round(u * (w - 1)), round(v * (h - 1))
    if not 0 <= x < w or not 0 <= y < h:
        return None
    patch = depth[max(0, y - 1) : y + 2, max(0, x - 1) : x + 2]
    values = patch[np.isfinite(patch) & (patch > 0)]
    return float(np.median(values)) * float(shot.get("world_scale", 1.0)) if len(values) else None


def _visible_during(obj, first, last, global_first, global_last):
    keys = {first: False}
    if first > global_first:
        keys[global_first] = True
        keys[first - 1] = True
    if last < global_last:
        keys[last + 1] = True
    for frame, hide in sorted(keys.items()):
        for attr in ("hide_viewport", "hide_render"):
            setattr(obj, attr, hide)
            obj.keyframe_insert(data_path=attr, frame=frame + 1, group="Shot visibility")


def build_scene(scene, manifest, shot_world_scale=None):
    """Create one calibrated active camera for all shots and editable meshes. Return camera."""
    import bpy
    from mathutils import Matrix

    document = (
        json.loads(Path(manifest).read_bytes()) if isinstance(manifest, (str, Path)) else manifest
    )
    if document.get("schema") != "orderflow.editable-set/1" or not document["shots"]:
        raise ValueError("unrecognised or empty set manifest")
    root = bpy.data.collections.new("OrderFlow • reconstructed sets")
    scene.collection.children.link(root)
    data = bpy.data.cameras.new("Source calibrated perspective")
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = 36
    data.clip_start = 0.005
    data.clip_end = 2000.0
    camera = bpy.data.objects.new("OrderFlow • source camera", data)
    root.objects.link(camera)
    camera.rotation_mode = "QUATERNION"
    scene.camera = camera
    palette = []
    for tone, value in enumerate([0.16, 0.24, 0.34, 0.47, 0.61, 0.74]):
        mat = bpy.data.materials.new(f"Set clay • tone {tone}")
        mat.diffuse_color = (value * 1.02, value, value * 0.97, 1)
        mat.roughness = 0.76
        palette.append(mat)
    global_first = document["shots"][0]["first"]
    global_last = document["shots"][-1]["last"]
    color_cache = {}
    previous = None
    for i, shot in enumerate(document["shots"]):
        first, last = shot["first"], shot["last"]
        scale = float(shot.get("world_scale", 1.0))
        if shot_world_scale is not None:
            if isinstance(shot_world_scale, dict):
                scale = float(shot_world_scale.get(first, shot_world_scale.get(str(first), scale)))
            else:
                scale = float(shot_world_scale[i])
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("positive finite shot scale required")
        shot["world_scale"] = scale
        collection = bpy.data.collections.new(f"Set {i + 1:02d} • source {first}–{last}")
        root.children.link(collection)
        geom = Path(shot["geometry_path"]).read_bytes()
        if hashlib.sha256(geom).hexdigest() != shot["geometry_sha256"]:
            raise ValueError("geometry hash mismatch")
        banks = {}
        for j, item in enumerate(json.loads(geom)["objects"]):
            name = f"{item['kind']} {j + 1:03d}"
            mesh = bpy.data.meshes.new(name)
            vertices = np.asarray(item["vertices"]) @ _CV_TO_WORLD.T * scale
            mesh.from_pydata(vertices.tolist(), [], item["faces"])
            mesh.update()
            obj = bpy.data.objects.new(name, mesh)
            destination = collection
            if "seat_row" in item:
                key = (item["source_frame"], item["seat_row"])
                if key not in banks:
                    bank = bpy.data.collections.new(
                        f"Seat row {key[1] + 1:02d} • observation {key[0]}"
                    )
                    collection.children.link(bank)
                    banks[key] = bank
                destination = banks[key]
                obj["seat_row"] = int(item["seat_row"])
                if "seat_index" in item:
                    obj["seat_index"] = int(item["seat_index"])
            destination.objects.link(obj)
            colors = item.get("palette") or [item.get("rgb")]
            for color in colors:
                mat = palette[item["tone"]]
                if color is not None:
                    rgb = tuple(round(float(v) * 150) / 150 for v in color)
                    if rgb not in color_cache:
                        shaded = bpy.data.materials.new("Set surface • measured colour")
                        shaded.diffuse_color = (*rgb, 1)
                        shaded.roughness = 0.82
                        color_cache[rgb] = shaded
                    mat = color_cache[rgb]
                mesh.materials.append(mat)
            if "face_material" in item:
                mesh.polygons.foreach_set("material_index", item["face_material"])
                mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
            if item.get("background_luminance_only"):
                obj["background_luminance_only"] = True
            if "aperture_side" in item:
                obj["aperture_side"] = item["aperture_side"]
            if "observation_status" in item:
                obj["observation_status"] = item["observation_status"]
            if item.get("shared_corner"):
                obj["shared_corner"] = True
            if "source_observations" in item:
                obj["source_observations"] = item["source_observations"]
            if item.get("occlusion_frames"):
                obj["occlusion_frames"] = item["occlusion_frames"]
            if "motion" in item:
                obj.rotation_mode = "QUATERNION"
                previous_rot = None
                for f, pose in item["motion"].items():
                    rot = Matrix(
                        (_CV_TO_WORLD @ np.asarray(pose["rotation"]) @ _CV_TO_WORLD.T).tolist()
                    ).to_quaternion()
                    if previous_rot is not None:
                        rot.make_compatible(previous_rot)
                    previous_rot = rot.copy()
                    obj.rotation_quaternion = rot
                    obj.location = tuple((_CV_TO_WORLD @ np.asarray(pose["center"])) * scale)
                    obj.keyframe_insert(data_path="location", frame=int(f) + 1)
                    obj.keyframe_insert(data_path="rotation_quaternion", frame=int(f) + 1)
                obj["motion_frames"] = [int(f) for f in item["motion"]]
            obj["source_frame"] = int(item["source_frame"])
            obj["reconstruction_kind"] = item["kind"]
            _visible_during(
                obj,
                item.get("visible_first", first),
                item.get("visible_last", last),
                global_first,
                global_last,
            )
        k = np.asarray(shot["intrinsics"])
        data.lens = float(k[0, 0] * data.sensor_width)
        data.shift_x = float(0.5 - k[0, 2])
        data.shift_y = float((k[1, 2] - 0.5) * k[0, 0] / k[1, 1])
        for field in ("lens", "shift_x", "shift_y"):
            data.keyframe_insert(data_path=field, frame=first + 1)
            data.keyframe_insert(data_path=field, frame=last + 1)
        for f in range(first, last + 1):
            m = np.asarray(shot["camera_to_world"][str(f)])
            rot = Matrix((_CV_TO_WORLD @ m[:3, :3] @ _LOCAL_TO_CV).tolist()).to_quaternion()
            if previous is not None:
                rot.make_compatible(previous)
            previous = rot.copy()
            camera.location = tuple((_CV_TO_WORLD @ m[:3, 3]) * scale)
            camera.rotation_quaternion = rot
            camera.keyframe_insert(data_path="location", frame=f + 1, group="Source camera")
            camera.keyframe_insert(
                data_path="rotation_quaternion", frame=f + 1, group="Source camera"
            )
    camera["source_sha256"] = document["source_sha256"]
    camera["metric_scale_verified"] = False
    scene.frame_set(scene.frame_current)
    return camera


def finalize_foreground(scene, camera, manifest, binding_info):
    """Set the uncertain depth of witnessed foreground gratings and inferred backing after binding.

    Each group is scaled once about its reconstruction camera. Individual bars keep
    their relative spacing and the observed view projection. Actors stay fixed.
    """
    import bpy
    from mathutils import Vector

    document = (
        json.loads(Path(manifest).read_bytes()) if isinstance(manifest, (str, Path)) else manifest
    )
    objects = [o for o in scene.objects if o.type == "MESH" and "reconstruction_kind" in o]
    actors = []
    for name in binding_info.get("editable_tracks", {}).values():
        rig = bpy.data.objects.get(name)
        if not rig:
            continue
        for mesh in rig.children:
            if mesh.type != "MESH":
                continue
            group = mesh.vertex_groups.get("DEF-spine.006")
            if not group:
                continue
            ids = [
                v.index
                for v in mesh.data.vertices
                if any(g.group == group.index and g.weight > 0.5 for g in v.groups)
            ]
            if ids:
                actors.append((mesh, np.array(ids, dtype=int)))
    audit = []
    saved = scene.frame_current
    try:
        for shot in document["shots"]:
            first, last = shot["first"], shot["last"]
            items = [obj for obj in objects if first <= obj["source_frame"] <= last]
            bars = [obj for obj in items if len(obj.get("occlusion_frames", [])) > 0]
            backing = [obj for obj in items if obj["reconstruction_kind"].startswith("Backing")]
            if not bars and not backing:
                continue
            witnesses = set(int(i) for obj in bars for i in obj["occlusion_frames"])
            frames = sorted(witnesses | set(range(first, last + 1, 4)) | {last})
            foreground_ratio = []
            background_ratio = []
            observed = []
            for f in frames:
                scene.frame_set(f + 1)
                dg = bpy.context.evaluated_depsgraph_get()
                inv = np.asarray(camera.matrix_world.inverted())
                actor_z = []
                face_bounds = []
                visible_body_depths = []
                k = np.array(shot["intrinsics"])

                def projected_bounds(camera_coords):
                    z = -camera_coords[:, 2]
                    if np.min(z) <= 0.005:
                        return None
                    uv = np.column_stack(
                        [
                            camera_coords[:, 0] / z * k[0, 0] + k[0, 2],
                            -camera_coords[:, 1] / z * k[1, 1] + k[1, 2],
                        ]
                    )
                    return uv.min(0), uv.max(0)

                for ob, ids in actors:
                    if ob.hide_render:
                        continue
                    evaluated = ob.evaluated_get(dg)
                    co = np.empty(len(evaluated.data.vertices) * 3, np.float32)
                    evaluated.data.vertices.foreach_get("co", co)
                    transform = inv @ np.asarray(evaluated.matrix_world)
                    body = co.reshape(-1, 3) @ transform[:3, :3].T + transform[:3, 3]
                    body_z = -body[:, 2]
                    bz = np.maximum(0.005, body_z)
                    bu = body[:, 0] / bz * k[0, 0] + k[0, 2]
                    bv = -body[:, 1] / bz * k[1, 1] + k[1, 2]
                    inside = (
                        (body_z > 0.025) & (bu > -0.03) & (bu < 1.03) & (bv > -0.03) & (bv < 1.03)
                    )
                    if inside.any():
                        visible_body_depths.append(float(body_z[inside].max()))
                    cc = body[ids]
                    zs = -cc[:, 2]
                    if np.isfinite(zs).all() and zs.min() > 0.025:
                        actor_z.append([float(zs.min()), float(zs.max())])
                        face_bounds.append(projected_bounds(cc))
                if not actor_z:
                    continue
                for group, ratios, front in [
                    (bars, foreground_ratio, True),
                    (backing, background_ratio, False),
                ]:
                    if not group:
                        continue
                    bounds = []
                    overlaps = False
                    for obj in group:
                        if obj.hide_render:
                            continue
                        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
                        depths = [-(inv[2, :3] @ np.asarray(v) + inv[2, 3]) for v in corners]
                        bounds.extend(depths)
                        if front:
                            camcoords = np.asarray(corners) @ inv[:3, :3].T + inv[:3, 3]
                            bound = projected_bounds(camcoords)
                            if bound:
                                for head_bound in face_bounds:
                                    if head_bound and np.all(
                                        np.minimum(bound[1], head_bound[1])
                                        - np.maximum(bound[0], head_bound[0])
                                        > 0.006
                                    ):
                                        overlaps = True
                    if not bounds or (front and f not in witnesses and not overlaps):
                        continue
                    extent = max(bounds) if front else min(bounds)
                    if extent <= 0.02:
                        continue
                    actual = (
                        min(z[0] for z in actor_z)
                        if front
                        else max([z[1] for z in actor_z] + visible_body_depths)
                    )
                    ratios.append((actual * (0.97 if front else 1.06)) / extent)
                    if front:
                        observed.append(f)
            for group, ratio, kind in [
                (bars, min([1.0, *foreground_ratio]), "source-overlap grating"),
                (backing, max([1.0, *background_ratio]), "inferred background"),
            ]:
                if group:
                    for obj in group:
                        scene.frame_set(int(obj["source_frame"]) + 1)
                        origin = camera.matrix_world.translation.copy()
                        # Objects are inserted with identity transform, and this operation is called
                        # before any external editing of the measured set.
                        if "motion_frames" in obj:
                            for f in obj["motion_frames"]:
                                scene.frame_set(int(f) + 1)
                                center = camera.matrix_world.translation.copy()
                                obj.location = center + (obj.location - center) * ratio
                                obj.keyframe_insert(data_path="location", frame=int(f) + 1)
                            for v in obj.data.vertices:
                                v.co *= ratio
                        else:
                            for v in obj.data.vertices:
                                v.co = origin + (v.co - origin) * ratio
                        obj.data.update()
                        obj["depth_fit_ratio"] = float(ratio)
                    audit.append(
                        {
                            "first": first,
                            "last": last,
                            "kind": kind,
                            "figure_mesh_witness_frames": observed,
                            "objects": len(group),
                            "factor": float(ratio),
                            "rigid_shot_fit": True,
                        }
                    )
    finally:
        scene.frame_set(saved)
    return audit
