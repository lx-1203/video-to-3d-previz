"""Repair an inferred actor depth excursion with source face and static-seat ordering."""

import numpy as np


SEATING = {
    "Seat back",
    "Seat cushion",
    "Seat pedestal",
    "Seat armrest",
    "Seat row support",
    "Stepped seating platform",
}


def _runs(mask):
    padded = np.r_[False, mask, False]
    return zip(
        np.flatnonzero(~padded[:-1] & padded[1:]),
        np.flatnonzero(padded[:-1] & ~padded[1:]),
    )


def constrained_depth(depth, ppm, ceiling, witnessed):
    """A visible neighbour on each side fixes the local monocular body-size scale."""
    depth, ppm, ceiling = np.asarray(depth), np.asarray(ppm), np.asarray(ceiling)
    conflict = np.isfinite(ceiling)
    out = depth.copy()
    episodes = []
    # Merge a one-frame gap only if bounded by independent geometry conflicts.
    if len(conflict) > 2:
        conflict[1:-1] |= conflict[:-2] & conflict[2:]
    clean = np.asarray(witnessed) & ~conflict
    for lo, hi in _runs(conflict):
        if hi - lo < 2:
            continue
        left = np.flatnonzero(clean[:lo])
        right = hi + np.flatnonzero(clean[hi:])
        left = left[left >= lo - 16][-9:]
        right = right[right < hi + 16][:9]
        boundary = (lo == 0 and len(right) >= 5) or (hi == len(depth) and len(left) >= 5)
        if len(left) >= 2 and len(right) >= 2:
            basis = np.interp(
                np.arange(len(depth)),
                [np.median(left), np.median(right)],
                [
                    np.median(depth[left] * ppm[left]),
                    np.median(depth[right] * ppm[right]),
                ],
            )
        elif boundary:
            neighbors = right if lo == 0 else left
            constants = depth[neighbors] * ppm[neighbors]
            if np.percentile(constants, 80) > 1.12 * np.percentile(constants, 20):
                continue
            basis = np.full(len(depth), np.median(constants))
        else:
            continue
        guide = basis / np.maximum(ppm, 1.0e-6)
        # A seated actor behind a chair can be correct. Correct the actor only
        # if its measured scale actually departs from visible same-track peers.
        if np.median(depth[lo:hi] / guide[lo:hi]) < 1.075:
            continue
        a = max(0, lo - 4)
        b = min(len(depth), hi + 4)
        # The measured source scale varies slowly during a depth-order excursion.
        # Interpolate between actual visible boundary depths and retain the
        # smoothed change of body size relative to those same two boundaries.
        kernel = np.exp(-0.5 * (np.arange(-6, 7) / 3.0) ** 2)
        kernel /= kernel.sum()
        smoothed = np.convolve(np.pad(ppm, (6, 6), mode="edge"), kernel, mode="valid")
        n = b - a
        t = np.linspace(0.0, 1.0, n)
        size_weight = (1.0 - t) * smoothed[a] + t * smoothed[b - 1]
        base = ((1.0 - t) * depth[a] + t * depth[b - 1]) * (
            size_weight / np.maximum(smoothed[a:b], 1.0e-6)
        ) ** 0.35
        if boundary:
            base = basis[a:b] / np.maximum(smoothed[a:b], 1.0e-6)
        limits = ceiling[a:b] * 0.988
        # Carry a tighter isolated constraint into neighbouring frames with a
        # bounded approach, then regularize acceleration at the two joins.
        increment = 0.018 * float(np.median(base))
        for j in range(1, n):
            limits[j] = min(limits[j], limits[j - 1] + increment)
        for j in range(n - 2, -1, -1):
            limits[j] = min(limits[j], limits[j + 1] + increment)
        target = np.minimum(base, limits)
        target = np.maximum(target, 0.91 * base)
        D = np.diff(np.eye(n), n=2, axis=0)
        A = np.eye(n) + 7.0 * (D.T @ D)
        rhs = target.copy()
        for j in (0, n - 1):
            if boundary and ((j == 0 and lo == 0) or (j == n - 1 and hi == len(depth))):
                continue
            A[j, j] += 30.0
            rhs[j] += 30.0 * depth[a + j]
        fit = np.linalg.solve(A, rhs)
        out[a:b] = np.minimum(out[a:b], np.maximum(fit, 0.91 * base))
        episodes.append((a, b, lo, hi))
    return out, episodes


def direct_head(actor):
    """A face detection or geometrically consistent, directly estimated head landmarks."""
    head = actor.get("anchors", {}).get("head", {})
    if head.get("state") == "face_landmarks":
        return True
    joints = actor.get("joints2d", {})
    real = [joints.get(k, {}) for k in ("left_eye", "right_eye", "nose")]
    if not all(
        j.get("source") == "rtmpose"
        and j.get("score", 0) >= 0.55
        and j.get("visibility") in ("visible", "model_estimate", "observed")
        for j in real
    ):
        return False
    left, right, nose = [np.array([j["u"], j["v"]]) for j in real]
    eyes = (left + right) * 0.5
    width = np.linalg.norm(left - right)
    return bool(
        width >= 2
        and np.linalg.norm(eyes - nose) < 1.8 * width
        and head.get("score", 0) >= 0.45
        and np.linalg.norm(eyes - [head["u"], head["v"]]) < 2.5 * width
    )


def _observed(scene, rig, actor, camera, deps, width, height):
    from mathutils import Vector

    head = actor.get("anchors", {}).get("head", {})
    shoulder = actor.get("anchors", {}).get("shoulder", {})
    pm = actor.get("pixels_per_meter", 0.0)
    if not pm or head.get("score", 0) < 0.45 or not direct_head(actor):
        return None
    meshes = [
        o.evaluated_get(deps) for o in rig.children if o.type == "MESH" and not o.hide_render
    ]
    if not meshes:
        return None
    pose = camera.matrix_world.normalized()
    projection = camera.calc_matrix_camera(deps, x=width, y=height)
    inverse = projection.inverted()

    def ray(u, v):
        q = inverse @ Vector((2 * u / width - 1, 1 - 2 * v / height, 0, 1))
        return pose.to_quaternion() @ q.to_3d().normalized()

    pivot = np.array([head["u"], head["v"]])
    if shoulder.get("score", 0) > 0.45:
        pivot = 0.8 * pivot + 0.2 * np.array([shoulder["u"], shoulder["v"]])
    center = pose.translation
    axis = pose.to_quaternion() @ Vector((0, 0, -1))
    bone = rig.pose.bones.get("DEF-spine.006")
    if bone is None:
        return None
    head_center = rig.matrix_world @ ((bone.head + bone.tail) * 0.5)
    z = (head_center - center).dot(axis)
    if z <= camera.data.clip_start:
        return None
    own = {ob.original.name for ob in meshes}
    max_depth = []
    seen = 0
    confirmed = 0
    occluders = {}
    box = actor["bbox"]
    radius = max(2.0, 0.035 * (box[2] - box[0]))
    for feature, point in (("head", head), ("shoulder", shoulder)):
        if point.get("score", 0) < 0.45:
            continue
        for dx in (-radius, 0.0, radius):
            direction = ray(point["u"] + dx, point["v"])
            hit, loc, _, _, first, _ = scene.ray_cast(deps, center, direction)
            if not hit:
                continue
            if first.name in own:
                if feature == "head":
                    seen += 1
                continue
            kind = first.get("reconstruction_kind")
            if kind not in SEATING:
                continue
            target = []
            for ob in meshes:
                inv = ob.matrix_world.inverted()
                ok, pos, _, _ = ob.ray_cast(inv @ center, (inv.to_3x3() @ direction).normalized())
                if ok:
                    target.append((ob.matrix_world @ pos - center).dot(axis))
            if not target:
                continue
            mesh_depth = min(target)
            solid_depth = (loc - center).dot(axis)
            if solid_depth > mesh_depth - 0.01:
                continue
            if feature == "head":
                confirmed += 1
            max_depth.append(z + solid_depth - mesh_depth)
            occluders[first.name] = occluders.get(first.name, 0) + 1
    return dict(
        z=z,
        ppm=pm,
        ceiling=float(np.percentile(max_depth, 10))
        if confirmed >= 2 and len(max_depth) >= 3
        else np.inf,
        seen=seen >= 2,
        occluders=occluders,
        anchor=ray(*pivot) / ray(*pivot).dot(axis),
        location=rig.location.copy(),
    )


def fit_observed_actor_depth(
    scene, camera, people, binding_info, foreground_evidence=None, *, source_config=None
):
    import bpy

    rigs = {}
    for name in binding_info["editable_tracks"].values():
        rig = bpy.data.objects[name]
        rigs[(str(rig["shot_id"]), str(rig["track_id"]))] = rig
    tracks = {}
    initial = scene.frame_current
    try:
        for i, frame in enumerate(people["frames"]):
            eligible = [
                (a, rigs.get((str(frame["shot_id"]), str(a["track_id"]))))
                for a in frame["actors"]
                if direct_head(a)
            ]
            if not eligible:
                continue
            scene.frame_set(i + 1)
            deps = bpy.context.evaluated_depsgraph_get()
            cam = camera.evaluated_get(deps)
            for a, rig in eligible:
                if rig is None:
                    continue
                row = _observed(scene, rig, a, cam, deps, people["width"], people["height"])
                if row:
                    tracks.setdefault(rig.name, {})[i] = row
        result = {}
        for name, rows in tracks.items():
            rig = bpy.data.objects[name]
            indices = np.asarray(sorted(rows))
            details = []
            for group in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
                if len(group) < 7:
                    continue
                vals = [rows[int(i)] for i in group]
                props = [[v[k] for v in vals] for k in ("z", "ppm", "ceiling", "seen")]
                solved, episodes = constrained_depth(*props)
                for start, end, lo, hi in episodes:
                    edits = []
                    for n in range(start, end):
                        row = vals[n]
                        delta = solved[n] - row["z"]
                        if abs(delta) < 0.001:
                            continue
                        frame = int(group[n])
                        rig.location = row["location"] + float(delta) * row["anchor"]
                        rig.keyframe_insert(data_path="location", frame=frame + 1)
                        edits.append(
                            {
                                "frame": frame,
                                "depth_before": round(row["z"], 5),
                                "depth_after": round(float(solved[n]), 5),
                                "occluders": row["occluders"],
                            }
                        )
                    if edits:
                        details.append(
                            {
                                "first_observed": int(group[lo]),
                                "last_observed": int(group[hi - 1]),
                                "keys": edits,
                            }
                        )
            if details:
                result[name] = details
        if foreground_evidence is not None:
            originals = [
                (ob, ob.matrix_world.copy())
                for ob in scene.objects
                if ob.get("reconstruction_kind") in SEATING and ob.parent is None
            ]
            seat = refit_conflicting_seating(
                scene, camera, people, rigs, result, foreground_evidence
            )
            if source_config is not None and originals:
                if __package__:
                    from .fullclip_guardrail_material_bind import augment
                else:
                    from fullclip_guardrail_material_bind import augment
                extended = augment(scene, camera, people, rigs, foreground_evidence, source_config)
                if extended is not None and extended.get("material_coverage", {}).get(
                    "material_witnesses", 0
                ):
                    poses = [ob.matrix_world.copy() for ob, _ in originals]
                    for ob, old in originals:
                        ob.matrix_world = old
                    scene.view_layers[0].update()
                    final = refit_conflicting_seating(
                        scene, camera, people, rigs, result, extended
                    )
                    if final.get("applied"):
                        seat = final
                    else:
                        for (ob, _), pose in zip(originals, poses):
                            ob.matrix_world = pose
                        scene.view_layers[0].update()
                    seat["material_coverage"] = extended["material_coverage"]
            result["static_seating"] = seat
        return result
    finally:
        scene.frame_set(initial)


def torso_colour_witnesses(image, actors):
    """Source pixels which continue a visible upper garment down the torso."""
    import cv2

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    h, w = lab.shape[:2]
    out = {}

    def local(u, v, radius):
        x, y = int(round(u)), int(round(v))
        if x < radius or y < radius or x >= w - radius or y >= h - radius:
            return None
        return np.median(
            lab[y - radius : y + radius + 1, x - radius : x + radius + 1], axis=(0, 1)
        )

    for actor in actors:
        hpt = actor["anchors"].get("head", {})
        sh = actor["anchors"].get("shoulder", {})
        hip = actor["anchors"].get("hip", {})
        head_samples = []
        if "u" in hpt and "v" in hpt and direct_head(actor):
            hx, hy = round(hpt["u"]), round(hpt["v"])
            box = actor.get("bbox", [hx - 18, hy - 20, hx + 18, hy + 20])
            rr = max(2, min(7, round((box[2] - box[0]) * 0.035)))
            if rr <= hx < w - rr and rr <= hy < h - rr:
                patch = lab[hy - rr : hy + rr + 1, hx - rr : hx + rr + 1, 0]
                if np.percentile(patch, 90) - np.percentile(patch, 10) > 7:
                    head_samples = [
                        [round(hpt["u"] + dx * rr, 2), round(hpt["v"], 2), -1.0]
                        for dx in (-0.45, 0, 0.45)
                    ]
                    out[actor["track_id"]] = head_samples
        direct_torso = all(
            actor["joints2d"].get(side + "_" + joint, {}).get("source") == "rtmpose"
            and actor["joints2d"][side + "_" + joint].get("score", 0) >= 0.48
            for side in ("left", "right")
            for joint in ("shoulder", "hip")
        )
        if (
            not (direct_head(actor) or direct_torso)
            or min(sh.get("score", 0), hip.get("score", 0)) < 0.48
        ):
            continue
        sides = [actor["joints2d"].get(s + "_shoulder", {}) for s in ("left", "right")]
        if any(v.get("score", 0) < 0.45 for v in sides):
            continue
        span = abs(sides[0]["u"] - sides[1]["u"])
        if span < 10:
            continue
        a = np.array([sh["u"], sh["v"]])
        b = np.array([hip["u"], hip["v"]])
        v = b - a
        if np.linalg.norm(v) < 18 or v[1] < 0.65 * np.linalg.norm(v):
            continue
        r = max(2, min(5, round(span * 0.04)))
        chest = [
            local(*(a + t * v + [dx * span, 0]), r)
            for t in (0.16, 0.28, 0.40)
            for dx in (-0.21, 0, 0.21)
        ]
        front = np.array([x for x in chest if x is not None])
        if len(front) < 5:
            continue
        checks = []
        for t in (0.24, 0.48, 0.72, 0.91):
            mid = a + t * v
            flank = [local(*(mid + [span * dx, 0]), r) for dx in (-1.08, -0.82, 0.82, 1.08)]
            exterior = np.array([x for x in flank if x is not None])
            if len(exterior) < 2:
                continue
            for dx in (-0.17, 0, 0.17):
                xy = mid + [dx * span, 0]
                col = local(*xy, r)
                if col is None:
                    continue
                body_dist = float(np.percentile(np.linalg.norm(front - col, axis=1), 35))
                bg_dist = float(np.percentile(np.linalg.norm(exterior - col, axis=1), 50))
                if body_dist < 23 and bg_dist > max(15.0, 1.55 * body_dist + 6.0):
                    checks.append([round(float(xy[0]), 2), round(float(xy[1]), 2), round(t, 2)])
        if len(checks) >= 4 or head_samples:
            out[actor["track_id"]] = checks + head_samples
    return out


def _seat_witnesses(scene, camera, people, rigs, regions, pixels):
    """Compare source-supported garment pixels to exact evaluated visible surfaces."""
    import bpy
    from mathutils import Vector

    constraints = {}
    seen = {}
    for i, names in regions.items():
        swatches = pixels.get(str(i), {})
        if not swatches:
            continue
        scene.frame_set(i + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        cam = camera.evaluated_get(deps)
        mw = cam.matrix_world.normalized()
        inv = cam.calc_matrix_camera(deps, x=people["width"], y=people["height"]).inverted()
        center = mw.translation
        axis = mw.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        for name in names:
            rig = bpy.data.objects[name]
            samples = swatches.get(str(rig["track_id"]), [])
            if not samples:
                continue
            parts = [
                c.evaluated_get(deps)
                for c in rig.children
                if c.type == "MESH" and not c.hide_render
            ]
            own = {p.original.name for p in parts}
            for u, v, t in samples:
                q = inv @ Vector((2 * u / people["width"] - 1, 1 - 2 * v / people["height"], 0, 1))
                ray = mw.to_quaternion() @ q.to_3d().normalized()
                hit, pos, _, _, ob, _ = scene.ray_cast(deps, center, ray)
                if not hit:
                    continue
                key = (i, name, float(u), float(v))
                if ob.name in own:
                    seen[key] = "visible"
                    continue
                kind = ob.get("reconstruction_kind", "")
                if kind not in SEATING:
                    continue
                z = []
                for body in parts:
                    tr = body.matrix_world.inverted()
                    ok, co, _, _ = body.ray_cast(tr @ center, (tr.to_3x3() @ ray).normalized())
                    if ok:
                        z.append((body.matrix_world @ co - center).dot(axis))
                if not z:
                    continue
                source = ob.get("source_frame")
                if not isinstance(source, int):
                    continue
                if (
                    not 0 <= source < len(people["frames"])
                    or people["frames"][source]["shot_id"] != people["frames"][i]["shot_id"]
                ):
                    continue
                seen[key] = "occluded"
                constraints.setdefault(source, []).append(
                    (
                        i,
                        np.asarray(pos),
                        np.asarray(axis),
                        min(z) - (pos - center).dot(axis),
                        ob.name,
                    )
                )
    return seen, constraints


def refit_conflicting_seating(scene, camera, people, rigs, episodes, evidence):
    import json
    from pathlib import Path
    from mathutils import Matrix

    data = evidence if isinstance(evidence, dict) else json.loads(Path(evidence).read_bytes())
    if data.get("source_sha256") != people.get("source_sha256"):
        raise ValueError("Seat visibility and pose sources differ")
    pixels = data.get("observed_torso_cores", {})
    regions = {}
    for i, frame in enumerate(people["frames"]):
        swatches = pixels.get(str(i), {})
        for actor in frame["actors"]:
            track = str(actor["track_id"])
            rig = rigs.get((str(frame["shot_id"]), track))
            if track in swatches and rig is not None:
                regions.setdefault(i, set()).add(rig.name)
    seen, raw = _seat_witnesses(scene, camera, people, rigs, regions, pixels)
    metrics = {
        "source_visible_rays": len(seen),
        "before": sum(s == "visible" for s in seen.values()),
        "groups": {},
    }
    if not raw:
        return dict(metrics, after=metrics["before"])
    long_baseline = {
        k: len({r[0] for r in v}) >= 18 and len({r[4] for r in v}) >= 3 for k, v in raw.items()
    }
    factors = {}
    centers = {}
    originals = {}
    newly_hidden_frames = set()
    for iteration in range(5):
        changed = False
        for source, rows in raw.items():
            recover = (
                source in factors
                and long_baseline.get(source, False)
                and any(row[0] in newly_hidden_frames for row in rows)
            )
            if not recover and (len({row[0] for row in rows}) < 3 or len(rows) < 8):
                continue
            if source not in centers:
                scene.frame_set(source + 1)
                centers[source] = camera.matrix_world.translation.copy()
                originals[source] = [
                    (ob, ob.matrix_world.copy())
                    for ob in scene.objects
                    if ob.get("reconstruction_kind") in SEATING
                    and ob.get("source_frame") == source
                    and ob.parent is None
                ]
            pivot = centers[source]
            ratios = []
            for _, position, axis, gap, _ in rows:
                den = np.dot(position - np.asarray(pivot), axis)
                if den > 0.35 and gap > 0:
                    ratios.append(1.0 + (gap + 0.025) / den)
            if not ratios or (len(ratios) < 8 and not recover):
                continue
            # A few actors many rows behind the main subjects can create a
            # second, incompatible depth estimate. Fit the supported lower
            # cluster before considering an update to an entire physical bank.
            values = np.sort(np.asarray(ratios))
            gaps = np.flatnonzero(values[1:] / values[:-1] > 1.15) + 1
            breaks = [i for i in gaps if i >= max(8, len(values) * 0.32)]
            if breaks:
                values = values[: breaks[0]]
            factor = factors.get(source, 1.0) * float(np.percentile(values, 90))
            # Beyond a moderate monocular scale ambiguity the chair geometry
            # needs fresh local measurements. Do not delete or keyframe chairs.
            strong = long_baseline.get(source, False)
            max_factor = 1.65 if strong else 1.23
            if factor > max_factor:
                continue
            if factor < factors.get(source, 1.0) + 0.002:
                continue
            factor = max(factor, factors.get(source, 1.0))
            transform = (
                Matrix.Translation(pivot) @ Matrix.Scale(factor, 4) @ Matrix.Translation(-pivot)
            )
            for ob, original in originals[source]:
                ob.matrix_world = transform @ original
            factors[source] = factor
            metrics["groups"][str(source)] = {
                "factor": factor,
                "witness_frames": sorted({row[0] for row in rows}),
                "parts": len(originals[source]),
                "trigger_examples": sorted({row[-1] for row in rows})[:12],
            }
            changed = True
        if not changed:
            break
        scene.view_layers[0].update()
        updated, raw = _seat_witnesses(scene, camera, people, rigs, regions, pixels)
        newly_hidden_frames = {
            key[0]
            for key, status in updated.items()
            if status == "occluded" and seen.get(key) == "visible"
        }
        if not raw:
            break
    updated, _ = _seat_witnesses(scene, camera, people, rigs, regions, pixels)
    old_visible = {k for k, v in seen.items() if v == "visible"}
    newly_lost = sum(updated.get(k) == "occluded" for k in old_visible)
    new = sum(updated.get(k) == "visible" for k in seen)
    if newly_lost or new <= metrics["before"]:
        for source in factors:
            for ob, original in originals[source]:
                ob.matrix_world = original
        return dict(metrics, after=metrics["before"], applied=False, newly_lost=newly_lost)
    return dict(metrics, after=new, applied=True, newly_lost=newly_lost)
