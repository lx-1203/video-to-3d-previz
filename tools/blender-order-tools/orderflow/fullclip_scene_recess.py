"""Reconstruct bounded, persistent recesses from their source edges and metric depth."""

import cv2
import numpy as np


def _overlap(a, b):
    lo = np.maximum(a[:2], b[:2])
    hi = np.minimum(a[2:], b[2:])
    area = np.prod(np.maximum(0, hi - lo))
    return area / max(1, np.prod(a[2:] - a[:2]) + np.prod(b[2:] - b[:2]) - area)


def _regions(image, occupied):
    gray = cv2.cvtColor(cv2.GaussianBlur(image, (3, 3), 0), cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    result = []
    if np.ptp(gray) < 18:
        return result
    for threshold in np.unique(np.percentile(gray, [9, 16, 23, 29, 35, 40, 45]).astype(int)):
        dark = cv2.morphologyEx(
            (gray < threshold).astype("uint8"), cv2.MORPH_OPEN, np.ones((3, 3), "uint8")
        )
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, a, b = cv2.boundingRect(contour)
            if (
                not 0.05 * w < a < 0.56 * w
                or not 0.067 * h < b < 0.6 * h
                or min(a / b, b / a) < 0.22
            ):
                continue
            area = cv2.contourArea(contour) / (a * b)
            if area < 0.71 or np.mean(occupied[y : y + b, x : x + a]) > 0.12:
                continue
            left, right = x, x + a - 1
            ya, yb = y + round(b * 0.27), y + round(b * 0.75)
            contrasts = []
            pad = max(3, round(a * 0.055))
            if x > pad + 1:
                contrasts.append(
                    float(np.median(gray[ya:yb, x - pad - 1 : x - 1]))
                    - float(np.median(gray[ya:yb, x + 2 : x + pad + 2]))
                )
            if right < w - 2 - pad:
                contrasts.append(
                    float(np.median(gray[ya:yb, right + 2 : right + pad + 2]))
                    - float(np.median(gray[ya:yb, right - pad - 1 : right - 1]))
                )
            if not contrasts or min(contrasts) < 6 or np.mean(contrasts) < 13:
                continue
            coords = np.array([left, y, right, y + b - 1], float)
            result.append((area + min(50.0, np.mean(contrasts)) * 0.004, coords))
    kept = []
    for _, box in sorted(result, key=lambda v: -v[0]):
        if not any(_overlap(box, prev) > 0.45 for prev in kept):
            kept.append(box)
    return kept


def _project(uv, z, camera, intr, size):
    dirs = np.column_stack(((uv / size - intr[:2, 2]) / intr.diagonal()[:2], np.ones(len(uv))))
    return (dirs * np.asarray(z).reshape(-1, 1)) @ camera[:3, :3].T + camera[:3, 3]


def _screen(points, camera, intr, size):
    q = (points - camera[:3, 3]) @ camera[:3, :3]
    uv = (q[:, :2] / np.maximum(0.001, q[:, 2:]) * intr.diagonal()[:2] + intr[:2, 2]) * size
    return uv, q[:, 2]


def _quad(box):
    x, y, u, v = box
    return np.array([[x, y], [u, y], [u, v], [x, v]], float)


def _fit_plane(box, depth):
    x, y, u, v = np.rint(box).astype(int)
    h, w = depth.shape
    width, height = u - x, v - y
    if x <= 2 or u >= w - 3:
        return None
    cols = np.r_[
        np.linspace(max(0, x - width * 0.23), x - 2, 8),
        np.linspace(u + 2, min(w - 1, u + width * 0.23), 8),
    ]
    xx, yy = np.meshgrid(cols, np.linspace(y + height * 0.15, v - height * 0.15, 10))
    design = np.column_stack((xx.ravel() / w, yy.ravel() / h, np.ones(xx.size)))
    samples = depth[np.rint(yy).astype(int), np.rint(xx).astype(int)].ravel()
    ok = np.isfinite(samples) & (samples > 0)
    if ok.sum() < design.shape[0] * 0.70:
        return None
    for _ in range(3):
        coef = np.linalg.lstsq(design[ok], 1 / samples[ok], rcond=None)[0]
        residual = np.abs(1 / samples - design @ coef)
        ok &= residual < max(0.004 / samples[ok].mean(), np.median(residual[ok]) * 3)

    def plane(uv):
        design = np.column_stack((uv / [w, h], np.ones(len(uv))))
        return 1 / np.maximum(0.001, design @ coef)

    face = plane(_quad(box))
    inner = depth[y + height // 4 : v - height // 4, x + width // 4 : u - width // 4]
    inner = inner[np.isfinite(inner) & (inner > 0)]
    if not len(inner) or np.ptp(face) > np.median(face) * 0.68:
        return None
    inset = float(np.median(inner) - np.median(face))
    if inset < np.median(face) * 0.012 or np.median(face) < np.nanpercentile(depth, 55):
        return None
    return (
        plane,
        float(np.clip(inset, np.median(face) * 0.023, np.median(face) * 0.16)),
        inset,
    )


def _slab(kind, uv, front, back, frame, witnesses, gray):
    a, b = front(uv), back(uv)
    return {
        "kind": kind,
        "vertices": np.vstack((a, b)).round(6).tolist(),
        "faces": [
            [3, 2, 1, 0],
            [4, 5, 6, 7],
            [0, 1, 5, 4],
            [1, 2, 6, 5],
            [2, 3, 7, 6],
            [3, 0, 4, 7],
        ],
        "tone": 3,
        "rgb": [gray] * 3,
        "source_frame": frame,
        "source_observations": witnesses,
        "observation_status": "bounded contrasting aperture with multiview edge and metric depth support",
    }


def _closure_seam(frames, regions, cameras, intr, size, world, witnesses):
    measurements = []
    for f in witnesses:
        uv, _ = _screen(world, cameras[f], intr, size)
        predicted = np.r_[uv.min(0), uv.max(0)]
        box = max(regions[f], key=lambda obs: _overlap(predicted, obs))
        x, y, u, v = np.rint(box).astype(int)
        if x < 2 or u >= size[0] - 2:
            continue
        width, height = u - x, v - y
        image = cv2.cvtColor(frames[f], cv2.COLOR_BGR2GRAY).astype(float)
        strip = image[y + int(height * 0.24) : y + int(height * 0.79), x : u + 1]
        sides = max(3, round(width * 0.045))
        candidates = []
        for k in range(round(width * 0.34), round(width * 0.66)):
            contrast = (
                np.minimum(
                    np.median(strip[:, k - sides : k - 1], axis=1),
                    np.median(strip[:, k + 2 : k + sides + 1], axis=1),
                )
                - strip[:, k]
            )
            median = float(np.median(contrast))
            if median >= 1.5 and np.mean(contrast >= 1.5) > 0.53:
                candidates.append((median, k / width))
        if candidates:
            measurements.append((f, max(candidates)[1]))
    if len(measurements) < 3:
        return None, []
    ratio = float(np.median([p[1] for p in measurements]))
    evidence = [f for f, r in measurements if abs(r - ratio) < 0.035]
    if len(evidence) < 3 or max(evidence) - min(evidence) < 12:
        return None, []
    return ratio, evidence


def _opening(box, plane, inset, frame, observations, camera, intr, size, seam):
    x, y, u, v = box
    width, height = u - x, v - y
    if y < 2:
        y -= height * 0.45

    z0 = np.median(plane(_quad(box)))

    def surface(d):
        return lambda uv: _project(uv, plane(uv) + d, camera, intr, size)

    objects = []

    def slab(kind, bounds, start, end, color):
        if bounds[2] - bounds[0] > 0.1 and bounds[3] - bounds[1] > 0.1:
            objects.append(_slab(kind, _quad(bounds), start, end, frame, observations, color))

    trim = max(1.1, min(width, height) * 0.040)
    for bounds in (
        [x, y, x + trim, v],
        [u - trim, y, u, v],
        [x + trim, y, u - trim, y + trim],
        [x + trim, v - trim, u - trim, v],
    ):
        slab(
            "Recess reveal jamb",
            bounds,
            surface(-z0 * 0.005),
            surface(inset + z0 * 0.008),
            0.39,
        )
    # Project the rear closure to the observed silhouette from the measuring view.
    if seam is None:
        slab(
            "Recess backing",
            [x, y, u, v],
            surface(inset),
            surface(inset + z0 * 0.017),
            0.23,
        )
    else:
        middle = x + width * seam
        half_gap = max(0.45, width * 0.008)
        for bounds in ([x, y, middle - half_gap, v], [middle + half_gap, y, u, v]):
            slab(
                "Recess backing leaf",
                bounds,
                surface(inset),
                surface(inset + z0 * 0.017),
                0.23,
            )
        slab(
            "Recess joint backing",
            [middle - half_gap, y, middle + half_gap, v],
            surface(inset + z0 * 0.026),
            surface(inset + z0 * 0.04),
            0.13,
        )
    return objects


def recover(frames, views, scales, cameras, intr, boxes, original, structures=()):
    first, last = min(frames), max(frames)
    h, w = frames[first].shape[:2]
    size = np.array([w, h])
    times = sorted(set(views) | set(range(first, last + 1, 6)))
    regions = {}
    for f in times:
        mask = np.zeros((h, w), bool)
        for bounds in boxes.get(f, []):
            x, y, u, v = np.rint(np.asarray(bounds) * np.tile(size / original, 2)).astype(int)
            mask[max(0, y) : v + 1, max(0, x) : u + 1] = True
        regions[f] = _regions(frames[f], mask)
    proposals = []
    for f in sorted(views):
        depth = cv2.resize(views[f], (w, h)) * scales[f]
        for box in regions[f]:
            fitted = _fit_plane(box, depth)
            if fitted is None:
                continue
            plane, inset, raw = fitted
            uv = _quad(box)
            world = _project(uv, plane(uv), cameras[f], intr, size)
            witnesses = []
            scores = []
            for t, choices in regions.items():
                projection, z = _screen(world, cameras[t], intr, size)
                if z.min() <= 0:
                    continue
                bounds = np.r_[
                    np.clip(projection.min(0), [0, 0], size - 1),
                    np.clip(projection.max(0), [0, 0], size - 1),
                ]
                score = max((_overlap(bounds, other) for other in choices), default=0.0)
                if score > 0.61:
                    witnesses.append(t)
                    scores.append(score)
            if len(witnesses) < 3 or max(witnesses) - min(witnesses) < 12:
                continue
            depth_witnesses = []
            for t in sorted(set(views).intersection(witnesses)):
                projected, _ = _screen(world, cameras[t], intr, size)
                predict = np.r_[projected.min(0), projected.max(0)]
                matched = max(regions[t], key=lambda reg: _overlap(predict, reg))
                other_depth = cv2.resize(views[t], (w, h)) * scales[t]
                measured = _fit_plane(matched, other_depth)
                if measured is not None:
                    depth_witnesses.append(t)
            if len(depth_witnesses) < 2:
                continue
            priority = len(witnesses) + np.mean(scores) * 2 + (box[1] > 2) * 1.4
            proposals.append(
                (priority, f, box, world, plane, inset, raw, witnesses, depth_witnesses)
            )
    kept, objects, proof, records = [], [], [], []
    for _, f, box, world, plane, inset, raw, witnesses, depth_witnesses in sorted(
        proposals, key=lambda p: -p[0]
    ):
        duplicate = False
        for old in kept:
            uv, z = _screen(old, cameras[f], intr, size)
            if z.min() > 0 and _overlap(box, np.r_[uv.min(0), uv.max(0)]) > 0.36:
                duplicate = True
                break
        if duplicate:
            continue
        seam, seam_frames = _closure_seam(frames, regions, cameras, intr, size, world, witnesses)
        parts = _opening(box, plane, inset, f, witnesses, cameras[f], intr, size, seam)
        for part in parts:
            part["visible_first"], part["visible_last"] = first, last
        objects.extend(parts)
        kept.append(world)
        proof.append(
            {
                "anchor": f,
                "box": box.tolist(),
                "observations": witnesses,
                "raw_recess_depth": raw,
                "modeled_recess_depth": inset,
                "depth_views": depth_witnesses,
                "closure_split": seam,
                "closure_edge_frames": seam_frames,
            }
        )
        extended = box.copy()
        if extended[1] < 2:
            extended[1] -= (extended[3] - extended[1]) * 0.45
        corners = _quad(extended)
        records.append(
            {
                "corners": _project(corners, plane(corners), cameras[f], intr, size),
                "camera": cameras[f][:3, 3],
                "proof": proof[-1],
            }
        )
    for aperture in proof:
        lower, evidence = _landing(
            frames,
            views,
            scales,
            cameras,
            intr,
            aperture["anchor"],
            aperture["box"],
            structures,
            aperture["observations"],
        )
        if lower:
            objects.extend(lower)
            aperture["lower_landing"] = evidence
            break
    wall = _continuous_walls(records, structures, objects)
    for piece in wall:
        piece["visible_first"], piece["visible_last"] = first, last
    objects.extend(wall)
    return objects, proof


def _floor_breaks(depth, left, right, low, high):
    """Locate a wall/floor junction by the slope change in inverse optical depth."""
    h, w = depth.shape
    before = max(7, int(h * 0.075))
    after = max(10, int(h * 0.10))
    samples = []
    for x in range(max(2, int(left)), min(w - 2, int(right)), 3):
        stripe = np.median(depth[:, x - 1 : x + 2], axis=1)
        choices = []
        for y in range(max(before, int(low)), min(int(high), h - after), 2):
            values = stripe[y - before : y + after]
            if not np.isfinite(values).all() or values.min() <= 0:
                continue
            z = float(np.median(values[:before]))
            ix = np.arange(-before, after, dtype=float)
            profile = z / values - 1
            flat = np.median(profile[:before])
            decline = (profile[-1] - profile[before]) * h / after
            if (
                decline < 0.40
                or np.ptp(profile[:before]) > 0.030
                or z < np.nanpercentile(stripe, 53)
            ):
                continue
            predictor = np.maximum(0, ix) / h
            slope = np.dot(predictor, profile - flat) / np.dot(predictor, predictor)
            residual = float(np.mean(np.abs(profile - flat - predictor * slope)))
            choices.append((residual / max(0.4, slope), y, z, slope))
        if choices:
            residual, y, z, slope = min(choices)
            if residual < 0.007:
                samples.append((x, y, z, slope))
    return np.array(samples)


def _landing(frames, views, scales, cameras, intr, reference, box, items, witnesses):
    image = frames[reference]
    h, w = image.shape[:2]
    size = np.array([w, h])
    depth = cv2.resize(views[reference], (w, h)) * scales[reference]
    camera = cameras[reference]
    for panel in items:
        if panel["kind"] != "Balustrade panel" or not panel.get("shared_corner"):
            continue
        uv, zz = _screen(np.array(panel["vertices"])[:4], camera, intr, size)
        if zz.min() <= 0 or np.ptp(uv[:2, 0]) < w * 0.19 or uv[1, 0] <= uv[0, 0]:
            continue
        xmid = (box[0] + box[2]) * 0.5
        if (
            not uv[0, 0] < xmid < uv[1, 0]
            or abs(uv[1, 1] - uv[0, 1]) > abs(uv[1, 0] - uv[0, 0]) * 0.28
        ):
            continue
        ybase = np.interp(xmid, uv[:2, 0], uv[:2, 1])
        if abs(ybase - box[3]) > h * 0.10:
            continue
        lower = uv[[3, 2]]
        ymin = max(lower[:, 1].min() + h * 0.08, box[3] + h * 0.20)
        all_points = _floor_breaks(depth, uv[0, 0], uv[1, 0], ymin, h * 0.88)
        if len(all_points) < 10:
            continue
        runs = np.split(
            all_points,
            np.where(
                (np.diff(all_points[:, 0]) > 6)
                | (np.abs(np.diff(all_points[:, 1])) > h * 0.016)
                | (np.abs(np.diff(all_points[:, 2])) > np.median(all_points[:, 2]) * 0.09)
            )[0]
            + 1,
        )
        run = max(runs, key=len)
        if len(run) < 10 or np.ptp(run[:, 0]) < w * 0.09:
            continue
        cx, cy, d, _ = run.T
        line = np.polyfit(cx, cy, 1)
        if abs(line[0]) > 0.28 or np.median(np.abs(np.polyval(line, cx) - cy)) > h * 0.023:
            continue
        z_line = np.polyfit(cx, 1 / d, 1)
        xy = np.column_stack((cx, np.polyval(line, cx)))
        world = _project(xy, 1 / np.polyval(z_line, cx), camera, intr, size)
        verified = []
        for f in sorted(views):
            other, z = _screen(world, cameras[f], intr, size)
            px, py = np.rint(other).astype(int).T
            valid = (px > 2) & (px < w - 3) & (py > h * 0.08) & (py < h * 0.92) & (z > 0)
            if valid.sum() < 6:
                continue
            yy = py[valid]
            xx = px[valid]
            native = cv2.resize(views[f], (w, h)) * scales[f]
            ds = native[yy, xx]
            yd = max(3, round(h * 0.055))
            above = native[yy - yd, xx]
            below = native[yy + yd, xx]
            congruent = (np.abs(ds / z[valid] - 1) < 0.16) & (below < above * 0.985)
            if congruent.mean() > 0.50:
                verified.append(f)
        if len(verified) < 2:
            continue
        specimen = frames[reference]
        gray = cv2.createCLAHE(3.0, (8, 8)).apply(cv2.cvtColor(specimen, cv2.COLOR_BGR2GRAY))
        edges = cv2.createLineSegmentDetector().detect(gray)[0]
        if edges is None:
            continue
        steps = []
        for x, y, u, v in edges[:, 0]:
            if x > u:
                x, u, y, v = u, x, v, y
            if u - x < max(12, np.ptp(cx) * 0.20) or abs(v - y) > 0.20 * (u - x):
                continue
            if min(u, cx.max()) - max(x, cx.min()) < 0.45 * (u - x):
                continue
            gap = (y + v) * 0.5 - np.polyval(line, (x + u) * 0.5)
            if h * 0.055 < gap < h * 0.23:
                steps.append((gap, u - x))
        if not steps:
            continue
        gap, _ = max(steps, key=lambda p: p[1])
        endpoints = np.array([cx.min(), cx.max()])
        bottom = np.polyval(line, endpoints)
        # The wall itself continues behind the front balustrade, beyond the open aisle.
        span = np.clip(uv[:2, 0], -w * 0.15, w * 1.2)
        wall_z = np.clip(1 / np.polyval(z_line, span), np.median(d) * 0.85, np.median(d) * 1.15)
        wall_top = np.interp(span, lower[:, 0], lower[:, 1]) - h * 0.025
        wall_bottom = np.polyval(line, span)
        wall_uv = np.array(
            [
                [span[0], wall_top[0]],
                [span[1], wall_top[1]],
                [span[1], wall_bottom[1]],
                [span[0], wall_bottom[0]],
            ]
        )
        profile = np.r_[wall_z, wall_z[::-1]]

        def front(u):
            return _project(u, profile, camera, intr, size)

        def back(u):
            return _project(u, profile + np.median(d) * 0.025, camera, intr, size)

        wall = _slab("Lower return wall", wall_uv, front, back, reference, verified, 0.54)
        far = np.column_stack((endpoints, bottom))
        near = far.copy()
        near[:, 1] = np.minimum(h - 2, near[:, 1] + gap)
        floor_uv = np.vstack((far, near[::-1]))
        near_x, near_y = np.rint(near).astype(int).T
        floor_z = np.r_[1 / np.polyval(z_line, endpoints), depth[near_y, near_x][::-1]]
        if np.min(floor_z) <= 0 or not np.isfinite(floor_z).all():
            continue
        surface = _project(floor_uv, floor_z, camera, intr, size)
        under = _project(floor_uv + [0, max(1, gap * 0.13)], floor_z, camera, intr, size)
        floor = _slab(
            "Lower landing platform",
            floor_uv,
            lambda u: surface,
            lambda u: under,
            reference,
            verified,
            0.50,
        )
        for o in (wall, floor):
            o["visible_first"], o["visible_last"] = min(frames), max(frames)
            o["observation_status"] = (
                "wall-floor inverse-depth slope change verified in multiple views; first visible nosing"
            )
        return [wall, floor], {
            "reference": reference,
            "depth_views": verified,
            "floor_break_extent": far.round(2).tolist(),
            "platform_near_edge": near.round(2).tolist(),
            "wall_depth": wall_z.round(3).tolist(),
        }
    return [], {}


def _unit(vector):
    return vector / max(1e-9, np.linalg.norm(vector))


def _continuous_walls(records, structures, lower):
    """Connect observed jambs through a shared room corner, leaving real holes in the walls."""
    if not records:
        return []
    groups = []
    for rec in records:
        corners = rec["corners"]
        direction = _unit(corners[1] - corners[0])
        up = _unit(corners[0] - corners[3])
        normal = _unit(np.cross(direction, up))
        group = next((g for g in groups if abs(np.dot(normal, g["normal"])) > 0.84), None)
        if group is None:
            group = {"normal": normal, "records": []}
            groups.append(group)
        group["records"].append(rec)
    up = _unit(np.mean([_unit(r["corners"][0] - r["corners"][3]) for r in records], axis=0))
    if len(groups) == 2:
        vertical = _unit(np.cross(groups[0]["normal"], groups[1]["normal"]))
        if abs(np.dot(vertical, up)) > 0.78:
            up = vertical * np.sign(np.dot(vertical, up))
    extent = np.vstack(
        [
            np.array(o["vertices"])
            for o in structures
            if o["kind"] in {"Seat back", "Balustrade panel", "Room wall"}
        ]
        or [r["corners"] for r in records]
    )
    radius = max(1, np.linalg.norm(np.ptp(extent, axis=0)))
    seen = np.vstack([r["corners"] for r in records])
    top = max((extent @ up).max(), (seen @ up).max()) + radius * 0.15
    floor = min((extent @ up).min(), (seen @ up).min()) - radius * 0.06
    for group in groups:
        members = group["records"]
        axis = _unit(np.cross(up, group["normal"]))
        if np.dot(axis, members[0]["corners"][1] - members[0]["corners"][0]) < 0:
            axis = -axis
        group["axis"] = axis
        group["normal"] = _unit(np.cross(axis, up))
        group["center"] = np.mean([r["corners"].mean(0) for r in members], axis=0)
        group["records"].sort(key=lambda r: r["corners"].mean(0) @ axis)
    wall = []
    for g in groups:
        axis, center, normal = g["axis"], g["center"], g["normal"]
        members = g["records"]
        bottom = floor
        if any("lower_landing" in r["proof"] for r in members):
            wall_under = next((o for o in lower if o["kind"] == "Lower return wall"), None)
            if wall_under:
                bottom = np.max(np.asarray(wall_under["vertices"]) @ up) - radius * 0.007
        depth = radius * 0.0025

        def height(p, level):
            return p + (level - np.dot(p, up)) * up

        def connect(a, b, lo, hi):
            return np.array([height(a, hi), height(b, hi), height(b, lo), height(a, lo)])

        def member(quad, rec):
            front = np.asarray(quad)
            n = np.cross(front[1] - front[0], front[2] - front[0])
            if np.linalg.norm(n) < 1.0e-7:
                return
            n = _unit(n)
            if np.dot(n, rec["camera"] - front.mean(0)) < 0:
                n = -n
            # _slab expects top-left, top-right, bottom-right, bottom-left in the source view.
            if np.dot(np.cross(front[1] - front[0], front[2] - front[0]), n) > 0:
                front = front[::-1]
            objects = _slab(
                "Recess wall surround",
                None,
                lambda _: front,
                lambda _: front - depth * n,
                rec["proof"]["anchor"],
                rec["proof"]["observations"],
                0.57,
            )
            objects["observation_status"] = (
                "continuous wall between measured reveals; shared corner or inferred exterior extent"
            )
            wall.append(objects)

        left = members[0]["corners"][0]
        right = members[-1]["corners"][1]
        # Outside the measured bays, a wall has support for at most one local
        # bay of continuation. A room-size extrapolation can intersect another tier.
        widths = [np.linalg.norm(r["corners"][1] - r["corners"][0]) for r in members]
        centers = [r["corners"].mean(0) @ axis for r in members]
        bay = max(np.median(widths), np.median(np.diff(centers)) if len(centers) > 1 else 0.0)
        outer = [left - axis * bay, right + axis * bay]
        for other in groups:
            if g is other:
                continue
            den = np.dot(other["normal"], axis)
            if abs(den) < 0.35:
                continue
            delta = np.dot(other["normal"], other["center"] - center) / den
            intersection = center + axis * delta
            if abs(delta) > radius * 1.2:
                continue
            x = intersection @ axis
            if x < (left @ axis) - radius * 0.008:
                outer[0] = intersection
            elif x > (right @ axis) + radius * 0.008:
                outer[1] = intersection
        # A measured return across a gallery bounds the wall above it. Extrapolating
        # to a nominal intersection behind a different bank can cut through its occupants.
        if any("lower_landing" in r["proof"] for r in members):
            base = next((o for o in lower if o["kind"] == "Lower return wall"), None)
            if base is not None:
                edge = np.asarray(base["vertices"])[:2]
                edge = edge[np.argsort(edge @ axis)]
                if edge[0] @ axis < left @ axis:
                    outer[0] = edge[0]
                if edge[1] @ axis > right @ axis:
                    outer[1] = edge[1]
        member(connect(outer[0], left, bottom, top), members[0])
        for i, rec in enumerate(members):
            tl, tr, br, bl = rec["corners"]
            member([height(tl, top), height(tr, top), tr, tl], rec)
            if min(bl @ up, br @ up) > bottom:
                member([bl, br, height(br, bottom), height(bl, bottom)], rec)
            if i:
                prev = members[i - 1]["corners"][1]
                member(connect(prev, tl, bottom, top), rec)
        member(connect(right, outer[1], bottom, top), members[-1])
    return wall
