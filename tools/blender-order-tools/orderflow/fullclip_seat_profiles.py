"""Recover individual backrest profiles from repeated upholstery and its surrounding trim."""

import cv2
import numpy as np


def uv(v, c, K, dim):
    p = (v - c[:3, 3]) @ c[:3, :3]
    return (p[..., :2] / np.maximum(0.001, p[..., 2:]) * [K[0, 0], K[1, 1]] + K[:2, 2]) * dim, p[
        ..., 2
    ]


def calibration(im, known, backs, cam, K):
    h, w = known.shape
    c = np.log((im[:, :, :2].astype("float32") + 6.0) / (im[:, :, 2:].astype("float32") + 6.0))
    p, z = uv(backs, cam, K, [w, h])
    center = p.mean(1)
    span = np.ptp(p, axis=1)
    valid = (
        (z.min(1) > 0.1) & np.all(center - span > 0, 1) & np.all(center + span < [w - 1, h - 1], 1)
    )
    plus = []
    minus = []
    for xy, size in zip(center[valid], span[valid]):
        px = np.rint(xy).astype(int)
        if not known[px[1], px[0]]:
            continue
        delta = np.array([[0, -0.88], [0, 0.88], [-0.58, 0], [0.58, 0]]) * size
        a, b = np.rint(xy + delta).astype(int).T
        sample = c[b, a][known[b, a]]
        if not len(sample):
            continue
        plus.append(c[px[1], px[0]])
        minus.append(np.median(sample, axis=0))
    if len(plus) < 6:
        return None
    d = np.median(np.array(plus) - minus, axis=0)
    contrast = np.linalg.norm(d)
    if contrast < 0.06:
        return None
    direction = d / contrast
    score = c @ direction
    return score, np.median(np.array(plus) @ direction), contrast


def panels(image, known, backs, cam, K):
    calibration_result = calibration(image, known, backs, cam, K)
    if calibration_result is None:
        return []
    score, pos, contrast = calibration_result
    h, w = score.shape
    proposals = []
    for tau in (-0.35, 0.15, 0.55, 0.95):
        mask = (score > pos - contrast * tau) & known
        edges = np.diff(np.pad(mask, ((0, 0), (1, 1))).astype("int8"), axis=1)
        prev = []
        done = []
        for y in range(h):
            runs = []
            for a, b in zip(np.flatnonzero(edges[y] == 1), np.flatnonzero(edges[y] == -1)):
                width = b - a
                if not 3 <= width < w * 0.15 or a < 1 or b >= w - 1:
                    continue
                found = [
                    (min(b, prior[-1][2]) - max(a, prior[-1][1]), i)
                    for i, prior in enumerate(prev)
                    if 0.65 < (prior[-1][2] - prior[-1][1]) / width < 1.55
                ]
                overlap, i = max(found, default=(0, None))
                if overlap > 0.71 * width:
                    chain = prev.pop(i)
                else:
                    chain = []
                chain.append((y, a, b))
                runs.append(chain)
            done.extend(prev)
            prev = runs
        for run in done + prev:
            if len(run) < 4:
                continue
            ys, left, right = np.array(run).T
            width = np.median(right - left)
            if not 0.3 < width / len(run) < 5.0 or width < 4:
                continue
            center = np.array([(np.median(left) + np.median(right)) * 0.5, np.mean(ys)])
            if np.max(right) - np.min(left) > 1.9 * width:
                continue
            corners = np.array(
                [
                    [left[0], ys[0]],
                    [right[0], ys[0]],
                    [right[-1], ys[-1]],
                    [left[-1], ys[-1]],
                ]
            )
            candidates_quality = np.sum(right - left)
            proposals.append((candidates_quality, center, corners))
    out = []
    for _, c, p in sorted(proposals, key=lambda e: -e[0]):
        if any(np.max(np.abs(c - b) / np.maximum(2, np.ptp(q, axis=0))) < 0.42 for b, q in out):
            continue
        out.append((c, p))
    return out


def _edge_family(image):
    lines = cv2.createLineSegmentDetector().detect(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))[0]
    if lines is None:
        return np.empty((0, 5))
    endpoints = lines.reshape(-1, 2, 2)
    d = endpoints[:, 1] - endpoints[:, 0]
    keep = (abs(d[:, 0]) > 3) & (abs(d[:, 1]) < 0.98 * abs(d[:, 0]))
    points = endpoints[keep]
    d = d[keep]
    return np.column_stack(
        (
            points[:, :, 0].min(1),
            points[:, :, 0].max(1),
            points[:, 0],
            d[:, 1] / d[:, 0],
        )
    )


def _bank_runs(image, known, backs, camera, intr, lines, outlines):
    profile = calibration(image, known, backs, camera, intr)
    if profile is None:
        return outlines
    h, w = known.shape
    score, foreground, contrast = profile
    # On an oblique tier the backs touch in horizontal image scans. Rectifying along
    # its measured vanishing line separates the actual padded faces at their stiles.
    projected, depth = uv(backs, camera, intr, [w, h])
    seeds = [(xy, np.ptp(poly, axis=0) + 1) for xy, poly in outlines]
    for quad, z in zip(projected, depth):
        center = quad.mean(0)
        size = np.ptp(quad, axis=0)
        if z.min() > 0.02 and min(size) > 3 and np.all((center > 1) & (center < [w - 2, h - 2])):
            seeds.append((center, size))
    found = []
    for xy, sz in seeds:
        x, y = np.rint(xy).astype(int)
        if not known[y, x]:
            continue
        slope = _row_direction(lines, xy, sz)
        if slope is None:
            continue
        us = np.arange(max(0, round(x - 3.5 * sz[0])), min(w, round(x + 3.5 * sz[0]) + 1))
        vs = np.arange(
            -min(38, max(5, round(1.15 * sz[1]))),
            min(38, max(5, round(1.15 * sz[1]))) + 1,
        )
        rows = np.rint(y + (us - x) * slope + vs[:, None]).astype(int)
        valid = (rows >= 1) & (rows < h - 1)
        rows = rows.clip(0, h - 1)
        known_region = known[rows, us] & valid
        for threshold in (foreground - 0.23 * contrast, foreground - 0.62 * contrast):
            positive = (score[rows, us] > threshold) & known_region
            for offset in [0, round(sz[1] * 0.18), -round(sz[1] * 0.18)]:
                j = int(np.clip(len(vs) // 2 + offset, 0, len(vs) - 1))
                boundaries = np.diff(np.pad(positive[j], (1, 1)).astype(np.int8))
                for left, stop in zip(
                    np.flatnonzero(boundaries == 1), np.flatnonzero(boundaries == -1)
                ):
                    width = stop - left
                    if (
                        left == 0
                        or stop == len(us)
                        or not max(4, sz[0] * 0.52) <= width <= sz[0] * 2.05
                    ):
                        continue
                    if not (known_region[j, left - 1] and known_region[j, stop]):
                        continue
                    core = positive[:, left + width // 4 : stop - width // 4].mean(1) > 0.68
                    if not core[j]:
                        continue
                    low = j
                    while low > 0 and core[low - 1]:
                        low -= 1
                    high = j
                    while high < len(vs) - 1 and core[high + 1]:
                        high += 1
                    height = high - low + 1
                    if (
                        low == 0
                        or high + 1 == len(vs)
                        or not max(4, sz[1] * 0.33) <= height <= sz[1] * 1.8
                    ):
                        continue
                    if not 0.32 < width / height < 5:
                        continue
                    aa, bb = us[left], us[stop - 1]
                    t = np.array([aa, bb, bb, aa])
                    vv = np.array([vs[low], vs[low], vs[high], vs[high]])
                    corners = np.column_stack([t, np.rint(y + slope * (t - x) + vv)]).astype(int)
                    found.append((width * height, corners.mean(0), corners))
    # Select a full rectified profile before the thin horizontal fragment of the same pad.
    result = []
    for _, center, quad in sorted(found, key=lambda a: -a[0]):
        size = np.maximum(3, np.ptp(quad, axis=0))
        if not any(np.max(abs(center - prev) / size) < 0.43 for prev, _ in result):
            result.append((center, quad))
    result.extend(
        [
            (c, q)
            for c, q in outlines
            if not any(
                np.max(abs(c - prev) / np.maximum(3, np.ptp(poly, axis=0))) < 0.46
                for prev, poly in result
            )
        ]
    )
    return result


def _row_direction(lines, center, size):
    a, b, x, y, slopes = lines.T
    overlap = np.minimum(b, center[0] + 3 * size[0]) - np.maximum(a, center[0] - 3 * size[0])
    offset = abs(y - center[1] - slopes * (x - center[0]))
    use = (overlap > size[0] * 0.54) & (offset < 1.8 * size[1])
    if not use.any():
        return None
    slopes = slopes[use]
    weights = overlap[use] / (1 + 0.45 * offset[use] / size[1])
    agree = abs(slopes[:, None] - slopes[None, :]) < 0.10
    best = np.argmax(agree @ weights)
    if weights[agree[best]].sum() < size[0] * 1.2:
        return None
    return np.average(slopes[agree[best]], weights=weights[agree[best]])


def neighbors(candidates, projected, lines):
    c = np.array([p for p, _ in candidates])
    wh = np.array([np.ptp(v, axis=0) + 1 for _, v in candidates])
    result = {}
    if len(c) < 3:
        return result
    for i, (xy, sz) in enumerate(zip(c, wh)):
        orientation = _row_direction(lines, xy, sz)
        if orientation is None:
            continue
        delta = c - xy
        plausible = (
            (np.abs(delta[:, 0]) >= sz[0] * 0.72)
            & (np.abs(delta[:, 0]) <= sz[0] * 2.45)
            & (np.abs(delta[:, 1]) <= np.abs(delta[:, 0]) * 0.97 + 0.5)
        )
        plausible &= np.all((wh / sz > 0.56) & (wh / sz < 1.76), axis=1)
        for j in np.flatnonzero(plausible):
            slope = delta[j, 1] / delta[j, 0]
            if abs(slope - orientation) > 0.15:
                continue
            slope = orientation
            # A row of like-sized cushion faces at a different height is an ambiguous diagonal.
            axis = delta[:, 0]
            near = np.abs(delta[:, 1] - slope * axis) < np.maximum(2.0, sz[1] * 0.24)
            near &= np.abs(axis) < sz[0] * 6.8
            near &= np.all((wh / sz > 0.53) & (wh / sz < 1.85), axis=1)
            band = np.flatnonzero(near)
            if len(band) < 3:
                continue
            phase = axis[band] / abs(delta[j, 0])
            aligned = np.abs(phase - np.rint(phase)) < 0.24
            group = band[aligned]
            if len(group) < 3:
                continue
            distance = np.min(np.linalg.norm((projected - c[group, None, :]) / sz, axis=2), axis=1)
            backed = np.count_nonzero(distance < 0.85)
            # new row must be close to another confirmed bank of chairs, not independent trim.
            closest = np.min(np.linalg.norm((projected - xy) / sz, axis=1))
            if backed < 1 and closest > 2.1:
                continue
            score = len(group) + 0.32 * backed - 0.18 * closest
            if i not in result or score > result[i][0]:
                result[i] = (score, group, slope, min(2.5, abs(delta[j, 0]) / sz[0]))
    return result


def intervals(image, known, p, slope):
    h, w = known.shape
    left, right = int(p[:, 0].min()), int(p[:, 0].max())
    width = right - left
    if width < 4:
        return
    center = p.mean(0)
    x, y = center
    # Estimate the visible upholstery polygon from scanline intersections, excluding extreme tips;
    # slope compensates diagonal rows (y range at center can exceed horizontal-slice height).
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, p.astype("int32"), 1)
    yp, xp = np.nonzero(mask)
    t = yp - slope * (xp - x)
    low, high = np.percentile(t, [2, 98])
    if high - low < 3:
        return
    # Choose a complete transverse edge above the panel, separated by a chromatically different strip.
    ref = image[yp, xp].astype(float)
    upper_bound = round(min(17, (high - low) * 0.83))
    xs = np.unique(np.rint(np.linspace(x - width * 0.25, x + width * 0.25, 9)).astype(int))
    base = low + slope * (xs - x)
    im = image.astype("float32")
    score = []
    for dy in range(2, upper_bound + 1):
        yy = np.rint(base - dy).astype(int)
        if np.any(yy < 1) or np.any(yy >= h - 1) or not np.all(known[yy, xs]):
            break
        edge = np.linalg.norm(im[yy - 1, xs] - im[yy + 1, xs], axis=1)
        score.append((np.median(edge), dy))
    med = np.median(ref, axis=0)
    if not score:
        return low, high, 0, med, med
    val, dy = max(score)
    inner_y = np.rint(base - dy * 0.5).astype(int)
    band = im[inner_y, xs].mean(axis=0)

    def chrom(a):
        return np.log((a[:2] + 6) / (a[2] + 6))

    if val < 10 or np.linalg.norm(chrom(band) - chrom(med)) < 0.16:
        return low, high, 0, med, med
    return low, high, dy, med, band


def _available(image, boxes, original):
    h, w = image.shape[:2]
    known = np.ones((h, w), bool)
    for bounds in boxes:
        x0, y0, x1, y1 = np.rint(
            np.array(bounds) * np.tile([w / original[0], h / original[1]], 2)
        ).astype(int)
        known[max(0, y0) : max(0, y1 + 1), max(0, x0) : max(0, x1 + 1)] = False
    return known


def _world(points, depth, camera, intr, size):
    xy = ((points / size) - intr[:2, 2]) / [intr[0, 0], intr[1, 1]]
    return (np.column_stack([xy, np.ones(len(xy))]) * np.asarray(depth).reshape(-1, 1)) @ camera[
        :3, :3
    ].T + camera[:3, 3]


def _witnesses(frame, centers, worlds, sizes, frames, cameras, intr, boxes, original):
    h, w = frames[frame].shape[:2]
    source = cv2.cvtColor(frames[frame], cv2.COLOR_BGR2GRAY)
    pts = np.asarray(centers, dtype=np.float32).reshape(-1, 1, 2)
    result = [[] for _ in pts]
    for target in (frame - 12, frame - 6, frame + 6, frame + 12):
        if target not in cameras or target not in frames:
            continue
        other = cv2.cvtColor(frames[target], cv2.COLOR_BGR2GRAY)
        q, status, _ = cv2.calcOpticalFlowPyrLK(
            source, other, pts, None, winSize=(17, 17), maxLevel=2
        )
        if q is None:
            continue
        back, status2, _ = cv2.calcOpticalFlowPyrLK(
            other, source, q, None, winSize=(17, 17), maxLevel=2
        )
        if back is None:
            continue
        measured, z = uv(worlds, cameras[target], intr, [w, h])
        xy = q[:, 0]
        good = (status.ravel() > 0) & (status2.ravel() > 0) & (z > 0.01)
        good &= np.linalg.norm(back[:, 0] - pts[:, 0], axis=1) < 1.1
        good &= np.linalg.norm(measured - xy, axis=1) < np.maximum(2.0, sizes.min(1) * 0.38)
        inside = np.all((xy > 1) & (xy < [w - 2, h - 2]), axis=1)
        xx, yy = np.rint(np.clip(xy, [0, 0], [w - 1, h - 1])).astype(int).T
        good &= inside & _available(frames[target], boxes.get(target, []), original)[yy, xx]
        for i in np.flatnonzero(good):
            result[i].append(target)
    return result


def _profile(observation, old, intr, camera, size):
    xy, width, top, bottom, band, slope, gradient, depth = observation
    # Perspective along a row follows a linear inverse-depth profile.  A visible width
    # bounds the fitted slope when a monocular depth pixel falls on the next tier.
    half = width * 0.5
    u = np.array([xy[0] - half, xy[0] + half, xy[0] - half, xy[0] + half])
    edge_depth = 1 / (1 / depth + np.clip(gradient * (u - xy[0]), -0.09 / depth, 0.09 / depth))

    def surface(y0, y1):
        v = np.array([y1, y1, y0, y0]) + slope * (u - xy[0])
        return _world(np.column_stack([u, v]), edge_depth, camera, intr, size)

    face = surface(top, bottom)
    normal = np.cross(face[1] - face[0], face[2] - face[0])
    normal /= max(1e-9, np.linalg.norm(normal))
    if normal @ (camera[:3, 3] - face.mean(0)) < 0:
        normal = -normal
    dimensions = np.linalg.norm(np.array([face[1] - face[0], face[2] - face[0]]), axis=1)
    old_size = np.linalg.norm(old[1] - old[0])
    if min(dimensions) < old_size * 0.11 or max(dimensions) > old_size * 2.55:
        return None
    thickness = np.clip(np.linalg.norm(old[2] - old[0]), old_size * 0.07, old_size * 0.18)

    def box(rect, front, rear):
        a = rect + front * normal
        b = rect + rear * normal
        return np.vstack([b[:2], a[:2], b[2:], a[2:]]).round(5).tolist()

    return box(face, 0, -thickness * 0.50), box(
        surface(top - band, bottom), -thickness * 0.35, -thickness
    )


def refine(items, frames, views, scales, cameras, intr, boxes, original):
    from collections import defaultdict
    import copy

    chairs = defaultdict(dict)
    for item in items:
        if "seat_index" in item:
            chairs[item["source_frame"], item["seat_row"], item["seat_index"]][item["kind"]] = item
    keys = [
        key
        for key, parts in chairs.items()
        if "Seat back" in parts and len(parts["Seat back"]["vertices"]) == 8
    ]
    if len(keys) < 6 or len(views) < 2:
        return {}
    base = np.array([chairs[key]["Seat back"]["vertices"] for key in keys])
    h, w = next(iter(frames.values())).shape[:2]
    candidates = []
    for f in sorted(views):
        image = frames[f]
        known = _available(image, boxes.get(f, []), original)
        outlines = panels(image, known, base, cameras[f], intr)
        if not outlines:
            continue
        projected, z = uv(base, cameras[f], intr, [w, h])
        centers = projected.mean(1)
        centers[z.min(1) <= 0.01] = -1000000
        lines = _edge_family(image)
        outlines = _bank_runs(image, known, base, cameras[f], intr, lines, outlines)
        rows = neighbors(outlines, centers, lines)
        # A single remaining pad may already belong to a multi-view measured bank.
        # Match its centre and rail direction using other existing chairs in that bank.
        for i, (center, poly) in enumerate(outlines):
            if i in rows:
                continue
            size = np.ptp(poly, axis=0) + 1
            delta = centers - center
            closest = int(np.argmin(np.linalg.norm(delta / size, axis=1)))
            if np.linalg.norm(delta[closest] / size) > 0.42:
                continue
            slope = _row_direction(lines, center, size)
            if slope is None:
                continue
            flat = abs(delta[:, 1] - slope * delta[:, 0]) < max(2.1, size[1] * 0.45)
            near = flat & (abs(delta[:, 0]) < size[0] * 4.8)
            if np.count_nonzero(near) < 3:
                continue
            peers = np.array([p for p, _ in outlines])
            offsets = peers - center
            coplanar = (abs(offsets[:, 1] - slope * offsets[:, 0]) < max(2.0, size[1] * 0.30)) & (
                abs(offsets[:, 0]) < size[0] * 5.0
            )
            group = np.flatnonzero(coplanar)
            if len(group) >= 2:
                rows[i] = (len(group), group, slope, 1.0)

        if not rows:
            continue
        xy = np.array([p for p, _ in outlines])
        sz = np.array([np.ptp(p, axis=0) + 1 for _, p in outlines])
        grid = cv2.resize(views[f], (w, h)) * scales[f]
        zz = np.array(
            [
                np.nanmedian(grid[max(0, y - 1) : y + 2, max(0, x - 1) : x + 2])
                for x, y in xy.round().astype(int)
            ]
        )
        reliable = np.isfinite(zz) & (zz > 0)
        positions = _world(xy, np.where(reliable, zz, 1), cameras[f], intr, [w, h])
        witness = _witnesses(f, xy, positions, sz, frames, cameras, intr, boxes, original)
        for i, (rank, group, slope, _) in rows.items():
            group = group[reliable[group]]
            if len(group) < 2 or not reliable[i] or len(witness[i]) < 2:
                continue
            detail = intervals(image, known, outlines[i][1], slope)
            if detail is None:
                continue
            top, bottom, band, pad, timber = detail
            # Median pairwise slope avoids fitting a single foreground depth discontinuity.
            offsets = xy[group, 0] - xy[i, 0]
            others = group[np.abs(offsets) > 2]
            grad = np.median((1 / zz[others] - 1 / zz[i]) / (xy[others, 0] - xy[i, 0]))
            if not np.isfinite(grad):
                continue
            # Horizontally cut polygons may sit against a detector box rather than a seam.
            shape = outlines[i][1]
            height, width = (
                bottom - top,
                float(np.median([shape[1, 0] - shape[0, 0], shape[2, 0] - shape[3, 0]])),
            )
            if width < 4 or height < 3:
                continue
            refs = np.maximum(sz[i], [6, 6])
            dist = np.linalg.norm((centers - xy[i]) / refs, axis=1)
            match = int(dist.argmin())
            depth = float(zz[i])
            if dist[match] < 0.58:
                depth = float(z[match].mean())
            score = float(np.sqrt(np.prod(sz[i])) + 0.5 * len(witness[i]) + 0.18 * rank)
            candidates.append(
                (
                    score,
                    f,
                    i,
                    match,
                    dist[match],
                    (xy[i], width, top, bottom, band, slope, grad, depth),
                    witness[i],
                    pad,
                    timber,
                )
            )
    fixed = set()
    observations = []
    new = []
    for score, f, i, idx, dist, obs, witness, pad, timber in sorted(
        candidates, key=lambda v: -v[0]
    ):
        key = keys[idx]
        original_back = base[idx]
        is_new = dist >= 0.58
        if not is_new and key in fixed:
            continue
        if is_new and (dist < 0.82 or not obs[4]):
            continue
        center = _world(obs[0][None], obs[-1], cameras[f], intr, [w, h])[0]
        if new:
            points = np.array(new)
            q, z = uv(points, cameras[f], intr, [w, h])
            duplicate = (
                (np.linalg.norm((q - obs[0]) / [obs[1], max(3, obs[3] - obs[2])], axis=1) < 0.70)
                & (z > 0)
                & (abs(z - obs[-1]) < 0.26 * obs[-1])
            )
            if duplicate.any():
                continue
        profile = _profile(obs, original_back, intr, cameras[f], [w, h])
        if profile is None:
            continue
        vertices, shell = profile
        parts = chairs[key]
        if is_new:
            # Clone only the volumetric seat assembly nearest the measured profile, using
            # the observed screen scale. No new row support is extrapolated across an aisle.
            if dist > 2.1:
                continue
            idx_new = len(new) + 1
            old_center = original_back.mean(0)
            scale = np.clip(
                np.linalg.norm(np.array(vertices)[1] - vertices[0])
                / np.linalg.norm(original_back[1] - original_back[0]),
                0.67,
                1.6,
            )
            new_parts = {}
            for name, src in parts.items():
                if name == "Seat back frame":
                    continue
                o = copy.deepcopy(src)
                o["vertices"] = (
                    ((np.array(src["vertices"]) - old_center) * scale + center).round(5).tolist()
                )
                o.update(
                    seat_index=1000000 + idx_new,
                    source_observations=sorted([f, *witness]),
                    observation_status="measured repeated upholstery",
                )
                new_parts[name] = o
                items.append(o)
            parts = new_parts
            new.append(center)
        else:
            fixed.add(key)
        back = parts["Seat back"]
        back["vertices"] = vertices
        # Map local image luminance onto neutral clay while retaining the source ordering.
        delta = np.clip((float(np.mean(pad)) - float(np.mean(timber))) / 120, -0.35, 0.35)
        back["rgb"] = [0.43 + delta * 0.43] * 3
        trim = copy.deepcopy(back)
        trim.update(
            kind="Seat back frame",
            vertices=shell,
            rgb=[0.43 - delta * 0.43] * 3,
            source_observations=sorted([f, *witness]),
            observation_status="recurrent backrest trim and upholstery",
        )
        if obs[4]:
            items.append(trim)
        observations.append(
            dict(
                target_world=np.mean(vertices, axis=0).round(6).tolist(),
                frame=f,
                center=obs[0].round(2).tolist(),
                pad_width=round(obs[1], 2),
                pad_height=round(obs[3] - obs[2], 2),
                trim_height=int(obs[4]),
                views=witness,
                new_assembly=bool(is_new),
            )
        )
    return dict(refined=len(fixed), added=len(new), observations=observations)
