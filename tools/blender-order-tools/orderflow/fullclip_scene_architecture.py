"""Large-scale editable architectural patches and observing-view palette fitting."""

import cv2
import numpy as np


def clay(bgr):
    # Neutral clay uses source brightness to distinguish surfaces without inheriting set colours.
    b, g, r = np.asarray(bgr, float) / 255
    luminance = 0.0722 * b + 0.7152 * g + 0.2126 * r
    value = 0.38 + 0.49 * np.sqrt(np.clip(luminance, 0, 1))
    return np.round([value * 1.009, value, value * 0.988], 3).tolist()


def _smooth_depth(depth, known):
    data = np.where(known, depth, 0).astype(np.float32)
    weight = known.astype(np.float32)
    lo, hi = np.nanpercentile(depth[known], [4, 96])
    data = np.where(known, np.clip(data, lo, hi), 0)
    for sigma in (5, 19, 60):
        g = cv2.GaussianBlur(data, (0, 0), sigma)
        w = cv2.GaussianBlur(weight, (0, 0), sigma)
        sel = (weight == 0) & (w > 0.01)
        data[sel] = g[sel] / w[sel]
        weight[sel] = 1
    data[weight == 0] = hi
    return cv2.GaussianBlur(data, (0, 0), 5)


def _plane_depths(depth, known):
    """Piecewise-planar inverse-depth fit with bounded projective slopes."""
    h, w = depth.shape
    yy, xx = np.indices((h, w), dtype=float)
    design = np.stack((xx / w - 0.5, yy / h - 0.5, np.ones((h, w))), axis=2)
    y, x = np.nonzero(known)
    y, x = y[::7], x[::7]
    points = design[y, x]
    od = 1 / np.maximum(depth[y, x], 0.001)
    remaining = np.ones(len(od), bool)
    rng = np.random.default_rng(7421)
    planes = []
    for _ in range(6):
        idx = np.flatnonzero(remaining)
        if len(idx) < max(15, len(od) * 0.025):
            break
        best = None
        score = 0
        for _ in range(60):
            sel = rng.choice(idx, 3, replace=False)
            mat = points[sel]
            if abs(np.linalg.det(mat)) < 0.008:
                continue
            coeff = np.linalg.solve(mat, od[sel])
            residual = (
                np.abs(points[idx, 0] * coeff[0] + points[idx, 1] * coeff[1] + coeff[2] - od[idx])
                / od[idx]
            )
            inliers = idx[residual < 0.045]
            if len(inliers) > score:
                best = inliers
                score = len(inliers)
        if best is None or score < max(12, len(od) * 0.022):
            break
        coeff = np.linalg.lstsq(points[best], od[best], rcond=None)[0]
        planes.append(coeff)
        remaining[best] = False
    if not planes:
        return np.array([np.full((h, w), np.median(depth[known]))]), np.zeros((h, w), int)
    lo, hi = np.percentile(depth[known], [2, 98])
    prediction = np.stack(
        [np.clip(1 / np.maximum(design @ p, 0.001), lo * 0.9, hi * 1.1) for p in planes]
    )
    sm = _smooth_depth(depth, known)
    error = np.abs(prediction - sm) / np.maximum(sm, 0.001)
    labels = np.argmin(error, axis=0).astype(np.uint8)
    labels = cv2.medianBlur(labels, 11)
    return prediction, labels


def _mesh(image, depth, known, project, frame, simple):
    h, w = depth.shape
    # Suppress embedded actors and shadows before estimating the broad construction palette.
    rgb = cv2.inpaint(image, (~known).astype(np.uint8), 8, cv2.INPAINT_TELEA)
    if simple:
        f = cv2.GaussianBlur(rgb, (0, 0), max(5, w // 66))
        layers = np.array([np.full_like(depth, np.percentile(depth[known], 95) * 1.55)])
        layer_id = np.zeros((h, w), int)
    else:
        f = cv2.bilateralFilter(cv2.medianBlur(rgb, 7), 13, 24, 24)
        layers, layer_id = _plane_depths(depth, known)
        layers = layers * 1.07
    step = 6 if not simple else 7
    xs = np.unique(
        np.r_[-round(w * 0.2), 0, np.arange(step, w - 1, step), w - 1, w - 1 + round(w * 0.2)]
    )
    ys = np.unique(
        np.r_[-round(h * 0.2), 0, np.arange(step, h - 1, step), h - 1, h - 1 + round(h * 0.2)]
    )
    xx, yy = np.meshgrid(xs, ys)
    px = np.clip(xx, 0, w - 1)
    py = np.clip(yy, 0, h - 1)
    uv = np.column_stack((xx.ravel(), yy.ravel()))
    vi = np.arange(xx.size).reshape(xx.shape)
    faces = np.column_stack(
        [vi[:-1, :-1].ravel(), vi[1:, :-1].ravel(), vi[1:, 1:].ravel(), vi[:-1, 1:].ravel()]
    )
    color = f[
        np.clip(np.rint(uv[faces].mean(axis=1)[:, 1]).astype(int), 0, h - 1),
        np.clip(np.rint(uv[faces].mean(axis=1)[:, 0]).astype(int), 0, w - 1),
    ].astype(np.float32)
    if simple:
        # Visual context is poorly observed behind a close actor. Use a muted backing and
        # recover separately measured beams and framing from strong persistent edges.
        average = np.median(color, axis=0)
        color = color * 0.32 + average * 0.68
    _, labels, centers = cv2.kmeans(
        color,
        12 if simple else 7,
        None,
        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 35, 0.24),
        1,
        cv2.KMEANS_PP_CENTERS,
    )
    centers_uv = np.clip(np.rint(uv[faces].mean(axis=1)).astype(int), [0, 0], [w - 1, h - 1])
    face_layer = layer_id[centers_uv[:, 1], centers_uv[:, 0]]
    groups = []
    for idx, z in enumerate(layers):
        keep = face_layer == idx
        retained = faces[keep]
        if not len(retained):
            continue
        used, local = np.unique(retained, return_inverse=True)
        xyz = project(uv[used], z[py.ravel()[used], px.ravel()[used]])
        groups.append(
            {
                "kind": "Observed planar shell" if not simple else "Backing • inferred extent",
                "source_frame": frame,
                "tone": 3,
                "palette": [
                    (
                        [
                            float(v)
                            for v in (0.245 + min(float(np.mean(c)) / 95.0, 1.0) * 0.13)
                            * np.array([0.96, 1.0, 1.05])
                        ]
                        if simple
                        else clay(c)
                    )
                    for c in centers
                ],
                "face_material": labels[keep, 0].tolist(),
                "vertices": xyz.round(5).tolist(),
                "faces": local.reshape(-1, 4).tolist(),
            }
        )
    return groups


def architecture(image, depth, known, project, frame, simple):
    return _mesh(image, depth, known, project, frame, simple) if known.sum() >= 24 else []
