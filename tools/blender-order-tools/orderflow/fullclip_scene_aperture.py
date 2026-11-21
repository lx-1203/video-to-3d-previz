"""Recover continuous opening borders and deeper diagonal supports from repeated line evidence."""

import cv2
import numpy as np


def _contrast(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return np.uint8(np.sqrt(gray.astype(float) / 255) * 255)


def boundaries(frames, track):
    """Track an edge band over time; face highlights in one frame cannot establish a beam."""
    times = np.arange(min(frames), max(frames) + 1, 3)
    h, w = next(iter(frames.values())).shape[:2]
    scores = []
    for f in times:
        g = _contrast(frames[int(f)])
        dy = np.abs(cv2.Sobel(cv2.GaussianBlur(g, (11, 7), 3), cv2.CV_32F, 0, 1))
        x = float(np.interp(f, track[:, 0], track[:, 1]))
        width = float(np.median(track[:, 2]))
        visible = np.abs(np.arange(w) - x) > width * 0.6
        response = np.mean(np.minimum(dy[:, visible], 60), axis=1)
        scores.append(response)
    profiles = np.array(scores)
    result = []
    for lower, upper, side in ((0.035, 0.32, "upper"), (0.64, 0.97, "lower")):
        lo, hi = round(h * lower), round(h * upper)
        band = profiles[:, lo:hi]
        likelihood = band / (np.median(band, axis=1, keepdims=True) + 1.5)
        previous = likelihood[0].copy()
        links = []
        axis = np.arange(hi - lo)
        for row in likelihood[1:]:
            # Limit changes in the opening's edge to continuous measured motion.
            candidate = previous[:, None] - 0.34 * np.abs(axis[:, None] - axis[None, :])
            candidate[np.abs(axis[:, None] - axis[None, :]) > 8] = -np.inf
            link = np.argmax(candidate, axis=0)
            previous = row + candidate[link, axis]
            links.append(link)
        path = [int(np.argmax(previous))]
        for link in reversed(links):
            path.append(int(link[path[-1]]))
        yy = np.array(path[::-1]) + lo
        measured = profiles[np.arange(len(times)), yy]
        strength = likelihood[np.arange(len(times)), yy - lo]
        if np.mean((measured > 4.6) & (strength > 1.65)) < 0.48:
            continue
        result.append(
            {
                "side": side,
                "frames": times,
                "edge": yy,
                "median_response": float(np.median(measured)),
            }
        )
    return result


def _diagonals(image):
    gray = cv2.createCLAHE(3.0, (6, 6)).apply(_contrast(image))
    h, w = gray.shape
    found = cv2.createLineSegmentDetector().detect(gray)[0]
    if found is None:
        return []
    lines = []
    for x0, y0, x1, y1 in found[:, 0]:
        if x0 > x1:
            x0, x1, y0, y1 = x1, x0, y1, y0
        dx = x1 - x0
        if dx < w * 0.069:
            continue
        m = (y1 - y0) / dx
        if 0.22 < abs(m) < 1.55:
            lines.append((x0, x1, m, y0 - m * x0))
    pairs = []
    for index, (a, b, m, n) in enumerate(lines):
        for c, d, k, q in lines[:index]:
            if abs(m - k) > 0.12:
                continue
            left, right = max(a, c), min(b, d)
            if right - left < max(w * 0.052, 0.27 * min(b - a, d - c)):
                continue
            x = (left + right) / 2
            separation = abs((m - k) * x + n - q)
            if not h * 0.035 < separation < h * 0.35:
                continue
            xx = np.linspace(left + 2, right - 2, 15)
            mid = ((m + k) * xx + n + q) * 0.5
            sample = []
            for f in (0, -0.63, 0.63):
                px = np.clip(np.rint(xx).astype(int), 0, w - 1)
                py = np.clip(np.rint(mid + separation * f).astype(int), 0, h - 1)
                sample.append(gray[py, px].astype(float))
            contrast = (sample[1] + sample[2]) * 0.5 - sample[0]
            if max(np.mean(contrast > 3), np.mean(contrast < -3)) < 0.62:
                continue
            pairs.append(
                (float(m + k) * 0.5, float(n + q) * 0.5, float(separation), float(b - a + d - c))
            )
    return pairs


def rear_support(frames, cams, intr, front_depth, solid_box, faces):
    h, w = next(iter(frames.values())).shape[:2]
    candidates = []
    for f in range(min(frames), max(frames) + 1, 2):
        for m, offset, thick, score in _diagonals(frames[f]):
            candidates.append((f, m, offset, thick, score))
    if not candidates:
        return [], {}
    best = []
    for ref in sorted(candidates, key=lambda a: -a[4])[:20]:
        group = [
            x
            for x in candidates
            if abs(x[1] - ref[1]) < 0.125 and abs(np.log(x[3] / ref[3])) < 0.55
        ]
        per = {}
        for x in group:
            if x[0] not in per or x[4] > per[x[0]][4]:
                per[x[0]] = x
        if len(per) > len(best):
            best = [per[f] for f in sorted(per)]
    if len(best) < 5 or best[-1][0] - best[0][0] < 10:
        return [], {}
    a = np.asarray(best)
    m = float(np.median(a[:, 1]))
    tmean = np.median(a[:, 0])
    t = a[:, 0] - tmean
    coeff = np.polynomial.polynomial.polyfit(t, a[:, 2], 1)
    resid = np.abs(np.polynomial.polynomial.polyval(t, coeff) - a[:, 2])
    keep = resid < max(h * 0.07, float(np.median(resid) * 2))
    if keep.sum() < 5:
        return [], {}
    coeff = np.polynomial.polynomial.polyfit(t[keep], a[keep, 2], 1)
    thickness = float(np.median(a[keep, 3])) / np.sqrt(1 + m * m)
    if thickness < h * 0.025:
        return [], {}
    depth = front_depth * 1.58
    tangent = np.array([1.0, m, 0.0]) / np.sqrt(1 + m * m)
    normal = np.array([-m, 1.0, 0.0]) / np.sqrt(1 + m * m)
    basis = np.column_stack((tangent, normal, [0.0, 0.0, 1.0]))
    focal = w * intr[0, 0]
    motion = {}
    for f in frames:
        intercept = float(np.polynomial.polynomial.polyval(f - tmean, coeff))
        xx = w * 0.48
        yy = m * xx + intercept
        local = (
            np.array([(xx / w - intr[0, 2]) / intr[0, 0], (yy / h - intr[1, 2]) / intr[1, 1], 1])
            * depth
        )
        mat = cams[f]
        motion[str(f)] = {
            "rotation": (mat[:3, :3] @ basis).round(8).tolist(),
            "center": (mat[:3, :3] @ local + mat[:3, 3]).round(8).tolist(),
        }
    obj = {
        "source_frame": int(a[keep, 0].min()),
        "kind": "Rear diagonal brace",
        "tone": 2,
        "rgb": [0.39, 0.40, 0.41],
        "vertices": solid_box(
            depth * w / focal * 0.86,
            depth * thickness / focal * 0.5,
            depth * thickness / focal * 0.26,
        ),
        "faces": faces,
        "motion": motion,
        "source_observations": [int(f) for f in a[keep, 0]],
    }
    return [obj], {
        "observed_frames": obj["source_observations"],
        "fitted_slope": m,
        "thickness_pixels": thickness,
        "edge_support": "paired persistent diagonal edges",
    }


def _clipped_observation(image, right):
    if not right:
        image = image[:, ::-1]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = gray.shape
    xmin = round(w * 0.87)
    region = gray[:, xmin:]
    threshold = max(45.0, float(np.percentile(gray, 98)))
    hot = region > threshold
    row = cv2.GaussianBlur(
        np.sum(np.minimum(region, 180) * hot, axis=1).reshape(-1, 1), (1, 13), 2.7
    ).ravel()
    hr = round(h * 0.35)
    peak = hr + int(np.argmax(row[hr : round(h * 0.90)]))
    if row[peak] < 100:
        return None
    # Follow the long vertical side continuing below a cluster of specular highlights.
    y0 = min(h - 8, peak + max(5, int(h * 0.045)))
    y1 = min(h - 1, peak + max(13, int(h * 0.18)))
    dx = cv2.Sobel(cv2.GaussianBlur(gray, (3, 3), 0.7), cv2.CV_32F, 1, 0)
    derivative = dx[y0:y1, xmin:]
    if len(derivative) < 5:
        return None
    positive = np.mean(np.clip(derivative, 0, 65), axis=0)
    neg = np.mean(np.clip(-derivative, 0, 65), axis=0)
    scores = np.maximum(positive, neg)
    candidates = []
    for x in range(2, len(scores) - 2):
        if scores[x] < 5 or scores[x] < max(scores[x - 2 : x + 3]):
            continue
        xx = x + xmin
        strip = gray[max(0, peak - 23) : min(h, peak + 13), xx : min(w, xx + 24)]
        if strip.size < 25 or np.sum(strip > threshold) < 4:
            continue
        contrast = max((derivative[:, x] > 4).mean(), (derivative[:, x] < -4).mean())
        if contrast < 0.61:
            continue
        candidates.append((scores[x] * contrast, xx))
    if not candidates:
        return None
    score, x = max(candidates)
    if not 2 <= w - x <= w * 0.105:
        return None
    return float(x), float(peak), float(score)


def clipped_fixture(frames, views, scales, cams, intr):
    h, w = next(iter(frames.values())).shape[:2]
    possibilities = []
    for right in (False, True):
        rows = []
        for f in sorted(frames)[::2]:
            r = _clipped_observation(frames[f], right)
            if r is not None:
                rows.append((f, *r))
        if len(rows) < max(5, len(frames) * 0.23):
            continue
        a = np.asarray(rows)
        if np.median(np.abs(a[:, 2] - np.median(a[:, 2]))) > h * 0.054:
            continue
        # Keep a continuous observed outline. A collection of unconnected edge highlights
        # cannot establish an upright object.
        c = np.polyfit(a[:, 0] - a[0, 0], a[:, 1], 1)
        err = np.abs(np.polyval(c, a[:, 0] - a[0, 0]) - a[:, 1])
        good = err < max(3.0, min(8.0, float(np.median(err) * 1.9)))
        a = a[good]
        if len(a) < max(5, len(frames) * 0.18) or a[-1, 0] - a[0, 0] < len(frames) * 0.55:
            continue
        possibilities.append((len(a) * np.median(a[:, 3]), right, a))
    if not possibilities:
        return [], {}
    _, right, a = max(possibilities, key=lambda x: x[0])
    # Shape is measured from the aligned edge. The far side beyond the image is inferred.
    radius = float(np.clip(np.percentile(w - a[:, 1], 92) * 0.82, 6, w * 0.08))
    center0 = float(np.median(a[:, 2]))
    profiles = []
    for f, x, peak, _ in a:
        im = frames[int(f)] if right else frames[int(f)][:, ::-1]
        grayscale = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx = np.abs(cv2.Sobel(cv2.GaussianBlur(grayscale, (3, 3), 0.72), cv2.CV_32F, 1, 0))
        px = int(round(x))
        int(round(x + radius))
        sample = np.zeros((h, int(np.ceil(radius)) + 2), np.float32)
        for j in range(sample.shape[1]):
            source_x = px + j
            if 1 <= source_x < w - 1:
                sample[:, j] = gx[:, source_x]
        profiles.append(np.roll(sample, int(round(center0 - peak)), axis=0))
    edge = np.median(profiles, axis=0)
    # Determine the measured top of the taper from persistent vertical contour support.
    support = edge.max(axis=1)
    low = max(2, round(center0 - h * 0.32))
    high = max(low + 2, round(center0 - h * 0.065))
    support = cv2.GaussianBlur(support.reshape(-1, 1), (1, 7), 1.6).ravel()
    ys = np.flatnonzero(support[low:high] > max(4.0, np.percentile(support[low:high], 48))) + low
    top = float(ys[0]) if len(ys) > 5 else float(high)
    if center0 - top < h * 0.06:
        return [], {}
    bottom = float(h + max(4, h * 0.04))
    ygrid = np.linspace(top, bottom, 23)
    # Fit a gently varying radius against the observed silhouette, anchored by the
    # straight shaft. A weak or clipped far side remains an inferred surface of revolution.
    grid = np.arange(edge.shape[1])
    path = []
    prev = 0.0
    for y in ygrid[::-1]:
        if y > center0 + 4:
            offset = 0.0
        else:
            band = edge[max(0, int(y) - 2) : min(h, int(y) + 3)]
            if not band.size:
                offset = prev
            else:
                reward = np.mean(band, axis=0) - 1.8 * np.abs(grid - prev)
                offset = float(np.argmax(reward))
        prev = np.clip(offset, prev - radius * 0.17, prev + radius * 0.17)
        path.append(max(radius * 0.30, radius - prev))
    rp = np.asarray(path[::-1])
    if radius - np.min(rp[ygrid < center0]) < radius * 0.18:
        return [], {}
    # Preserve a crisp lip and a measured lighting break at the neck.
    ref = min(views, key=lambda f: abs(f - np.median(a[:, 0])))
    dep = views[ref] * scales[ref]
    dh, dw = dep.shape
    px = float(np.interp(ref, a[:, 0], a[:, 1])) + min(3, radius * 0.24)
    source_x = px if right else w - 1 - px
    pix = np.clip(
        np.rint([source_x / w * dw, center0 / h * dh]).astype(int), [0, 0], [dw - 1, dh - 1]
    )
    nearby = dep[max(0, pix[1] - 3) : pix[1] + 4, max(0, pix[0] - 2) : pix[0] + 3]
    valid = nearby[np.isfinite(nearby) & (nearby > 0)]
    if not len(valid):
        return [], {}
    d = float(np.median(valid)) * 0.96
    focal = w * intr[0, 0]
    focal_y = h * intr[1, 1]
    middle = (top + bottom) * 0.5
    rings = []
    for y, r in zip(ygrid, rp):
        rings.append((float(y), float(r)))
    # Conservative concentric rims at observed upper/lower shaft transitions.
    for y in (center0 + 3.0, center0 + h * 0.17):
        if top + 3 < y < bottom - 3:
            rr = float(np.interp(y, ygrid, rp))
            rings.extend([(y - 1.0, rr), (y, rr * 1.045), (y + 1.5, rr)])
    rings = sorted(rings)
    verts = []
    faces = []
    sides = 24
    for k, (y, r) in enumerate(rings):
        for i in range(sides):
            theta = 2 * np.pi * i / sides
            verts.append(
                [
                    r * np.cos(theta) * d / focal,
                    (y - middle) * d / focal_y,
                    r * np.sin(theta) * d / focal,
                ]
            )
            if k:
                p = (k - 1) * sides
                faces.append(
                    [p + i, p + sides + i, p + sides + (i + 1) % sides, p + (i + 1) % sides]
                )
    faces.append(list(range(sides)))
    faces.append(list(range(len(rings) * sides - 1, (len(rings) - 1) * sides - 1, -1)))
    poses = {}
    for f, camera in cams.items():
        xx = np.interp(f, a[:, 0], a[:, 1]) + radius
        yy = np.interp(f, a[:, 0], a[:, 2]) + middle - center0
        if not right:
            xx = w - 1 - xx
        point = (
            np.array([(xx / w - intr[0, 2]) / intr[0, 0], (yy / h - intr[1, 2]) / intr[1, 1], 1])
            * d
        )
        poses[str(f)] = {
            "rotation": camera[:3, :3].round(8).tolist(),
            "center": (camera[:3, :3] @ point + camera[:3, 3]).round(8).tolist(),
        }
    obj = {
        "kind": "Clipped ribbed fixture",
        "tone": 4,
        "rgb": [0.64, 0.66, 0.68],
        "source_frame": ref,
        "source_observations": [int(f) for f in a[:, 0]],
        "vertices": np.round(verts, 7).tolist(),
        "faces": faces,
        "motion": poses,
        "observation_status": "visible profile, inferred axis and hidden far side",
    }
    return [obj], {
        "frames": obj["source_observations"],
        "side": "right" if right else "left",
        "edge_observations": [
            [int(f), float(x if right else w - 1 - x), float(y)] for f, x, y, _ in a
        ],
        "estimated_radius_px": radius,
        "observed_top_row": top,
        "inferred_backside": True,
        "radial_sections": len(rings),
    }
