"""Measure a broad foreground fascia terminating visible lower-body silhouettes."""

import numpy as np


def observe_profile(image, rail, actors, detector):
    import cv2

    h, w = image.shape[:2]
    scale = min(1.0, 900 / w)
    gray = cv2.cvtColor(cv2.resize(image, None, fx=scale, fy=scale), cv2.COLOR_BGR2GRAY)
    detected = detector.detect(gray)[0]
    if detected is None:
        return None
    seg = detected[:, 0].astype(float) / scale
    lo = np.minimum(seg[:, 0], seg[:, 2])
    hi = np.maximum(seg[:, 0], seg[:, 2])
    dx = seg[:, 2] - seg[:, 0]
    slopes = (seg[:, 3] - seg[:, 1]) / np.where(abs(dx) > 0.01, dx, 1.0)
    offsets = seg[:, 1] - slopes * seg[:, 0]
    keep = (
        (hi - lo > 0.034 * w)
        & (np.abs(slopes) < 0.85)
        & (lo < rail["x_max"])
        & (hi > rail["x_min"])
    )
    lo, hi, slopes, offsets = [q[keep] for q in (lo, hi, slopes, offsets)]
    if not len(lo):
        return None
    boxes = {a["track_id"]: a["bbox"] for a in actors if "bbox" in a}
    crossings = [c for c in rail["crossings"] if c["track_id"] in boxes]
    if not crossings:
        return None
    cx = np.asarray([c["u"] for c in crossings])
    radii = np.asarray([max(4.0, c["core_halfwidth"]) for c in crossings])
    body = np.asarray([boxes[c["track_id"]] for c in crossings])
    body_h = body[:, 3] - body[:, 1]
    r_y = rail["slope"] * cx + rail["intercept"]
    mid = float(np.median(cx))
    xs = np.arange(1, w - 1, max(1, round(w / 430)))
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype("float32")
    upper = []
    lower = []
    for i in np.argsort(lo - hi):
        m, b = slopes[i], offsets[i]
        v = m * cx + b
        distances = v - r_y
        if np.sum((distances > np.maximum(7, 0.04 * body_h)) & (distances < 1.4 * body_h)) == 0:
            continue
        group = (abs(slopes - m) < 0.024) & (
            abs((slopes - m) * (lo + hi) * 0.5 + offsets - b) < max(3.0, h * 0.005)
        )
        a, z = lo[group].min(), hi[group].max()
        if z - a < w * 0.19 or z < cx.min() - w * 0.10 or a > cx.max() + w * 0.10:
            continue
        # Use in-frame rows on the tracked member's own side of any corner.
        valid = (xs >= max(rail["x_min"], a)) & (xs <= min(rail["x_max"], z))
        yy = m * xs + b
        valid &= (yy > 7) & (yy < h - 8)
        if valid.sum() < max(12, 0.13 * len(xs)):
            continue
        ys = np.rint(yy).astype(int)
        above = lab[(ys - 5).clip(0, h - 1), xs]
        below = lab[(ys + 5).clip(0, h - 1), xs]
        delta = np.linalg.norm(below - above, axis=1)
        supported = delta > 10
        edge_fraction = float(np.mean(supported[valid]))
        if edge_fraction < 0.56:
            continue
        cover = np.zeros_like(xs, dtype=bool)
        for x1, x2 in zip(lo[group], hi[group]):
            cover |= (xs >= x1) & (xs <= x2)
        if np.mean(cover[valid]) < 0.37:
            continue
        observed = dict(slope=float(m), intercept=float(b), x_min=float(a), x_max=float(z))
        witness = []
        for j, c in enumerate(crossings):
            exterior = (xs < body[j, 0] - radii[j]) | (xs > body[j, 2] + radii[j])
            if np.sum(exterior & valid & cover & supported) < max(4, 0.035 * len(xs)):
                continue
            if not (
                0.04 * body_h[j] < distances[j] < 0.46 * body_h[j]
                and body[j, 3] - 0.23 * body_h[j] < v[j] < body[j, 3] + 0.08 * body_h[j]
            ):
                continue
            core = (abs(xs - cx[j]) < radii[j]) & (yy > 6) & (yy < h - 7)
            if core.sum() < 2 or np.mean(supported[core]) < 0.66:
                continue
            # An upper rim has one near-side material along its span; the actor
            # terminates on the far side, causing an asymmetric colour transition.
            wing = (abs(xs - cx[j]) > 1.8 * radii[j]) & (abs(xs - cx[j]) < 4 * radii[j]) & valid
            if not wing.any():
                continue
            ac = np.median(above[core], axis=0)
            bc = np.median(below[core], axis=0)
            far = np.median(np.linalg.norm(above[wing] - ac, axis=1))
            near = np.median(np.linalg.norm(below[wing] - bc, axis=1))
            if near < max(20.0, far * 0.85) and np.median(delta[core]) > 16:
                witness.append(c["track_id"])
        score = float(edge_fraction + 0.35 * (z - a) / w)
        if witness:
            observed["witnesses"] = witness
            upper.append((float(m * mid + b), score, observed))
        if z - a > 0.27 * w and edge_fraction > 0.70:
            lower.append((float(m * mid + b), score, observed))
    if not upper:
        return None
    # Several trim bands can continue behind the actor. The first continuous
    # cross-body termination below the rail is the observable upper edge.
    upper.sort(key=lambda r: r[0])
    y, _, top = upper[0]
    rg = y - (rail["slope"] * mid + rail["intercept"])
    candidates = [
        x
        for x in lower
        if 1.35 * rg < x[0] - y < 6 * rg and abs(x[2]["slope"] - top["slope"]) < 0.65
    ]
    profile = {"top": top, "post_u": post_observations(seg, rail, top, w, actors)}
    lip = [
        x
        for x in lower
        if 0.25 * rg < x[0] - y < 0.95 * rg and abs(x[2]["slope"] - top["slope"]) < 0.24
    ]
    if lip:
        profile["lip"] = min(lip, key=lambda x: x[0])[2]
    if candidates:
        _, _, base = max(candidates, key=lambda x: x[0])
        profile["bottom"] = base
    return profile


def fit_edge(samples, rail_ends, name, height):
    """Consensus fit of one straight profile parallel to the independently fitted rail."""
    if __package__:
        from .fullclip_guardrail_fit import line_pixel
    else:
        from fullclip_guardrail_fit import line_pixel
    observations = [s for s in samples if name in s["row"].get("panel_profile", {})]
    if len(observations) < 15:
        return None
    axis = rail_ends[1] - rail_ends[0]
    axis = axis / np.linalg.norm(axis)
    normal_basis = np.linalg.svd(axis[None, :], full_matrices=True)[2][1:]
    origin = rail_ends.mean(axis=0)
    p = np.array([s["projection"] for s in observations])
    rows = [s["row"]["panel_profile"][name] for s in observations]
    uv = np.array(
        [[r["x_min"] * 0.8 + r["x_max"] * 0.2, r["x_min"] * 0.2 + r["x_max"] * 0.8] for r in rows]
    )
    vv = np.array([r["slope"] * u + r["intercept"] for r, u in zip(rows, uv)])
    rays = np.repeat(p, 2, axis=0)
    x = uv.ravel()
    y = vv.ravel()
    projected_axis = rays @ np.r_[axis, 0.0]
    b = projected_axis[:, 0] - x * projected_axis[:, 2]
    if (abs(b) < 1.0e-8).any():
        return None
    q = (rays[:, 1] - y[:, None] * rays[:, 2]) - (rays[:, 0] - x[:, None] * rays[:, 2]) * (
        (projected_axis[:, 1] - y * projected_axis[:, 2]) / b
    )[:, None]
    a = q[:, :3] @ normal_basis.T
    target = -(q @ np.r_[origin, 1.0])

    # Evaluate each row in its own projected depth. This preserves near and far
    # image evidence rather than weighting the far wall by its world distance.
    def residual(params):
        o = origin + params @ normal_basis
        py, depth, _ = line_pixel(o, axis, rays, x)
        return np.where(depth > 0, abs(py - y), np.inf).reshape(-1, 2).max(axis=1)

    stride = np.unique(np.linspace(0, len(rows) - 1, min(len(rows), 32), dtype=int))
    best = None
    limit = height * 0.016
    for i in stride:
        for j in stride[stride > i]:
            ix = [2 * i, 2 * i + 1, 2 * j, 2 * j + 1]
            par = np.linalg.lstsq(a[ix], target[ix], rcond=1e-6)[0]
            er = residual(par)
            ok = er < limit
            if not ok.any():
                continue
            score = (int(ok.sum()), -float(np.median(er[ok])))
            if best is None or score > best[0]:
                best = (score, par)
    if best is None:
        return None
    par = best[1]
    for _ in range(3):
        errors = residual(par)
        inside = errors < limit
        if inside.sum() < max(12, len(rows) * 0.3):
            return None
        sel = np.repeat(inside, 2)
        _, z, _ = line_pixel(origin + par @ normal_basis, axis, rays, x)
        scale = np.maximum(z[sel], 0.001)
        par = np.linalg.lstsq(a[sel] / scale[:, None], target[sel] / scale, rcond=1e-6)[0]
    err = residual(par)
    good = err < limit
    if good.sum() < max(12, len(rows) * 0.30):
        return None
    line_origin = origin + par @ normal_basis
    limits = []
    for edge in ("x_min", "x_max"):
        col = np.array([r[edge] for r in rows])
        _, depth, distance = line_pixel(line_origin, axis, p, col)
        valid = good & (depth > 0) & np.isfinite(distance)
        limits.append(distance[valid])
    if min(map(len, limits)) < 12:
        return None
    orientation = float(np.sign(np.median(limits[1]) - np.median(limits[0])))
    if not orientation:
        return None
    ts = (
        np.array(
            [
                np.percentile(limits[0] * orientation, 3),
                np.percentile(limits[1] * orientation, 97),
            ]
        )
        * orientation
    )
    return {
        "ends": line_origin + ts[:, None] * axis,
        "views": int(good.sum()),
        "total": len(rows),
        "inlier_median_px": float(np.median(err[good])),
        "all_median_px": float(np.median(err)),
        "offset": par @ normal_basis,
    }


def post_observations(segments, rail, top, image_width, people):
    """Find observed narrow upright edges bridging the rail and the fascia."""
    found = []
    bits = []
    for x, y, xx, yy in segments:
        if y > yy:
            x, xx, y, yy = xx, x, yy, y
        if yy - y < 7 or abs(xx - x) > 0.28 * (yy - y):
            continue
        center = (x + xx) * 0.5
        if not rail["x_min"] <= center <= rail["x_max"]:
            continue
        if any(
            a["bbox"][0] < center < a["bbox"][2] and a["bbox"][1] < 0.5 * (yy + y) < a["bbox"][3]
            for a in people
            if "bbox" in a
        ):
            continue
        upper = rail["slope"] * center + rail["intercept"]
        lower = top["slope"] * center + top["intercept"]
        gap = lower - upper
        if gap < 10 or yy < upper or y > lower:
            continue
        dy = (xx - x) / (yy - y)
        mid = x + (0.5 * (upper + lower) - y) * dy
        bits.append((mid, dy, (y - upper) / gap, (yy - upper) / gap, gap))
    levels = np.linspace(0.08, 0.94, 14)
    for mid, slope, y, yy, gap in bits:
        supporting = [
            (lo, hi)
            for x, grad, lo, hi, g in bits
            if abs(mid - x) < max(3.8, gap * 0.065) and abs(slope - grad) < 0.22
        ]
        cover = np.any(
            [(levels > lo - 0.045) & (levels < hi + 0.045) for lo, hi in supporting],
            axis=0,
        )
        if cover.mean() < 0.60 or not cover[:4].any() or not cover[-4:].any():
            continue
        x = mid - 0.5 * gap * slope
        if all(abs(x - prior) > image_width * 0.005 for prior in found):
            found.append(float(x))
    return found


def observed_posts(samples, rail, thickness, width):
    if __package__:
        from .fullclip_guardrail_fit import line_pixel
    else:
        from fullclip_guardrail_fit import line_pixel
    start, end = rail
    di = end - start
    length = np.linalg.norm(di)
    bins = []
    for s in samples:
        us = s["row"].get("panel_profile", {}).get("post_u", [])
        if not us:
            continue
        _, _, ts = line_pixel(start, di, s["projection"], np.asarray(us))
        bins.extend((float(t), s["row"]["frame"]) for t in ts if 0.01 < t < 0.99)
    if len(bins) < 8:
        return []
    tol = max(thickness * 2 / length, 0.006)
    accepted = []
    for t, frame in sorted(bins):
        group = [r for r in bins if abs(r[0] - t) < tol]
        unique = {r[1] for r in group}
        if len(unique) < max(7, 0.055 * len(samples)) or max(unique) - min(unique) < 12:
            continue
        accepted.append((len(unique), float(np.median([r[0] for r in group]))))
    posts = []
    for n, t in sorted(accepted, reverse=True):
        if all(abs(t - p["fraction"]) > tol * 3 for p in posts):
            posts.append(dict(fraction=t, views=n))
    return sorted(posts, key=lambda p: p["fraction"])
