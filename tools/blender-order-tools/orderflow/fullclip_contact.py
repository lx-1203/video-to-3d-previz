"""Resolve inferred lower limbs against the actual finite tier meshes over an entire track."""

import numpy as np

from . import fullclip_contact_guard as guard
from . import fullclip_contact_path as path

SUPPORT = {"Stepped seating platform", "Room floor", "Lower landing platform"}


def _mesh(obj, deps):
    evaluated = obj.evaluated_get(deps)
    data = evaluated.data
    return np.asarray(
        [tuple(evaluated.matrix_world @ vertex.co) for vertex in data.vertices]
    ), data


def _surfaces(deps, scenery):
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    vertices, faces, solids = [], [], []
    objects = []
    obstacles = []
    for obj in scenery:
        positions, mesh = _mesh(obj, deps)
        if not len(positions):
            continue
        first = len(vertices)
        vertices.extend(Vector(x) for x in positions)
        polys = [list(p.vertices) for p in mesh.polygons]
        faces.extend(tuple(first + i for i in poly) for poly in polys)
        kind = obj.get("reconstruction_kind", "")
        if kind.startswith("Seat ") or kind in SUPPORT:
            obj_tree = BVHTree.FromPolygons([Vector(v) for v in positions], polys)
            objects.append((obj.name, kind in SUPPORT, guard.bounds(positions, obj_tree)))
        if kind.startswith("Seat "):
            obstacles.append(path.oriented_proxy(positions))
        if kind not in SUPPORT:
            continue
        normals, offsets = [], []
        center = positions.mean(0)
        for poly in polys:
            points = positions[poly]
            n = np.cross(points[1] - points[0], points[2] - points[0])
            size = np.linalg.norm(n)
            if size < 1e-8:
                continue
            n /= size
            if n @ (points.mean(0) - center) < 0:
                n = -n
            normals.append(n)
            offsets.append(n @ points[0])
        normals, offsets = np.array(normals), np.array(offsets)
        if len(normals) >= 4 and np.max(positions @ normals.T - offsets) < 0.001:
            solids.append((positions.min(0), positions.max(0), normals, offsets))
    return BVHTree.FromPolygons(vertices, faces), solids, objects, obstacles


def _depth(view, vertices, tree, width, height):
    from mathutils import Vector

    origin, rot, focal, principal = view
    local = (vertices - origin) @ rot
    if local[:, 2].min() < 0.05:
        return None
    projection = local[:, :2] / local[:, 2:] * focal + principal
    low = np.maximum([0, 0], np.floor(projection.min(0) - [24, 22]).astype(int))
    high = np.minimum([width, height], np.ceil(projection.max(0) + [24, 22]).astype(int))
    if np.any(high <= low):
        return None
    xx, yy = np.meshgrid(np.arange(low[0], high[0]), np.arange(low[1], high[1]))
    grid = np.column_stack((xx.ravel() + 0.5, yy.ravel() + 0.5))
    rays = np.column_stack(((grid - principal) / focal, np.ones(len(grid)))) @ rot.T
    origin = Vector(origin)
    distances = np.fromiter((tree.ray_cast(origin, Vector(ray))[3] or 1e6 for ray in rays), float)
    return (distances / np.linalg.norm(rays, axis=1)).reshape(xx.shape), low


def _groups(rig, mesh, side):
    names = [f"DEF-{segment}.{side}" for segment in ("thigh", "shin")]
    if any(n not in mesh.vertex_groups or n not in rig.pose.bones for n in names):
        return None
    indices = []
    for n in names:
        group = mesh.vertex_groups[n].index
        indices.append(
            np.array(
                [
                    v.index
                    for v in mesh.data.vertices
                    if any(w.group == group and w.weight > 0.6 for w in v.groups)
                ]
            )
        )
    if min(map(len, indices)) < 4:
        return None
    shin = indices[1]
    bone = rig.data.bones[names[1]]
    axis = np.asarray(bone.tail_local) - np.asarray(bone.head_local)
    rest = np.array([tuple(mesh.data.vertices[i].co) for i in shin])
    longitudinal = rest @ axis
    if np.ptp(longitudinal) < 1e-8:
        return None
    position = dict(zip(shin, (longitudinal - longitudinal.min()) / np.ptp(longitudinal)))
    end = [i for i in shin if position[i] >= 0.85]
    clip = []
    for e in mesh.data.edges:
        a, b = e.vertices
        if a in position and b in position and (position[a] - 0.85) * (position[b] - 0.85) < 0:
            clip.append((a, b, (0.85 - position[a]) / (position[b] - position[a])))
    return names, indices, end, clip


def _compact(points):
    if len(points) <= 16:
        return points
    # Bound solver memory on higher-resolution models while retaining extrema.
    directions = np.array(
        [
            [x, y, z]
            for x in (-1, 0, 1)
            for y in (-1, 0, 1)
            for z in (-1, 0, 1)
            if (x, y, z) != (0, 0, 0)
        ]
    )
    return points[np.unique((points @ directions.T).argmax(0))]


def _run(scene, camera, rig, mesh, track, width, height, view_for, rules):
    import bpy
    from mathutils import Quaternion, Vector

    dims = 640, max(1, round(640 * height / width))
    geometry = [
        obj
        for obj in scene.objects
        if obj.type == "MESH" and not (obj.parent and obj.parent.type == "ARMATURE")
    ]
    prior = None
    rows = {side: [] for side in rules}
    original = []
    guard_samples = []
    for frame, actor in track:
        scene.frame_set(frame + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        scenery = [o for o in geometry if not o.hide_render]
        signature = tuple(
            (o.name, tuple(val for row in o.matrix_world for val in row)) for o in scenery
        )
        if signature != prior:
            tree, solids, objects, chair_proxies = _surfaces(deps, scenery)
            prior = signature
        if not solids:
            return {"state": "no_convex_tread"}
        view = view_for(camera, deps, *dims)
        axes = path.axes(view)
        vertices, _ = _mesh(mesh, deps)
        if axes is None or len(vertices) != len(mesh.data.vertices):
            return {"state": "unsupported_mesh_or_camera"}
        tile = _depth(view, vertices, tree, *dims)
        if tile is None:
            return {"state": "outside_view"}
        originals = {}
        limbs = {}
        for side, (names, indices, end, clip) in rules.items():
            thigh, shin = (rig.pose.bones[n] for n in names)
            hip, knee = [np.asarray(rig.matrix_world @ bone.head) for bone in (thigh, shin)]
            distal = np.vstack(
                [
                    vertices[end],
                    *[vertices[a] + (vertices[b] - vertices[a]) * t for a, b, t in clip],
                ]
            )
            limbs[side] = hip, knee, vertices[indices[0]], vertices[indices[1]], distal
            side_name = "left" if side == "L" else "right"
            joints = [
                actor.get("joints2d", {}).get(f"{side_name}_{joint}", {})
                for joint in ("knee", "ankle")
            ]
            observed = any(
                j.get("visibility") in ("visible", "observed") or j.get("score", 0) >= 0.65
                for j in joints
            )
            opposites = [
                path.oriented_proxy(vertices[idx])
                for other, rule in rules.items()
                if other != side
                for idx in rule[1]
            ]
            reach = (
                np.linalg.norm(knee - hip)
                + np.linalg.norm(vertices[indices[1]] - knee, axis=1).max()
                + 0.03
            )
            nearby = [
                proxy
                for proxy in chair_proxies
                if np.all(hip + reach >= proxy[0]) and np.all(hip - reach <= proxy[1])
            ]
            rows[side].append(
                path.evaluate(
                    hip,
                    knee,
                    _compact(vertices[indices[0]]),
                    _compact(vertices[indices[1]]),
                    _compact(distal),
                    view,
                    tile,
                    solids,
                    observed,
                    nearby + opposites,
                )
            )
            originals[side] = (
                thigh.matrix.to_quaternion().copy(),
                shin.matrix.to_quaternion().copy(),
            )
        guard_samples.append((axes, limbs, objects))
        original.append((rig.matrix_world.to_quaternion().copy(), axes, originals))
    paths = {side: path.fit_sequence(samples) for side, samples in rows.items()}
    paths = guard.restrict(paths, guard_samples)
    summary = {}
    for side, values in paths.items():
        summary[side] = {
            "baseline_candidate_intersections": sum(row[2] for row in rows[side]),
            "modified_frames": int((abs(values).max(1) > 1e-6).sum()),
        }
    # Read all evaluated starting poses before writing keys. Inserting into a reduced
    # action can change the original interpolation at the following sample.
    for i, (frame, _) in enumerate(track):
        scene.frame_set(frame + 1)
        world, axes, originals = original[i]
        for side, angles in paths.items():
            if not summary[side]["modified_frames"]:
                continue
            lateral, pitch_hip, pitch_shin = angles[i]
            common = Quaternion(Vector(axes[0]), lateral)
            for name, base, pitch in zip(rules[side][0], originals[side], (pitch_hip, pitch_shin)):
                pose = rig.pose.bones[name]
                target = (
                    world.inverted() @ common @ Quaternion(Vector(axes[1]), pitch) @ world @ base
                )
                parent = pose.parent
                space = (
                    parent.matrix @ parent.bone.matrix_local.inverted() @ pose.bone.matrix_local
                )
                q = space.to_quaternion().inverted() @ target
                q.make_compatible(pose.rotation_quaternion)
                pose.rotation_quaternion = q
                pose.keyframe_insert(data_path="rotation_quaternion", frame=frame + 1)
                bpy.context.view_layer.update()
    return {"state": "inferred_continuous_tread_fit", "sides": summary}


def fit_contact(scene, camera, rig, track, width, height, view_for):
    mesh = next(
        (o for o in rig.children if o.type == "MESH" and "DEF-shin.L" in o.vertex_groups),
        None,
    )
    if mesh is None:
        return []
    rules = {side: rule for side in ("L", "R") if (rule := _groups(rig, mesh, side)) is not None}
    if not rules:
        return []
    # Source gaps and cuts are independent. Contacts never propagate across them.
    times = np.array([f for f, _ in track])
    runs = np.split(np.arange(len(track)), np.flatnonzero(np.diff(times) != 1) + 1)
    results = []
    for ids in runs:
        if len(ids) >= 8:
            segment = [track[k] for k in ids]
            from .fullclip_contact_rig import fit_plant

            entry = fit_plant(scene, camera, rig, mesh, segment, width, height, view_for, rules)
            if entry is None:
                entry = _run(scene, camera, rig, mesh, segment, width, height, view_for, rules)
            results.append({"source_first": segment[0][0], "source_last": segment[-1][0], **entry})
    return results
