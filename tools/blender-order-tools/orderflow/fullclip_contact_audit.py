"""Measure the final posed lower limbs against finite modeled tread surfaces."""

import numpy as np

from . import fullclip_contact as contact
from . import fullclip_contact_guard as geometry


def _surfaces(objects, deps):
    from mathutils.bvhtree import BVHTree

    result = []
    for obj in objects:
        if obj.evaluated_get(deps).hide_render:
            continue
        vertices, mesh = contact._mesh(obj, deps)
        if len(mesh.polygons) and len(vertices) >= 4:
            tree = BVHTree.FromPolygons(vertices, [tuple(p.vertices) for p in mesh.polygons])
            result.append((obj.name, geometry.bounds(vertices, tree)))
    return result


def _clearance(foot, points, surface):
    from mathutils import Vector

    low, high, center, tree = foot
    lo, hi, _, tread = surface
    if np.any(high[:2] < lo[:2]) or np.any(lo[:2] > hi[:2]) or low[2] > hi[2] + 0.03:
        return None
    gaps = []
    for x, y, _ in np.vstack((points, (points + center) * 0.5, center)):
        hit, normal, _, _ = tread.ray_cast(Vector((x, y, hi[2] + 0.1)), Vector((0, 0, -1)))
        if hit is None or abs(normal.z) < 0.65:
            continue
        sole, _, _, _ = tree.ray_cast(Vector((x, y, low[2] - 0.1)), Vector((0, 0, 1)))
        if sole is not None and sole.z >= hit.z:
            gaps.append(sole.z - hit.z)
    return min(gaps, default=None)


def _side(vertices, rule, surfaces):
    _, (upper, lower), ends, clipped = rule
    points = np.vstack(
        [vertices[ends], *[vertices[i] + (vertices[j] - vertices[i]) * t for i, j, t in clipped]]
    )
    foot = geometry.hull(points)
    crossed = {}
    for segment, hull in [
        ("thigh", geometry.hull(vertices[upper])),
        ("shin", geometry.hull(vertices[lower])),
        ("foot_end", foot),
    ]:
        crossed[segment] = [name for name, solid in surfaces if geometry._hit(hull, solid)]
    nearby = [
        (gap, name)
        for name, solid in surfaces
        if (gap := _clearance(foot, points, solid)) is not None and gap <= 0.03
    ]
    gap, name = min(nearby, default=(None, None))
    return {
        "crossed": crossed,
        "support_within_30mm": name if not crossed["foot_end"] else None,
        "nearby_gap_m": gap,
    }


def measure_final_support(scene, binding):
    """Inspect visible modeled frames after key reduction, transforms and geometry modifiers."""
    import bpy

    tracks = binding.get("partial_tier_depth", {})
    if not tracks:
        return {}
    rigs = {
        str(scene.objects[name].get("track_id")): scene.objects[name]
        for name in binding["editable_tracks"].values()
    }
    objects = [
        o
        for o in scene.objects
        if o.type == "MESH"
        and o.get("reconstruction_kind") in contact.SUPPORT | {"Occupied gallery deck"}
    ]
    result = {}
    saved = scene.frame_current
    try:
        for ident, record in tracks.items():
            rig = rigs.get(ident)
            mesh = (
                next(
                    (
                        m
                        for m in rig.children
                        if m.type == "MESH" and "DEF-shin.L" in m.vertex_groups
                    ),
                    None,
                )
                if rig
                else None
            )
            rules = {s: contact._groups(rig, mesh, s) for s in ("L", "R")} if mesh else {}
            if not rules or not all(rules.values()) or not objects:
                result[ident] = {"state": "unmeasured"}
                continue
            frames = []
            for sample in record["frames"]:
                f = sample["frame"]
                scene.frame_set(f + 1)
                deps = bpy.context.evaluated_depsgraph_get()
                vertices, _ = contact._mesh(mesh, deps)
                surfaces = _surfaces(objects, deps)
                sides = {s: _side(vertices, r, surfaces) for s, r in rules.items()}
                frames.append(dict(frame=f, measured=bool(surfaces), sides=sides))
            crossed = [
                r["frame"]
                for r in frames
                if any(any(s["crossed"].values()) for s in r["sides"].values())
            ]
            unsupported = [
                r["frame"]
                for r in frames
                if r["measured"] and not any(s["support_within_30mm"] for s in r["sides"].values())
            ]
            unknown = [r["frame"] for r in frames if not r["measured"]]
            result[ident] = dict(
                frames=frames,
                limb_intersection_frames=crossed,
                no_support_within_30mm_frames=unsupported,
                unmeasured_frames=unknown,
                state="unmeasured"
                if not frames or unknown
                else ("failed" if crossed or unsupported else "passed"),
            )
    finally:
        scene.frame_set(saved)
    return result
