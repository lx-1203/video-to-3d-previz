"""Cross-validate a proposed static seating scale with measured source parallax."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np


def check_scale(errors, groups, times, proposal, width):
    """Scores contain pixel errors for reference and proposed scale, by time and feature."""
    errors = np.asarray(errors, dtype=float)
    groups = np.asarray(groups)
    baseline, proposed = errors
    train = np.arange(len(times)) < len(times) // 2
    report = {"proposal": float(proposal), "state": "insufficient_static_witnesses"}
    minimum = max(0.5, width * 0.0006)
    for label, split in (("fit", train), ("held_out", ~train)):
        # A source view with many features on a single seat cannot stand in for a room.
        keep = np.isfinite(baseline[split]) & np.isfinite(proposed[split])
        present = keep.any(axis=0)
        objects = len(set(groups[present]))
        if present.sum() < 12 or objects < 3 or sum(keep.any(axis=1)) < 2:
            return report
        a, b = baseline[split][keep], proposed[split][keep]
        report[label] = {
            "frames": [int(f) for f in np.asarray(times)[split]],
            "features": int(present.sum()),
            "chair_parts": objects,
            "median_reference_px": round(float(np.median(a)), 4),
            "median_proposed_px": round(float(np.median(b)), 4),
            "p90_reference_px": round(float(np.quantile(a, 0.9)), 4),
            "p90_proposed_px": round(float(np.quantile(b, 0.9)), 4),
        }
    report["state"] = "source_supports_expansion"
    for k in ("fit", "held_out"):
        m = report[k]
        if m["median_reference_px"] - m["median_proposed_px"] < minimum or (
            m["p90_proposed_px"] > m["p90_reference_px"] + minimum
        ):
            report["state"] = "retain_registered_geometry"
    return report


def _tracks(source, boxes, times, region):
    import cv2

    cap = cv2.VideoCapture(str(source))
    records = []
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, times[0])
        prev = points = active = None
        for f in range(times[0], times[-1] + 1):
            ok, image = cap.read()
            if not ok:
                return None
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            mask = np.zeros_like(gray)
            x, y, r, b = region
            mask[y:b, x:r] = 255
            for rect in boxes.get(str(f), []):
                x, y, r, b = np.rint(rect).astype(int)
                cv2.rectangle(mask, (x - 3, y - 3), (r + 3, b + 3), 0, -1)
            if prev is None:
                points = cv2.goodFeaturesToTrack(gray, 900, 0.004, 5, mask=mask)
                if points is None or len(points) < 12:
                    return None
                active = np.ones(len(points), bool)
            else:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, gray, points, None)
                if nxt is None:
                    return None
                back, st2, _ = cv2.calcOpticalFlowPyrLK(gray, prev, nxt, None)
                if back is None:
                    return None
                active &= st.ravel().astype(bool) & st2.ravel().astype(bool)
                active &= np.linalg.norm(back[:, 0] - points[:, 0], axis=1) < 0.85
                points = nxt
                pix = np.rint(points[:, 0]).astype(int)
                h, w = gray.shape
                active &= (
                    (pix[:, 0] >= 0)
                    & (pix[:, 0] < w)
                    & (pix[:, 1] >= 0)
                    & (pix[:, 1] < h)
                )
                # The initial region bounds seed features. Later camera motion may
                # carry stationary chairs outside it; only mask actors on later frames.
                for rect in boxes.get(str(f), []):
                    x, y, r, b = rect
                    active &= ~(
                        (pix[:, 0] >= x - 3)
                        & (pix[:, 0] <= r + 3)
                        & (pix[:, 1] >= y - 3)
                        & (pix[:, 1] <= b + 3)
                    )
            if f in times:
                row = points[:, 0].copy()
                row[~active] = np.nan
                records.append(row)
            prev = gray
    finally:
        cap.release()
    return np.array(records)


def measure(scene, camera, people, track, members, cfg, center, proposal):
    import bpy
    from mathutils import Vector
    from .fullclip_lower_tier import _back, _screen, _view

    width, height = people["width"], people["height"]
    times = sorted(
        set(int(f) for f in np.linspace(track[0][0], track[-1][0], 7).round())
    )
    fallback = {"state": "insufficient_static_witnesses", "proposal": float(proposal)}
    if len(times) < 6:
        return fallback
    views = []
    for f in times:
        scene.frame_set(f + 1)
        views.append(
            _view(camera, bpy.context.evaluated_depsgraph_get(), width, height)
        )
    scene.frame_set(times[0] + 1)
    dep = bpy.context.evaluated_depsgraph_get()
    kinds = {"Seat back", "Seat cushion", "Seat back frame"}
    objects = [
        o
        for o in members
        if o.get("reconstruction_kind") in kinds and not o.hide_render
    ]
    if len(objects) < 3:
        return fallback
    xy = []
    for o in objects:
        points = [o.matrix_world @ Vector(corner) for corner in o.bound_box]
        uv, z = _screen(points, views[0])
        if np.all(z > 0):
            xy.extend(uv)
    if not xy:
        return fallback
    low = np.clip(np.floor(np.min(xy, axis=0)), 0, [width, height]).astype(int)
    high = np.clip(np.ceil(np.max(xy, axis=0)), 0, [width, height]).astype(int)
    region = [*low.tolist(), *high.tolist()]
    boxes = {
        f: [
            a["bbox"]
            for a in people["frames"][f]["actors"]
            if len(a.get("bbox") or []) == 4
        ]
        for f in range(times[0], times[-1] + 1)
    }
    with tempfile.TemporaryDirectory() as tmp:
        params = Path(tmp) / "request.json"
        file = Path(tmp) / "features.npy"
        params.write_text(json.dumps([cfg["source"], boxes, times, region]), "utf8")
        subprocess.run(
            [
                cfg["media_python"],
                str(Path(__file__).resolve()),
                str(params),
                str(file),
            ],
            check=True,
        )
        if not file.is_file():
            return fallback
        tracked = np.load(file, allow_pickle=False)
    names = {o.name for o in objects}
    points, labels, ids = [], [], []
    origin = Vector(views[0][0])
    for i, pixel in enumerate(tracked[0]):
        direction = Vector(_back(pixel, 1.0, views[0])) - origin
        hit, pt, _, _, obj, _ = scene.ray_cast(dep, origin, direction.normalized())
        if hit and obj.name in names:
            points.append(tuple(pt))
            labels.append(obj.name)
            ids.append(i)
    if len(points) < 12:
        return fallback
    points = np.asarray(points)
    compare = []
    for factor in (1.0, proposal):
        moved = center + factor * (points - center)
        error = []
        for view, observed in zip(views[1:], tracked[1:, ids]):
            uv, depth = _screen(moved, view)
            diff = np.linalg.norm(uv - observed, axis=1)
            diff[depth <= 0] = np.nan
            error.append(diff)
        compare.append(error)
    return check_scale(compare, labels, times[1:], proposal, width)


if __name__ == "__main__":
    source, boxes, times, region = json.loads(Path(sys.argv[1]).read_bytes())
    measured = _tracks(source, boxes, times, region)
    if measured is not None:
        np.save(sys.argv[2], measured, allow_pickle=False)
