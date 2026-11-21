"""Fit one rigid finite line to source image lines and evaluated body-depth constraints."""

import numpy as np


def line_pixel(org, axis, matrices, columns):
    a = matrices @ np.r_[org, 1.0]
    b = matrices @ np.r_[axis, 0.0]
    den = b[..., 0] - columns * b[..., 2]
    safe = np.where(np.abs(den) < 1e-8, np.where(den < 0, -1e-8, 1e-8), den)
    t = -(a[..., 0] - columns * a[..., 2]) / safe
    q = a + b * t[..., None]
    return q[..., 1] / np.maximum(1e-7, q[..., 2]), q[..., 2], t


def robust_fit(fun):
    """Small bounded robust Gauss-Newton solve; Blender only needs its bundled NumPy."""
    x = np.zeros(4)

    def objective(r):
        return np.sum(9 * (np.sqrt(1 + (r / 3) ** 2) - 1))

    for _ in range(28):
        r = fun(x)
        jac = np.column_stack([(fun(x + step) - r) / 1e-5 for step in np.eye(4) * 1e-5])
        weight = (1 + (r / 3) ** 2) ** -0.25
        step = np.linalg.lstsq(jac * weight[:, None], -r * weight, rcond=1e-7)[0]
        current = objective(r)
        for ratio in (1.0, 0.5, 0.25, 0.1, 0.025):
            if objective(fun(x + ratio * step)) < current:
                x += ratio * step
                break
        else:
            break
        if np.linalg.norm(ratio * step) < 1e-6:
            break
    return x


def fit(samples, width, height, *, constrain_depth=True):
    n = np.asarray([s["plane"] for s in samples])
    _, sv, basis = np.linalg.svd(n[:, :3], full_matrices=False)
    if len(sv) < 3 or sv[1] < 0.0005 or not any(s["front"] for s in samples):
        return None
    axis = basis[-1]
    origin = np.linalg.lstsq(np.vstack([n[:, :3], axis]), np.r_[-n[:, 3], 0.0], rcond=None)[0]
    p = []
    u = []
    v = []
    depth = []
    for s in samples:
        r = s["row"]
        lo, hi = r["x_min"], r["x_max"]
        for x, z in [
            (lo * 0.75 + hi * 0.25, 0.0),
            (lo * 0.25 + hi * 0.75, 0.0),
            *s["front"],
        ]:
            p.append(s["projection"])
            u.append(x)
            v.append(r["slope"] * x + r["intercept"])
            depth.append(z)
    p, u, v, z = map(np.asarray, (p, u, v, depth))
    cand = z > 0
    if not np.any(cand):
        return None
    scene_depth = np.median(z[cand])

    def state(x):
        return origin + (x[:2] @ basis[:2]) * scene_depth, axis + x[2:] @ basis[:2]

    def residual(x):
        o, a = state(x)
        yy, dd, _ = line_pixel(o, a, p, u)
        front = (
            np.maximum(0.0, dd[cand] - 0.965 * z[cand]) * (4 * height / scene_depth)
            if constrain_depth
            else np.empty(0)
        )
        return np.r_[np.clip(yy - v, -3 * height, 3 * height), front, np.maximum(0, -dd) * height]

    x = robust_fit(residual)
    org, axis = state(x)
    axis = axis / np.linalg.norm(axis)
    yy, dd, _ = line_pixel(org, axis, p, u)
    err = np.abs(yy - v)
    depth_fraction = float((dd[cand] < z[cand]).mean())
    if (
        np.median(err) > height * 0.012
        or np.percentile(err, 90) > height * 0.030
        or (constrain_depth and depth_fraction < 0.78)
        or (dd > 0).mean() < 0.95
    ):
        return None
    endpoints = [[], []]
    exposed = [[], []]
    radii = []
    for s in samples:
        r = s["row"]
        proj = s["projection"]
        focal = s["fx"]
        for side, x in enumerate((r["x_min"], r["x_max"])):
            _, d, t = line_pixel(org, axis, proj, np.asarray(x))
            if 0.15 * scene_depth < d < 8 * scene_depth:
                endpoints[side].append(t)
                if 0.025 * width < x < 0.975 * width:
                    exposed[side].append(t)
        for x in [c["u"] for c in r["crossings"]]:
            _, d, _ = line_pixel(org, axis, proj, np.asarray(x))
            if 0 < d < 5 * scene_depth:
                radii.append(r["thickness"] * 0.5 * d / focal)
    if not radii or not all(endpoints):
        return None
    sign = 1 if np.median(endpoints[1]) >= np.median(endpoints[0]) else -1
    limits = []
    for side in range(2):
        # Off-screen endpoints are bounds only. Interior observed ends locate the
        # actual finite member where it emerges from an occluding actor or meets a corner.
        vals = exposed[side] if len(exposed[side]) >= 6 else endpoints[side]
        q = (15 if side == 0 else 85) if sign > 0 else (85 if side == 0 else 15)
        limits.append(float(np.percentile(vals, q)))
    rad = float(np.median(radii))
    ends = [org + axis * t for t in limits]
    if abs(limits[1] - limits[0]) < 20 * rad:
        return None
    return dict(
        ends=np.asarray(ends),
        radius=rad,
        median_px=float(np.median(err)),
        p90_px=float(np.percentile(err, 90)),
        front_fraction=depth_fraction,
        surface_samples=int(cand.sum()),
        views=len(samples),
    )
