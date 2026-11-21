"""Fit a continuous gallery walking surface to upright tracks in a reconstructed set."""

from collections import defaultdict
import numpy as np


def surface_from_soles(locations, height):
    points = np.asarray(locations, float)
    if len(points) < 8:
        return None
    center = np.median(points, axis=0)
    xy = points[:, :2] - center[:2]
    q = np.quantile(np.linalg.norm(xy, axis=1), 0.97)
    good = np.linalg.norm(xy, axis=1) <= q
    points = points[good]
    xy = xy[good]
    _, _, dirs = np.linalg.svd(xy, full_matrices=False)
    along = dirs[0]
    across = np.array([-along[1], along[0]])
    path = xy @ along
    lateral = xy @ across
    lo, hi = np.quantile(path, [0.015, 0.985])
    if hi - lo < 0.45 * height:
        return None
    # Aisle geometry is inferred where feet are occluded. A bounded grade keeps
    # noisy depth from turning a level occupied floor into an implausible wall.
    design = np.column_stack([xy, np.ones(len(xy))])
    z = points[:, 2]
    keep = np.ones(len(z), bool)
    for _ in range(4):
        slope = np.linalg.lstsq(design[keep], z[keep], rcond=None)[0][:2]
        slope *= min(1.0, 0.35 / max(np.linalg.norm(slope), 1.0e-8))
        intercept = np.median(z - xy @ slope)
        residual = z - (xy @ slope + intercept)
        mad = np.median(abs(residual))
        keep = abs(residual) < max(0.10 * height, 2.8 * mad)
        if sum(keep) < 4:
            return None
    s0, s1 = np.quantile(lateral, [0.025, 0.975])
    s0 -= 0.42 * height
    s1 += 0.42 * height
    lo -= 0.8 * height
    hi += 0.8 * height
    local = np.array([along * a + across * b for a, b in [(lo, s0), (hi, s0), (hi, s1), (lo, s1)]])
    verts = np.column_stack([local + center[:2], local @ slope + intercept - 0.012 * height])
    return verts, dict(
        samples=len(points),
        median_abs_foot_height_error=float(np.median(abs(residual))),
        grade=float(np.linalg.norm(slope)),
        length=float(hi - lo),
        width=float(s1 - s0),
    )


def fit_walkway(scene, manifest, people, binding, height):
    import bpy
    from .fullclip_scene import _visible_during

    rigs = [scene.objects[v] for v in binding["editable_tracks"].values()]
    rigs = {(r["shot_id"], r["track_id"]): r for r in rigs}
    original_frame = scene.frame_current
    report = []
    for shot in manifest["shots"]:
        first, last = shot["first"], shot["last"]
        # An occupied elevated gallery needs both architectural evidence and sustained
        # upright tracks. Single-person close-ups supply neither condition on their own.
        original = [
            o
            for o in scene.objects
            if o.get("reconstruction_kind") == "Gallery walkway"
            and first <= o["source_frame"] <= last
        ]
        if not original:
            continue
        feet = defaultdict(list)
        for frame in people["frames"][first : last + 1 : 3]:
            scene.frame_set(frame["source_frame"] + 1)
            for actor in frame["actors"]:
                anchors = actor.get("anchors", {})
                sh = anchors.get("shoulder", {})
                hp = anchors.get("hip", {})
                if min(hp.get("score", 0), sh.get("score", 0)) < 0.40:
                    continue
                if hp["v"] - sh["v"] < max(abs(sh["u"] - hp["u"]), 0.10 * people["height"]):
                    continue
                rig = rigs.get((frame["shot_id"], actor["track_id"]))
                if rig is None:
                    continue
                er = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
                soles = [
                    er.matrix_world @ er.pose.bones["DEF-shin." + side].tail for side in ("L", "R")
                ]
                low = min(soles, key=lambda v: v.z)
                feet[actor["track_id"]].append(tuple(low))
        n = (last - first + 1) / 3
        tracks = [values for values in feet.values() if len(values) >= max(4, 0.30 * n)]
        if len(tracks) < 2:
            continue
        fit = surface_from_soles([pt for seq in tracks for pt in seq], height)
        if fit is None:
            continue
        top, metrics = fit
        if metrics["median_abs_foot_height_error"] > 0.20 * height:
            continue
        bottom = top.copy()
        bottom[:, 2] -= 0.12 * height
        mesh = bpy.data.meshes.new("Gallery floor from tracked contact")
        mesh.from_pydata(
            np.vstack([top, bottom]).tolist(),
            [],
            [[0, 1, 2, 3], [7, 6, 5, 4], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]],
        )
        mesh.update()
        obj = bpy.data.objects.new("Gallery walking surface", mesh)
        original[0].users_collection[0].objects.link(obj)
        obj["reconstruction_kind"] = "Occupied gallery deck"
        obj["estimated_support"] = True
        obj["source_frame"] = first
        _visible_during(
            obj, first, last, manifest["shots"][0]["first"], manifest["shots"][-1]["last"]
        )
        report.append(dict(first=first, last=last, tracks=len(tracks), **metrics))
    scene.frame_set(original_frame)
    return report
