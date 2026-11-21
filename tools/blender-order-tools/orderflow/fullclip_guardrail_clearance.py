"""Check swept actor poses against a fixed structural member and resolve small penetrations."""

import math
import numpy as np


def capsule_bounds(ends, points):
    axis = ends[1] - ends[0]
    fraction = np.clip((points - ends[0]) @ axis / (axis @ axis), 0, 1)
    residual = points - ends[0] - fraction[:, None] * axis
    return np.linalg.norm(residual, axis=1)


def cylinder_tree(ends, radius):
    from mathutils.bvhtree import BVHTree

    direction = ends[1] - ends[0]
    direction /= np.linalg.norm(direction)
    normal = np.cross(direction, np.eye(3)[np.argmin(np.abs(direction))])
    normal /= np.linalg.norm(normal)
    tangent = np.cross(direction, normal)
    angles = np.arange(16) * 2 * math.pi / 16
    ring = radius * (normal * np.cos(angles)[:, None] + tangent * np.sin(angles)[:, None])
    verts = np.concatenate((ends[0] + ring, ends[1] + ring))
    faces = [(i, (i + 1) % 16, 16 + (i + 1) % 16, 16 + i) for i in range(16)] + [
        tuple(range(16)),
        tuple(range(16, 32)),
    ]
    return BVHTree.FromPolygons(verts.tolist(), faces)


def snapshot_meshes(scene, track, actors, ends, margin):
    import bpy
    from mathutils.bvhtree import BVHTree

    shot = track[0]["shot"]
    meshes = [ob for (sid, _), objs in actors.items() if sid == shot for ob in objs]
    cache = []
    seen = 0
    for f in range(track[0]["frame"], track[-1]["frame"] + 1):
        scene.frame_set(f + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        for ob in meshes:
            evaluated = ob.evaluated_get(deps)
            if evaluated.hide_render:
                continue
            seen += 1
            mw = np.asarray(evaluated.matrix_world)
            box = np.asarray(evaluated.bound_box) @ mw[:3, :3].T + mw[:3, 3]
            center = box.mean(axis=0)
            if (
                capsule_bounds(ends, center[None, :])[0]
                > np.max(np.linalg.norm(box - center, axis=1)) + margin
            ):
                continue
            mesh = evaluated.data
            xyz = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", xyz)
            xyz = xyz.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
            # Keep all faces of nearby meshes. A long face can cross a rod even
            # when none of its vertices lies within the candidate's search radius.
            faces = [poly.vertices[:] for poly in mesh.polygons]
            if faces:
                cache.append(BVHTree.FromPolygons(xyz.tolist(), faces))
    return cache, seen


def resolve(scene, rows, actors, samples, solved, width, height):
    if __package__:
        from .fullclip_guardrail_fit import line_pixel
    else:
        from fullclip_guardrail_fit import line_pixel
    ends = np.asarray(solved["ends"])
    r = solved["radius"]
    cache, seen = snapshot_meshes(scene, rows, actors, ends, r * 45)

    def count(pts):
        rail = cylinder_tree(pts, r)
        return sum(bool(rail.overlap(tree)) for tree in cache)

    initial = count(ends)
    solved.update(
        actor_frames_checked=seen,
        initial_intersecting_actor_frames=initial,
        intersecting_actor_frames=initial,
        clearance_adjusted=False,
    )
    if initial == 0:
        return
    axis = ends[1] - ends[0]
    axis /= np.linalg.norm(axis)
    planes = np.asarray([s["plane"][:3] for s in samples])
    normal = np.linalg.svd(planes, full_matrices=False)[2][0]
    shift = np.cross(axis, normal)
    shift /= np.linalg.norm(shift)
    vertical = np.cross(axis, shift)
    vertical /= np.linalg.norm(vertical)
    p = []
    u = []
    v = []
    depth = []
    for s in samples:
        row = s["row"]
        for x, z in [
            (row["x_min"] * 0.8 + row["x_max"] * 0.2, 0.0),
            (row["x_min"] * 0.2 + row["x_max"] * 0.8, 0.0),
            *s["front"],
        ]:
            p.append(s["projection"])
            u.append(x)
            v.append(row["slope"] * x + row["intercept"])
            depth.append(z)
    p, u, v, z = map(np.asarray, (p, u, v, depth))
    mask = z > 0

    # Vary endpoint depths within their observed projection planes. Each candidate
    # remains a single static straight solid for every original camera and actor pose.
    # Solve vertical corrections in screen space; never warp per frame or move actors.
    def pixels(pts):
        return line_pixel(pts[0], pts[1] - pts[0], p, u)[:2]

    initial_y, _ = pixels(ends)
    jac = np.column_stack(
        [(pixels(ends + np.eye(2)[j, :, None] * vertical * r)[0] - initial_y) / r for j in (0, 1)]
    )
    weights = np.sqrt(np.minimum(1.0, 5.0 / np.maximum(1e-5, np.abs(initial_y - v))))
    jac = jac * weights[:, None]
    least = np.linalg.pinv(jac, rcond=1e-5)
    endpoint_p = np.asarray([s["projection"] for s in samples])
    endpoint_x = np.asarray([[s["row"]["x_min"], s["row"]["x_max"]] for s in samples])
    typical_depth = np.median(z[mask])

    def observed_ends(pts):
        # A depth correction changes the along-rail image coordinate too. Refit
        # finite ends against interior source endpoints before testing collisions.
        org = pts[0]
        di = pts[1] - pts[0]
        limits = []
        for side in range(2):
            columns = endpoint_x[:, side]
            _, distance, t = line_pixel(org, di, endpoint_p, columns)
            valid = (distance > 0.15 * typical_depth) & (distance < 8 * typical_depth)
            visible = valid & (columns > 0.025 * width) & (columns < 0.975 * width)
            vals = t[visible] if visible.sum() >= 6 else t[valid]
            if not len(vals):
                return pts
            limits.append(np.percentile(vals, 15 if side == 0 else 85))
        return org + np.asarray(limits)[:, None] * di

    options = []
    offsets = np.array([-32, -24, -16, -10, -6, -3, 0, 3, 6, 10, 16, 24, 32]) * r
    for a in offsets:
        for b in offsets:
            if a == b == 0:
                continue
            pts = ends + np.array([a, b])[:, None] * shift
            delta = pixels(pts)[0] - v
            fine = np.clip(-least @ (delta * weights), -15 * r, 15 * r)
            pts += fine[:, None] * vertical
            yy, dd = pixels(pts)
            err = np.abs(yy - v)
            median, p90 = np.percentile(err, [50, 90])
            fraction = float((dd[mask] < z[mask]).mean())
            if (
                median > height * 0.016
                or p90 > height * 0.030
                or fraction < 0.94
                or np.any(dd < 0)
            ):
                continue
            score = p90 * 0.55 + median * 0.45 + (0.035 / r) * (abs(a) + abs(b))
            options.append((score, observed_ends(pts), median, p90, fraction))
    best = initial
    for _, pts, median, p90, fraction in sorted(options, key=lambda r: r[0]):
        collisions = count(pts)
        if collisions < best:
            best = collisions
            solved.update(
                ends=pts,
                median_px=float(median),
                p90_px=float(p90),
                front_fraction=fraction,
                intersecting_actor_frames=best,
                clearance_adjusted=True,
            )
        if best == 0:
            break

    if solved["clearance_adjusted"]:
        # A finite end can move along the axis as the depth estimate changes.
        # Rebuild the broad phase around the selected member before reporting it.
        final_cache, _ = snapshot_meshes(scene, rows, actors, solved["ends"], r * 1.02)
        selected = cylinder_tree(solved["ends"], r)
        solved["intersecting_actor_frames"] = sum(
            bool(selected.overlap(tree)) for tree in final_cache
        )
