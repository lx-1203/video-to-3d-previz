"""Build a closed, world-fixed fascia only when both of its edges have multiview support."""

import json
import numpy as np

if __package__:
    from .fullclip_guardrail_panel import fit_edge, observed_posts
    from .fullclip_guardrail_clearance import snapshot_meshes, cylinder_tree
else:
    from fullclip_guardrail_panel import fit_edge, observed_posts
    from fullclip_guardrail_clearance import snapshot_meshes, cylinder_tree


def bind_panel(scene, samples, actors, rail, width, height, source_sha, idx):
    import bpy
    from mathutils.bvhtree import BVHTree

    if __package__:
        from .fullclip_guardrail_bind import make_member
        from .fullclip_guardrail_fit import fit
    else:
        from fullclip_guardrail_bind import make_member
        from fullclip_guardrail_fit import fit

    ends = np.asarray(rail["ends"])
    rows = [s["row"] for s in samples]
    reference = fit(samples, width, height, constrain_depth=False)
    if reference is None:
        return {"status": "insufficient_rigid_reference"}
    top = fit_edge(samples, reference["ends"], "top", height)
    bottom = fit_edge(samples, reference["ends"], "bottom", height)
    if not top or not bottom:
        return {"status": "insufficient_paired_multiview_profile"}
    upper = top["ends"]
    lower = bottom["ends"]
    lip = fit_edge(samples, reference["ends"], "lip", height)
    if not lip:
        return {"status": "insufficient_fascia_cap_evidence"}
    axis = upper[1] - upper[0]
    axis /= np.linalg.norm(axis)

    # Map the observed profile lines onto the same end stations. Separate
    # visibility intervals can truncate the near/far sample of the lower edge
    # when the bottom exits the camera; this does not cut the physical solid.
    def stations(line):
        return line + np.outer((upper - line) @ axis, axis)

    nose = stations(lip["ends"])
    lower = stations(lower)
    # The unobserved rear of a closed masonry/wood fascia follows gravity.
    # Set its depth from the observed bottom silhouette along rays through the
    # upper edge, so the end caps cannot leave a triangular hole by an actor.
    down = np.array([0.0, 0.0, -1.0])
    estimates = []
    for s in samples:
        p = s["projection"]
        r = s["row"].get("panel_profile", {}).get("bottom")
        if not r:
            continue
        u = 0.5 * (r["x_min"] + r["x_max"])
        v = r["slope"] * u + r["intercept"]
        basis = p @ np.r_[axis, 0.0]
        den = basis[0] - u * basis[2]
        if abs(den) < 1e-7:
            continue
        q = p[1] - v * p[2] - (p[0] - u * p[2]) * (basis[1] - v * basis[2]) / den
        if abs(q[:3] @ down) < 1e-6:
            continue
        drop = -(q @ np.r_[upper[0], 1.0]) / (q[:3] @ down)
        if drop > 0:
            estimates.append(drop)
    if len(estimates) < 12:
        return {"status": "insufficient_solid_depth_evidence"}
    drop = float(np.median(estimates))
    back = upper + drop * down
    rings = [upper, nose, lower, back]
    verts = np.vstack(rings)
    faces = [(0, 2, 4, 6), (7, 5, 3, 1)] + [
        (2 * i, 2 * i + 1, 2 * ((i + 1) % 4) + 1, 2 * ((i + 1) % 4)) for i in range(4)
    ]
    thickness = float(np.linalg.norm(nose[0] - upper[0]))
    radius = max(np.max(np.linalg.norm(line - ends, axis=1)) for line in rings)
    cache, seen = snapshot_meshes(scene, rows, actors, ends, radius)
    tree = BVHTree.FromPolygons(verts.tolist(), faces)
    from mathutils import Vector

    probe = Vector((0.619, 0.319, 0.715)).normalized()

    def enclosed(point):
        if point is None:
            return False
        point = point.copy()
        count = 0
        for _ in range(len(faces) + 1):
            hit, _, _, _ = tree.ray_cast(point, probe)
            if hit is None:
                break
            count += 1
            point = hit + probe * 1.0e-5
        return bool(count % 2)

    intersections = sum(bool(tree.overlap(part)) for part in cache)
    contained = sum(
        enclosed(part.find_nearest(Vector(verts.mean(axis=0)))[0])
        for part in cache
        if not tree.overlap(part)
    )
    collisions = intersections + contained
    result = {
        "upper_views": top["views"],
        "lower_views": bottom["views"],
        "upper_inlier_median_px": top["inlier_median_px"],
        "lower_inlier_median_px": bottom["inlier_median_px"],
        "top_ends": top["ends"].tolist(),
        "bottom_ends": lower.tolist(),
        "cap_views": lip["views"],
        "back_drop_inferred": drop,
        "thickness": float(thickness),
        "actor_mesh_frames_checked": seen,
        "actor_surface_intersections": intersections,
        "contained_actor_meshes": contained,
    }
    if collisions:
        return dict(result, status="actor_surface_intersection")
    shot = rows[0]["shot"]
    label = f"Observed gallery fascia {idx + 1:02} • {shot}"
    for old in scene.objects:
        if (
            old.get("source_sha256") != source_sha
            or old.get("fascia_track_index") != idx
            or old.get("shot_id") != shot
        ):
            continue
        receipt = old.get("fascia_receipt")
        if (
            receipt
            and len(old.data.vertices) == len(verts)
            and np.allclose([list(v.co) for v in old.data.vertices], verts, atol=1.0e-5)
        ):
            saved = json.loads(receipt)
            if all(post["object"] in scene.objects for post in saved["posts"]):
                return dict(saved, reused=True)
    mesh = bpy.data.meshes.new(label)
    mesh.from_pydata(verts.tolist(), [], faces)
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    ob = bpy.data.objects.new(label, mesh)
    scene.collection.objects.link(ob)
    material = bpy.data.materials.get("Observed fascia • clay") or bpy.data.materials.new(
        "Observed fascia • clay"
    )
    material.diffuse_color = (0.42, 0.39, 0.35, 1.0)
    mesh.materials.append(material)
    ob["reconstruction_kind"] = "Measured foreground fascia"
    ob["observed_fascia"] = True
    ob["shot_id"] = shot
    ob["source_sha256"] = source_sha
    ob["static_world_geometry"] = True
    first, last = rows[0]["frame"], rows[-1]["frame"]
    for key, hidden in sorted({1: True, first + 1: False, last + 2: True}.items()):
        for attr in ("hide_render", "hide_viewport"):
            setattr(ob, attr, hidden)
            ob.keyframe_insert(data_path=attr, frame=key)
    cap = bpy.data.materials.get("Observed fascia • cap") or bpy.data.materials.new(
        "Observed fascia • cap"
    )
    cap.diffuse_color = (0.64, 0.60, 0.53, 1.0)
    mesh.materials.append(cap)
    mesh.polygons[2].material_index = 1
    stale = conflicting_prior_frontage(scene, samples, upper, width, height, tree)
    result.update(
        status="observed_rigid_profile",
        object=ob.name,
        posts=[],
        superseded_in_this_shot=hide_conflicting_shot(scene, stale, first, last),
    )
    for number, post in enumerate(observed_posts(samples, ends, rail["radius"], width)):
        fraction = post["fraction"]
        a = ends[0] + fraction * (ends[1] - ends[0])
        axis = top["ends"][1] - top["ends"][0]
        b = top["ends"][0] + axis * np.dot(a - top["ends"][0], axis) / np.dot(axis, axis)
        member = {"ends": np.asarray([a, b]), "radius": rail["radius"] * 0.79}
        post_tree = cylinder_tree(member["ends"], member["radius"])
        if any(post_tree.overlap(part) for part in cache):
            continue
        name = make_member(scene, member, shot, idx, first, last, source_sha)
        obj = bpy.data.objects[name]
        obj.name = f"{label} • support {number + 1:02}"
        obj["reconstruction_kind"] = "Observed rail support"
        result["posts"].append(dict(post, object=obj.name))
    ob["fascia_track_index"] = idx
    ob["fascia_receipt"] = json.dumps(result)
    return result


def conflicting_prior_frontage(scene, samples, upper_ends, width, height, fascia_tree):
    """Find older depth-only structural estimates at the same observed frontage."""
    from mathutils.bvhtree import BVHTree

    kinds = {"Gallery walkway", "Balustrade panel", "Handrail", "Panel moulding"}
    candidates = []
    axis = upper_ends[1] - upper_ends[0]
    axis /= np.linalg.norm(axis)
    shot = samples[0]["row"]["shot"]
    for o in scene.objects:
        key = o.get("source_frame")
        if (
            o.type != "MESH"
            or o.get("reconstruction_kind") not in kinds
            or not isinstance(key, int)
        ):
            continue
        if (
            key < samples[0]["row"]["frame"]
            or key > samples[-1]["row"]["frame"]
            or o.get("shot_id", shot) != shot
        ):
            continue
        vertices = np.asarray([tuple(o.matrix_world @ v.co) for v in o.data.vertices])
        if len(vertices) < 3:
            continue
        direction = np.linalg.svd(vertices - vertices.mean(axis=0), full_matrices=False)[2][0]
        if abs(axis @ direction) < 0.94:
            continue
        hom = np.column_stack((vertices, np.ones(len(vertices))))
        source = min(samples, key=lambda s: abs(s["row"]["frame"] - key))

        def projection(s):
            xy = hom @ s["projection"].T
            return xy[:, :2] / xy[:, 2, None] if np.all(xy[:, 2] > 0.01) else None

        uv = projection(source)
        if uv is None:
            continue
        r = source["row"]
        tt = r.get("panel_profile", {}).get("top")
        if not tt:
            continue
        xlo, xhi = np.min(uv[:, 0]), np.max(uv[:, 0])
        overlap = min(r["x_max"], xhi) - max(r["x_min"], xlo)
        if overlap < 0.5 * (xhi - xlo) or overlap < 0.08 * width:
            continue
        # A different wing can share a vanishing point; require the source
        # footprint and confirmed crossings of the open rail-to-rim gap.
        base = r.get("panel_profile", {}).get("bottom")
        pixels = uv[:, 1]
        above = pixels - tt["slope"] * uv[:, 0] - tt["intercept"]
        in_band = base and np.any(
            (above >= 0) & (pixels < base["slope"] * uv[:, 0] + base["intercept"])
        )
        if not in_band and np.min(abs(above)) > 0.09 * height:
            continue
        conflicts = 0
        for s in samples[::4]:
            pts = projection(s)
            row = s["row"]
            top = row.get("panel_profile", {}).get("top")
            if pts is None or not top:
                continue
            u, v = pts.T
            lower = top["slope"] * u + top["intercept"]
            rail = row["slope"] * u + row["intercept"]
            valid = (
                (u > max(0, row["x_min"]))
                & (u < min(width, row["x_max"]))
                & (v > 0)
                & (v < height)
            )
            conflicts += bool(
                (
                    valid & (v > rail - 2 * row["thickness"]) & (v < lower - 2 * row["thickness"])
                ).any()
            )
        legacy_tree = BVHTree.FromPolygons(
            vertices.tolist(), [f.vertices[:] for f in o.data.polygons]
        )
        intersects = bool(fascia_tree.overlap(legacy_tree))
        candidates.append((o, key, xlo, xhi, conflicts, intersects))
    conflicts = [r for r in candidates if r[-2] >= 4 or r[-1]]
    return [
        o
        for o, key, a, b, _, _ in candidates
        if any(
            k == key and min(b, hi) - max(a, lo) > 0.75 * min(b - a, hi - lo)
            for other, k, lo, hi, votes, intersects in conflicts
        )
    ]


def hide_conflicting_shot(scene, objects, first, last):
    output = []
    for obj in objects:
        scene.frame_set(last + 2)
        restored = {k: getattr(obj, k) for k in ("hide_render", "hide_viewport")}
        scene.frame_set(max(1, first))
        before = {k: getattr(obj, k) for k in restored}
        for key, state in (
            (max(1, first), before),
            (first + 1, dict.fromkeys(restored, True)),
            (last + 2, restored),
        ):
            for attr, hidden in state.items():
                setattr(obj, attr, hidden)
                obj.keyframe_insert(data_path=attr, frame=key)
        output.append(obj.name)
    return output
