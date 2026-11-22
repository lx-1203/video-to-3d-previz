"""Place a partially observed fixed-size actor between source-supported seating tiers."""

from collections import defaultdict

import numpy as np


def _unit(v):
    return v / max(1e-8, np.linalg.norm(v))


def _view(camera, deps, width, height):
    c = camera.evaluated_get(deps)
    world = np.asarray(c.matrix_world.normalized())
    proj = c.calc_matrix_camera(deps, x=width, y=height)
    focal = np.array([proj[0][0] * width, proj[1][1] * height]) * 0.5
    principal = np.array([(1 - proj[0][2]) * width, (1 + proj[1][2]) * height]) * 0.5
    return world[:3, 3], world[:3, :3] @ np.diag([1.0, -1.0, -1.0]), focal, principal


def _screen(points, view):
    origin, rotation, focal, center = view
    local = (np.asarray(points) - origin) @ rotation
    return local[:, :2] / np.maximum(0.001, local[:, 2:]) * focal + center, local[:, 2]


def _back(uv, z, view):
    origin, rotation, focal, center = view
    return origin + rotation @ np.r_[(uv - center) / focal, 1.0] * z


def _trace(scene, deps, uv, view):
    from mathutils import Vector

    origin, rotation, _, _ = view
    ray = _unit(_back(uv, 1.0, view) - origin)
    start = origin.copy()
    for _ in range(15):
        hit, point, _, _, ob, _ = scene.ray_cast(deps, Vector(start), Vector(ray))
        if not hit:
            return None
        xyz = np.asarray(point)
        if not any(c.type == "ARMATURE" for c in ([ob.parent] if ob.parent else [])):
            return ob, float((xyz - origin) @ rotation[:, 2])
        start = xyz + ray * 0.008
    return None


def _pair(actor, name):
    pts = []
    for side in ("left", "right"):
        part = actor.get("joints2d", {}).get(side + "_" + name, {})
        if (
            part.get("score", 0) >= 0.24
            and part.get("u") is not None
            and part.get("v") is not None
        ):
            pts.append([part["u"], part["v"]])
    return np.mean(pts, axis=0) if pts else None


def _context(scene, cam, track, width, height):
    scene.frame_set(track[0][0] + 1)

    seatbacks = [
        o
        for o in scene.objects
        if o.get("reconstruction_kind") == "Seat back"
        and "seat_row" in o
        and not o.hide_render
    ]
    panels = [
        o for o in scene.objects if o.get("reconstruction_kind") == "Balustrade panel"
    ]
    if not seatbacks or not panels:
        return {}, []
    groups = defaultdict(list)
    for o in seatbacks:
        center = np.mean([list(o.matrix_world @ v.co) for v in o.data.vertices], axis=0)
        groups[(o["source_frame"], o["seat_row"])].append(center)
    observed = defaultdict(int)
    sampled = []
    indices = np.linspace(0, len(track) - 1, min(7, len(track))).round().astype(int)
    sources = sorted({key[0] for key in groups})
    checkpoints = [(f, None) for f in sources]
    checkpoints.extend(track[i] for i in indices)
    for f, actor in checkpoints:
        scene.frame_set(f + 1)
        deps = __import__("bpy").context.evaluated_depsgraph_get()
        view = _view(cam, deps, width, height)
        lines = []
        for o in panels:
            if o.hide_render:
                continue
            front = np.array(
                [list(o.matrix_world @ v.co) for v in list(o.data.vertices)[:4]]
            )
            bound, z = _screen(front, view)
            if np.min(z) <= 0.02:
                continue
            a, b = bound[3], bound[2]
            if b[0] - a[0] < 0.09 * width:
                continue
            lines.append((a, b))

        def floor_edge(x):
            edges = [
                a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
                for a, b in lines
                if a[0] - width * 0.10 <= x <= b[0] + width * 0.10
            ]
            return min(edges) if edges else None

        if actor is not None:
            box = np.array(actor["partial_body_track"]["visible_bbox"])
            point = (box[:2] + box[2:]) * 0.5
            edge = floor_edge(point[0])
            if edge is None or point[1] < edge + 0.03 * height:
                continue
            sample = _trace(scene, deps, point, view)
            if sample is None or "seat_row" not in sample[0]:
                continue
            sampled.append(view)
        for tag, points in groups.items():
            pix, z = _screen(np.array(points), view)
            local = [
                xy
                for xy, d in zip(pix, z)
                if d > 0
                and -0.12 * width <= xy[0] <= 1.12 * width
                and -0.10 * height <= xy[1] <= 1.22 * height
            ]
            evidence = [
                xy[1] - edge for xy in local if (edge := floor_edge(xy[0])) is not None
            ]
            if evidence and np.percentile(evidence, 25) > 0.026 * height:
                observed[tag] += 1
    accepted = {
        k: v
        for k, v in groups.items()
        if observed[k] >= min(2, len(sampled)) and observed[k] > 0
    }
    # Large dais and ground-floor banks are sometimes disjoint in the image.
    # Extend the witnessed tier over connected elevations, bounded by the gallery.
    if accepted and panels:
        up = []
        for panel in panels:
            verts = [
                np.asarray(panel.matrix_world @ v.co) for v in panel.data.vertices[:4]
            ]
            up.append(_unit((verts[0] + verts[1]) - (verts[2] + verts[3])))
        vertical = _unit(np.median(up, axis=0))
        elevations = {
            tag: float(np.median(np.asarray(pts) @ vertical))
            for tag, pts in groups.items()
        }
        base = max(elevations[tag] for tag in accepted)
        levels = sorted(set(elevations.values()))
        split = next(
            (
                (a + b) * 0.5
                for a, b in zip(levels, levels[1:])
                if b >= base and b - a > 0.48 and a >= base - 0.16
            ),
            None,
        )
        if split is not None:
            accepted = {
                tag: pts for tag, pts in groups.items() if elevations[tag] < split
            }
    return accepted, sampled


def _mean_bone(rig, names):
    pts = [
        np.asarray(rig.matrix_world @ rig.pose.bones[n].head)
        for n in names
        if n in rig.pose.bones
    ]
    return np.mean(pts, axis=0) if pts else None


def _source_shoulder(actor, box):
    partial = actor["partial_body_track"]
    size = box[2:] - box[:2]
    for point in (partial.get("shoulder_uv"), _pair(actor, "shoulder")):
        if point is None:
            continue
        uv = np.asarray(point, dtype=float)
        if (
            uv.shape == (2,)
            and np.all(uv >= box[:2] - size * 0.18)
            and np.all(uv <= box[2:] + size * 0.18)
        ):
            return uv
    return box[:2] + size * [0.48, 0.27]


def _measure(scene, cam, rig, actor, frame, width, height):
    import bpy

    scene.frame_set(frame + 1)
    deps = bpy.context.evaluated_depsgraph_get()
    view = _view(cam, deps, width, height)
    box = np.asarray(actor["partial_body_track"]["visible_bbox"], float)
    wh = box[2:] - box[:2]
    if min(wh) < 4:
        return None
    source_shoulder = _source_shoulder(actor, box)
    loc = _mean_bone(rig, ["DEF-upper_arm.L", "DEF-upper_arm.R"])
    if loc is None:
        return None
    pixel, z = _screen([loc], view)
    shoulders = np.array(
        [
            list(rig.matrix_world @ rig.pose.bones[n].head)
            for n in ("DEF-upper_arm.L", "DEF-upper_arm.R")
        ]
    )
    shoulder_sep = np.linalg.norm(
        _screen(shoulders, view)[0][1] - _screen(shoulders, view)[0][0]
    )
    real_sh = [
        actor.get("joints2d", {}).get(s + "_shoulder", {}) for s in ("left", "right")
    ]
    desired = None
    if all(
        p.get("u") is not None and p.get("v") is not None and p.get("score", 0) > 0.29
        for p in real_sh
    ):
        sep = np.linalg.norm(
            np.array(
                [real_sh[0]["u"] - real_sh[1]["u"], real_sh[0]["v"] - real_sh[1]["v"]]
            )
        )
        if sep >= wh[0] * 0.20 and shoulder_sep > 3:
            desired = z[0] * shoulder_sep / sep
    upper, lower = [], []
    for side, rows in ((upper, (0.12, 0.31, 0.48)), (lower, (0.76, 0.96, 1.10))):
        for y in rows:
            for x in (0.22, 0.48, 0.75):
                hit = _trace(scene, deps, box[:2] + wh * [x, y], view)
                if hit is not None and "seat_row" in hit[0]:
                    side.append(hit[1])
    if not upper and not lower:
        return None
    return dict(
        f=frame,
        view=view,
        box=box,
        loc=loc,
        root=np.asarray(rig.location).copy(),
        uv=source_shoulder,
        upper=upper,
        lower=lower,
        desired=desired,
        shoulder_z=float(z[0]),
    )


def _stable_series(data, key):
    values = np.array(data, dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return None
    times = np.asarray(key)
    filled = np.interp(times, times[finite], values[finite])
    return np.array([np.median(filled[np.abs(times - t) <= 6]) for t in times])


def _visible_seats(scene, camera, frames, members, width, height):
    import bpy
    from mathutils import Vector

    result = {}
    names = {o.name for o in members}
    for f in frames:
        scene.frame_set(int(f) + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        view = _view(camera, deps, width, height)
        points = []
        for v in np.linspace(height / 64, height - height / 64, 32):
            for u in np.linspace(width / 112, width - width / 112, 56):
                direction = _unit(_back(np.array([u, v]), 1, view) - view[0])
                hit, pt, _, _, ob, _ = scene.ray_cast(
                    deps, Vector(view[0]), Vector(direction)
                )
                if hit and ob.name in names:
                    points.append(np.asarray(pt))
        if points:
            result[int(f)] = (view, np.array(points))
    return result


def _visibility_counts(scene, references, center, scale, names):
    import bpy

    counts = {}
    for f, (view, pts) in references.items():
        scene.frame_set(f + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        seen = 0
        pixels, _ = _screen(center + (pts - center) * scale, view)
        for uv in pixels:
            first = _trace(scene, deps, uv, view)
            seen += int(first is not None and first[0].name in names)
        counts[int(f)] = (int(seen), len(pts))
    return counts


def _visibility_ratio(scene, references, center, scale, names):
    counts = _visibility_counts(scene, references, center, scale, names)
    return min((seen / n for seen, n in counts.values()), default=0.0)


def _retains_seat_witnesses(counts):
    # Resolve the 95.5% criterion to a whole ray per frame. A sparse frame
    # must still retain every observed ray when losing one would cost >10%.
    return bool(counts) and all(
        n > 0 and seen >= max(int(np.ceil(0.90 * n)), int(np.floor(0.955 * n)))
        for seen, n in counts.values()
    )


def indices_for_track(count):
    return np.linspace(0, count - 1, min(5, count)).round().astype(int)


def _size_gain(ratios, proposal):
    ratios = np.asarray(ratios, dtype=float)
    ratios = ratios[np.isfinite(ratios) & (ratios > 0)]
    if len(ratios) < 5:
        return {"registered": 0.0, "proposed": 0.0}
    errors = np.log(ratios)
    return {
        "registered": float(np.median(abs(errors))),
        "proposed": float(np.median(abs(errors - np.log(proposal)))),
    }


def _place_actor(scene, camera, rig, track, measured, width, height):
    import bpy
    from mathutils import Vector

    times = [m["f"] for m in measured]
    # Upper clothing visibly interrupts the back row. Front seating should
    # continue to hide the lower legs. Depth is estimated between those surfaces.
    values = [np.percentile(m["upper"], 23) if m["upper"] else np.nan for m in measured]
    z = _stable_series(values, times)
    if z is None:
        return []
    z = z - np.clip(z * 0.018, 0.085, 0.24)
    anchors = np.array([_back(m["uv"], d, m["view"]) for m, d in zip(measured, z)])
    center_world = np.median(anchors, axis=0)
    guide = np.array(
        [(center_world - m["view"][0]) @ m["view"][1][:, 2] for m in measured]
    )
    z = guide + np.clip(z - guide, -0.20, 0.20)
    records = []
    lookup = {f: m for f, m in zip(times, measured)}
    for f, a in track:
        m = lookup.get(f)
        if m is None:
            scene.frame_set(f + 1)
            v = _view(camera, bpy.context.evaluated_depsgraph_get(), width, height)
            loc = _mean_bone(rig, ["DEF-upper_arm.L", "DEF-upper_arm.R"])
            if loc is None:
                continue
            bbox = np.asarray(a["partial_body_track"]["visible_bbox"])
            uv = _source_shoulder(a, bbox)
            m = {
                "root": np.asarray(rig.location).copy(),
                "loc": loc,
                "uv": uv,
                "view": v,
            }
        else:
            scene.frame_set(f + 1)
        depth = float(np.interp(f, times, z))
        rig.location = Vector(m["root"] + _back(m["uv"], depth, m["view"]) - m["loc"])
        rig.keyframe_insert(data_path="location", frame=f + 1)
        records.append({"frame": f, "shoulder_depth_m": round(depth, 4)})
    return records


def fit_lower_tier(scene, camera, people, binding_info, *, source_config=None):
    """Keep genuine partial-body tracks at their observed occluding chair row."""
    import bpy
    from mathutils import Matrix, Vector

    width, height = people["width"], people["height"]
    tracks = defaultdict(list)
    for f in people["frames"]:
        for actor in f["actors"]:
            state = actor.get("partial_body_track", {})
            if (
                actor.get("renderable", True)
                and state.get("association") == "shot_local_source_extension"
                and len(state.get("visible_bbox", [])) == 4
            ):
                tracks[str(f["shot_id"]), str(actor["track_id"])].append(
                    (f["source_frame"], actor)
                )
    initial = scene.frame_current
    results = {}
    previous_tiers = set()
    try:
        for name in binding_info.get("editable_tracks", {}).values():
            rig = bpy.data.objects.get(name)
            if rig is None:
                continue
            track = tracks.get((str(rig.get("shot_id")), str(rig.get("track_id"))), [])
            if len(track) < 8:
                continue
            groups, views = _context(scene, camera, track, width, height)
            if not groups or len(views) < 2:
                continue
            measured = [
                m
                for f, a in track
                if (m := _measure(scene, camera, rig, a, f, width, height)) is not None
            ]
            if len(measured) < 8:
                continue
            ratios = []
            for m in measured:
                ref = m["upper"] or m["lower"]
                if m["desired"] is not None and ref and m["desired"] > 0:
                    ratios.append(m["desired"] / np.median(ref))
            center = np.median([v[0] for v in views], axis=0)
            scale = 1.0
            if len(ratios) >= 5:
                scale = float(np.clip(np.median(ratios), 1.0, 1.65))
            # A single rigid transform preserves every chair and inter-seat spacing over time.
            # Limit reprojection drift of visible seats to the measured chair's own extent.
            pts = np.vstack(list(groups.values()))
            candidate = scale
            for _ in range(7):
                errors = []
                for v in views:
                    orig, z = _screen(pts, v)
                    warped, _ = _screen(center + (pts - center) * candidate, v)
                    keep = (
                        (z > 0)
                        & (orig[:, 0] >= 0)
                        & (orig[:, 0] <= width)
                        & (orig[:, 1] >= 0)
                        & (orig[:, 1] <= height)
                    )
                    if keep.any():
                        errors.extend(np.linalg.norm((orig - warped)[keep], axis=1))
                if not errors or np.percentile(errors, 90) < width * 0.009:
                    break
                candidate = 1 + (candidate - 1) * 0.78
            scale = candidate
            move = (
                Matrix.Translation(Vector(center))
                @ Matrix.Scale(scale, 4)
                @ Matrix.Translation(Vector(-center))
            )
            group_set = set(groups)
            members = [
                o
                for o in scene.objects
                if "seat_row" in o
                and (o.get("source_frame"), o["seat_row"]) in group_set
                and o.parent is None
            ]
            # The generic room floor is an inferred enclosure, distinct from
            # source-measured landings and walls. Keep its contact with the
            # seating platforms when resolving the lower storey's monocular scale.
            scene.frame_set(track[0][0] + 1)
            floors = [
                o
                for o in scene.objects
                if not o.hide_render
                and o.get("reconstruction_kind") == "Room floor"
                and not o.get("source_observations")
                and o.parent is None
            ]
            seats = list(members)
            if source_config is None:
                members.extend(floors)
            originals = {o.name: o.matrix_world.copy() for o in members}
            names = {o.name for o in seats}
            checks = sorted(
                set(
                    [
                        min(k[0] for k in groups),
                        *[track[k][0] for k in indices_for_track(len(track))],
                    ]
                )
            )
            accepted_visibility = 1.0
            static_evidence = {"state": "no_expansion_requested"}
            if scale > 1.015 and source_config and not names & previous_tiers:
                from .fullclip_tier_parallax import measure

                static_evidence = measure(
                    scene, camera, people, track, seats, source_config, center, scale
                )
                if static_evidence["state"] != "source_supports_expansion":
                    # Compare both depths against the observed partial body's size.
                    # A near seat bank in front of the visible upper body is infeasible,
                    # even when its stationary chairs explain parallax more closely.
                    size_gain = _size_gain(ratios, scale)
                    static_evidence["relative_size_log_error"] = size_gain
                    if size_gain["proposed"] + 0.12 < size_gain["registered"]:
                        static_evidence["selection"] = (
                            "observed_actor_size_requires_deeper_rows"
                        )
                    else:
                        scale = 1.0
                        static_evidence["selection"] = "registered_rows"
            if names & previous_tiers:
                scale = 1.0
            partition = {}
            trials = []
            if scale > 1.015:
                from .fullclip_tier_floor import build, discard

                visible_seats = seats
                if source_config:
                    physical_parts = {
                        "Seat back",
                        "Seat cushion",
                        "Seat back frame",
                        "Seat armrest",
                    }
                    visible_seats = [
                        o
                        for o in seats
                        if o.get("reconstruction_kind") in physical_parts
                    ]
                witness_names = {o.name for o in visible_seats}
                checks = _visible_seats(
                    scene, camera, checks, visible_seats, width, height
                )
                first = int(track[0][0])
                scene.frame_set(first + 1)
                row_members, origins = [], []
                if source_config and floors:
                    shot_id = people["frames"][first]["shot_id"]
                    shot_frames = [
                        f["source_frame"]
                        for f in people["frames"]
                        if f["shot_id"] == shot_id
                    ]
                    row_members = [
                        o
                        for o in scene.objects
                        if "seat_row" in o and not o.hide_render
                    ]
                    for f in np.linspace(
                        shot_frames[0], shot_frames[-1], min(33, len(shot_frames))
                    ).round():
                        scene.frame_set(int(f) + 1)
                        origins.append(
                            np.asarray(camera.matrix_world.translation).copy()
                        )
                    scene.frame_set(first + 1)
                floor_matrices = {o.name: o.matrix_world.copy() for o in floors}
                # Evaluate each proposed row depth with exactly its own final floor geometry.
                for _ in range(8):
                    scene.frame_set(first + 1)
                    move = (
                        Matrix.Translation(Vector(center))
                        @ Matrix.Scale(scale, 4)
                        @ Matrix.Translation(Vector(-center))
                    )
                    for o in members:
                        o.matrix_world = move @ originals[o.name]
                    bpy.context.view_layer.update()
                    children = []
                    accepted = False
                    try:
                        if row_members:
                            children, partition = build(
                                scene, floors, row_members, center, scale, origins
                            )
                        counts = {}
                        if source_config:
                            counts = _visibility_counts(
                                scene, checks, center, scale, witness_names
                            )
                            accepted_visibility = min(
                                (seen / n for seen, n in counts.values()), default=0.0
                            )
                            accepted = _retains_seat_witnesses(counts)
                        else:
                            accepted_visibility = _visibility_ratio(
                                scene, checks, center, scale, names
                            )
                            accepted = accepted_visibility >= 0.955
                        trials.append(
                            {
                                "scale": round(scale, 5),
                                "minimum_visible_fraction": round(
                                    accepted_visibility, 5
                                ),
                                "visible_seat_rays_by_source_frame": counts,
                                "partition_parts": len(children),
                                "accepted": accepted,
                            }
                        )
                    finally:
                        if not accepted:
                            discard(children)
                            for o in floors:
                                o.matrix_world = floor_matrices[o.name]
                            for o in members:
                                o.matrix_world = originals[o.name]
                            partition = {}
                            bpy.context.view_layer.update()
                    if accepted:
                        previous_tiers.update(names)
                        break
                    scale = 1 + (scale - 1) * 0.65
                else:
                    scale, accepted_visibility = 1.0, 1.0
            else:
                scale = 1.0
            if scale > 1:
                measured = [
                    m
                    for f, a in track
                    if (m := _measure(scene, camera, rig, a, f, width, height))
                    is not None
                ]
            if not measured:
                continue
            records = _place_actor(scene, camera, rig, track, measured, width, height)
            if not records:
                continue
            from .fullclip_contact import fit_contact

            contact = fit_contact(scene, camera, rig, track, width, height, _view)
            results[str(rig["track_id"])] = {
                "inferred_contact": contact,
                "frames": records,
                "tier_scale": round(scale, 5),
                "static_seat_parallax": static_evidence,
                "static_floor_partition": partition,
                "static_scale_trials": trials,
                "preserved_bank_visibility": round(accepted_visibility, 5),
                "seat_assemblies": len(groups),
                "rigid_parts": len(members),
                "datum": "source_partial_body_appearance_and_registered_seat_visibility",
            }
    finally:
        scene.frame_set(initial)
    return results
