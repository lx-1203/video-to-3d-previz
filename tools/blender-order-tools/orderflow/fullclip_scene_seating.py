"""Fit rows and volumetric furniture to repeated image observations."""

import cv2
import numpy as np


def _rows(candidates):
    if len(candidates) < 3:
        return []
    c = np.array([x[0] for x in candidates])
    size = np.array([np.ptp(x[1], axis=0) for x in candidates], float)
    avail = np.ones(len(c), bool)
    rows = []
    while avail.sum() >= 3:
        best = []
        best_span = 0
        for i in np.flatnonzero(avail):
            for j in np.flatnonzero(avail & (c[:, 0] > c[i, 0] + size[i, 0] * 0.6)):
                vec = c[j] - c[i]
                if abs(vec[1]) > 0.9 * vec[0] or np.linalg.norm(vec) > size[i, 0] * 10:
                    continue
                direction = vec / np.linalg.norm(vec)
                normal = np.array([-direction[1], direction[0]])
                error = np.abs((c - c[i]) @ normal)
                scale = np.sqrt(
                    np.maximum(1.0, size[:, 0] * size[:, 1]) / (size[i, 0] * size[i, 1])
                )
                group = np.flatnonzero(
                    avail
                    & (error < np.maximum(2.1, size[:, 1] * 0.23))
                    & (scale > 0.48)
                    & (scale < 1.85)
                )
                group = group[np.argsort(c[group, 0])]
                # Spatially separated banks belong to different rows.
                chunks = np.split(
                    group, np.flatnonzero(np.diff(c[group, 0]) > size[i, 0] * 3.4) + 1
                )
                for ids in chunks:
                    if len(ids) < 3:
                        continue
                    span = float(c[ids[-1], 0] - c[ids[0], 0])
                    if span < size[i, 0] * 1.9:
                        continue
                    if len(ids) > len(best) or (len(ids) == len(best) and span > best_span):
                        best, best_span = ids, span
        if len(best) < 3:
            break
        rows.append(best)
        avail[best] = False
    return rows


def _box(center, right, forward, up, half_sizes):
    ext = [basis * value for basis, value in zip([right, forward, up], half_sizes)]
    return np.array(
        [
            center + ext[0] * sx + ext[1] * sy + ext[2] * sz
            for sz in (-1, 1)
            for sy in (-1, 1)
            for sx in (-1, 1)
        ]
    )


FACES = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]]


def _unit(v):
    return v / max(np.linalg.norm(v), 1e-8)


def _obj(kind, frame, verts, tone, rgb):
    return {
        "kind": kind,
        "source_frame": frame,
        "vertices": verts.round(5).tolist(),
        "faces": FACES,
        "tone": tone,
        "rgb": [rgb * 1.005, rgb, rgb * 0.993],
    }


def fitted_rows(
    candidates, image, depth, known, project, camera, intr, frame, *, measured_pitch=None
):
    rows = _rows(candidates) if measured_pitch is None else [np.arange(len(candidates))]
    h, w = known.shape
    objects = []
    summaries = []
    provisional = []
    for ids in rows:
        c = np.array([candidates[i][0] for i in ids])
        size = np.median([np.ptp(candidates[i][1], axis=0) for i in ids], axis=0)
        x, y = np.clip(np.rint(c).astype(int), [0, 0], [w - 1, h - 1]).T
        delta = np.diff(c[:, 0])
        pitch = delta[(delta > size[0] * 0.63) & (delta < size[0] * 1.85)]
        pitch = float(np.median(pitch)) if len(pitch) else size[0] * 1.18
        metric = float(np.median(depth[y, x])) * pitch / (w * intr[0, 0])
        m, b = np.polyfit(c[:, 0], c[:, 1], 1)
        provisional.append((float(x.min()), float(x.max()), m, b, size[1], metric))
    family = np.median([p[-1] for p in provisional]) if provisional else 0
    for rownum, ids in enumerate(rows):
        x0, x1, m, b, hp, metric = provisional[rownum]
        if len(rows) >= 3 and not family * 0.47 < metric < family * 1.85:
            continue
        if len(rows) >= 4:
            neighbors = 0
            for j, (l, r, ms, bs, other_h, _) in enumerate(provisional):
                xmid = (min(x1, r) + max(x0, l)) * 0.5
                sep = abs((m - ms) * xmid + b - bs)
                if (
                    j != rownum
                    and min(x1, r) - max(x0, l) > 0.17 * min(x1 - x0, r - l)
                    and abs(m - ms) < 0.34
                    and sep < 3.8 * max(hp, other_h)
                ):
                    neighbors += 1
            if not neighbors:
                continue
        c = np.array([candidates[i][0] for i in ids])
        wh = np.array([np.ptp(candidates[i][1], axis=0) for i in ids])
        good = np.array(
            [known[int(np.clip(y, 0, h - 1)), int(np.clip(x, 0, w - 1))] for x, y in c]
        )
        if good.sum() < 3:
            continue
        c, wh = c[good], wh[good]
        px, py = np.clip(np.rint(c).astype(int), [0, 0], [w - 1, h - 1]).T
        z = depth[py, px]
        if np.min(z) <= 0 or not np.isfinite(z).all():
            continue
        line = np.polynomial.polynomial.polyfit(c[:, 0], c[:, 1], 1)
        coef = np.polynomial.polynomial.polyfit(c[:, 0], 1 / z, 1)
        width, height = np.median(wh, axis=0)
        dif = np.diff(c[:, 0])
        plausible = dif[(dif > width * 0.63) & (dif < width * 1.85)]
        step = float(np.median(plausible)) if len(plausible) else width * 1.18
        centers = []
        for index, xy in enumerate(c):
            centers.append(xy[0])
            if index == len(c) - 1:
                continue
            gap = c[index + 1, 0] - xy[0]
            slots = round(gap / step)
            for k in range(1, slots):
                if slots > 3 or abs(gap / slots - step) > step * 0.17:
                    continue
                xx = xy[0] + gap * k / slots
                yy = np.polynomial.polynomial.polyval(xx, line)
                x, y = np.clip(np.rint([xx, yy]).astype(int), [0, 0], [w - 1, h - 1])
                if known[y, x]:
                    patch = image[max(0, y - 2) : y + 3, max(0, x - 2) : x + 3]
                    peer = np.median(image[py, px], axis=0)
                    if np.max(np.abs(np.median(patch.reshape(-1, 3), axis=0) - peer)) < 30:
                        centers.append(xx)
        xs = np.array(sorted(centers))
        uy = np.polynomial.polynomial.polyval(xs, line)
        zpred = np.clip(
            1 / np.maximum(np.polynomial.polynomial.polyval(xs, coef), 0.001),
            z.min() * 0.98,
            z.max() * 1.02,
        )
        centers3 = project(np.column_stack((xs, uy)), zpred)
        right = _unit(centers3[-1] - centers3[0])
        camup = -camera[:3, 1]
        up = _unit(camup - right * np.dot(camup, right))
        forward = _unit(np.cross(up, right))
        if np.dot(forward, camera[:3, 3] - centers3.mean(axis=0)) < 0:
            forward = -forward
        size = np.clip(np.median(zpred) * step / (w * intr[0, 0]), 0.1, 1.25)
        if measured_pitch is not None:
            size = np.clip(
                np.median(np.linalg.norm(np.diff(centers3, axis=0), axis=1)),
                measured_pitch * 0.74,
                measured_pitch * 1.28,
            )
        elif family > 0 and len(rows) >= 3:
            size = np.clip(size, family * 0.74, family * 1.28)
        back_h = np.clip(np.median(zpred) * height / (h * intr[1, 1]), size * 0.45, size * 1.3)
        underside = []
        first_object = len(objects)
        for seat_index, pos in enumerate(centers3):
            base = pos - up * back_h * 0.45 + forward * size * 0.27
            underside.append(base - up * size * 0.42)
            pieces = [
                ("Seat back", pos, (size * 0.45, size * 0.085, back_h * 0.52), 0.59),
                ("Seat cushion", base, (size * 0.44, size * 0.35, size * 0.07), 0.42),
                (
                    "Seat pedestal",
                    base - up * size * 0.27,
                    (size * 0.11, size * 0.12, size * 0.23),
                    0.32,
                ),
                (
                    "Seat armrest",
                    base + right * size * 0.44 + up * size * 0.17,
                    (size * 0.038, size * 0.29, size * 0.055),
                    0.39,
                ),
            ]
            for kind, center, half, v in pieces:
                piece = _obj(kind, frame, _box(center, right, forward, up, half), 4, v)
                piece["seat_index"] = seat_index
                objects.append(piece)
        if len(underside) > 1:
            center = (underside[0] + underside[-1]) * 0.5
            track = _box(
                center,
                right,
                forward,
                up,
                (
                    np.linalg.norm(underside[-1] - underside[0]) * 0.5 + size * 0.4,
                    size * 0.13,
                    size * 0.08,
                ),
            )
            objects.append(_obj("Seat row support", frame, track, 3, 0.35))
            tier = _box(
                center - up * size * 0.90,
                right,
                forward,
                up,
                (
                    np.linalg.norm(underside[-1] - underside[0]) * 0.5 + size * 0.56,
                    size * 0.84,
                    size * 0.84,
                ),
            )
            objects.append(_obj("Stepped seating platform", frame, tier, 3, 0.48))
        for piece in objects[first_object:]:
            piece["seat_row"] = rownum
        summaries.append(
            {
                "observed": len(c),
                "seats": len(centers3),
                "pitch_screen": step,
                "world_width": float(size),
            }
        )
    return objects, summaries


def fascia(image, depth, known, project, frame):
    """Match converging edges by horizontal support and fit an extruded balustrade."""
    h, w = known.shape
    seg = cv2.createLineSegmentDetector().detect(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))[0]
    if seg is None:
        return
    lines = []
    for x0, y0, x1, y1 in seg[:, 0]:
        if x0 > x1:
            x0, x1, y0, y1 = x1, x0, y1, y0
        if x1 - x0 < w * 0.17 or abs(y1 - y0) > 0.92 * (x1 - x0):
            continue
        m = (y1 - y0) / (x1 - x0)
        lines.append((x0, x1, m, y0 - m * x0))
    done = []
    for ref in sorted(lines, key=lambda a: a[0] - a[1]):
        left, right, m, n = ref
        center = (left + right) * 0.5
        group = []
        for a, b, v, c in lines:
            overlap = min(right, b) - max(left, a)
            if (
                overlap < 0.57 * (right - left)
                or overlap < 0.59 * (b - a)
                or abs(m - v) > 0.48
                or m * v < -0.006
            ):
                continue
            if abs((v - m) * center + c - n) > h * 0.39:
                continue
            group.append((a, b, v, c))
        if len(group) < 4:
            continue
        x0 = float(np.percentile([a for a, _, _, _ in group], 24))
        x1 = float(np.percentile([b for _, b, _, _ in group], 76))
        if any(min(x1, b) - max(x0, a) > 0.66 * (x1 - x0) for a, b in done):
            continue
        done.append((x0, x1))
        ys = np.array([[v * x0 + c, v * x1 + c] for _, _, v, c in group])
        order = np.argsort(ys.mean(axis=1))
        levels = ys[order]
        top, bottom = levels[0], levels[-1]
        if np.mean(bottom - top) < h * 0.045:
            continue
        points = np.linspace(x0, x1, 40)
        fraction = np.linspace(0, 1, 40)
        upper = top[0] * (1 - fraction) + top[1] * fraction
        lower = bottom[0] * (1 - fraction) + bottom[1] * fraction
        xx = np.tile(points, 3)
        yy = np.concatenate(
            [upper * 0.75 + lower * 0.25, (upper + lower) * 0.5, upper * 0.25 + lower * 0.75]
        )
        px, py = np.clip(np.rint(np.column_stack([xx, yy])).astype(int), [0, 0], [w - 1, h - 1]).T
        keep = known[py, px]
        if keep.sum() < 10:
            continue
        z = depth[py[keep], px[keep]]
        coef = np.polynomial.polynomial.polyfit(xx[keep], 1 / z, 1)
        zd = np.clip(
            1 / np.maximum(0.001, np.polynomial.polynomial.polyval([x0, x1], coef)),
            z.min() * 0.98,
            z.max() * 1.02,
        )

        def member(a, b, kind, rgb, mult, thick):
            uv = np.array([[x0, a[0]], [x1, a[1]], [x1, b[1]], [x0, b[0]]])
            ds = np.r_[zd, zd[::-1]] * mult
            vertices = np.vstack([project(uv, ds), project(uv, ds + np.median(ds) * thick)])
            return {
                "kind": kind,
                "source_frame": frame,
                "vertices": vertices.round(5).tolist(),
                "tone": 3,
                "rgb": [rgb] * 3,
                "faces": [
                    [3, 2, 1, 0],
                    [4, 5, 6, 7],
                    [0, 1, 5, 4],
                    [1, 2, 6, 5],
                    [2, 3, 7, 6],
                    [3, 0, 4, 7],
                ],
            }

        yield member(top, bottom, "Balustrade panel", 0.54, 0.993, 0.045)
        yield member(top - 1.6, top + 1.6, "Handrail", 0.36, 0.982, 0.023)
        previous = top
        for edge in levels[1:-1]:
            if (edge - previous).mean() > max(5.0, np.mean(bottom - top) * 0.22):
                yield member(edge - 0.7, edge + 0.7, "Panel moulding", 0.37, 0.986, 0.015)
                previous = edge
        walkway = np.maximum(h * 0.028, (bottom - top) * 0.48)
        near_uv = np.array([[x0, top[0]], [x1, top[1]]])
        far_uv = near_uv.copy()
        far_uv[:, 1] -= walkway
        surface = np.vstack([project(near_uv, zd), project(far_uv, zd * 1.19)[::-1]])
        down = project(np.vstack([near_uv, far_uv[::-1]]) + [0, 2.3], np.r_[zd, zd[::-1] * 1.19])
        yield {
            "kind": "Gallery walkway",
            "source_frame": frame,
            "vertices": np.vstack([surface, down]).round(5).tolist(),
            "tone": 3,
            "rgb": [0.65] * 3,
            "faces": [
                [3, 2, 1, 0],
                [4, 5, 6, 7],
                [0, 1, 5, 4],
                [1, 2, 6, 5],
                [2, 3, 7, 6],
                [3, 0, 4, 7],
            ],
        }


def hall(image, depth, known, project, frame):
    """Infer simple rear wall and floor extents from measured depth; retain visible portals."""
    h, w = known.shape
    far = float(np.percentile(depth[known], 97)) * 1.28
    foreground = known.copy()
    foreground[: int(0.78 * h)] = False
    near = float(np.percentile(depth[foreground], 40)) * 1.10 if foreground.any() else far * 0.65

    def panel(uv, z, thick, name, color):
        a = project(uv, z)
        b = project(uv, np.array(z) + thick)
        return {
            "kind": name,
            "source_frame": frame,
            "tone": 3,
            "rgb": [color] * 3,
            "vertices": np.vstack([a, b]).round(5).tolist(),
            "faces": [
                [3, 2, 1, 0],
                [4, 5, 6, 7],
                [0, 1, 5, 4],
                [1, 2, 6, 5],
                [2, 3, 7, 6],
                [3, 0, 4, 7],
            ],
        }

    wall = panel(
        [
            [-w * 0.35, -h * 0.35],
            [w * 1.35, -h * 0.35],
            [w * 1.35, h * 1.35],
            [-w * 0.35, h * 1.35],
        ],
        [far] * 4,
        far * 0.035,
        "Room wall",
        0.68,
    )
    yield wall
    floor = panel(
        [[-w * 0.4, h * 0.64], [w * 1.4, h * 0.64], [w * 1.4, h * 1.4], [-w * 0.4, h * 1.4]],
        [far * 0.97, far * 0.97, near, near],
        near * 0.045,
        "Floor and circulation",
        0.50,
    )
    yield floor
    g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    top = known.copy()
    top[int(0.52 * h) :] = False
    if not top.any():
        return
    mask = ((g < np.percentile(g[top], 21)) & top).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, ww, hh = cv2.boundingRect(c)
        if not (
            w * 0.038 < ww < w * 0.42
            and h * 0.09 < hh < h * 0.50
            and cv2.contourArea(c) / (ww * hh) > 0.75
        ):
            continue
        if np.mean(known[y : y + hh, x : x + ww]) < 0.93:
            continue
        uv = np.array([[x, y], [x + ww, y], [x + ww, y + hh], [x, y + hh]])
        yield panel(uv, [far * 0.97] * 4, far * 0.008, "Wall opening", 0.33)


def join_fascia_corners(items, camera, intr, w, h):
    """Weld independently measured, adjacent facade spans along their common corner."""
    groups = []
    for item in items:
        if item["kind"] == "Balustrade panel":
            groups.append([])
        groups[-1].append(item)

    def project(points):
        q = (points - camera[:3, 3]) @ camera[:3, :3]
        uv = (q[:, :2] / q[:, 2:] * [intr[0, 0], intr[1, 1]] + [intr[0, 2], intr[1, 2]]) * [w, h]
        return uv, q[:, 2]

    def weld(a, b):
        right, left = [1, 2, 5, 6], [0, 3, 4, 7]
        va, vb = np.array(a["vertices"]), np.array(b["vertices"])
        corner = np.mean([va[right], vb[left]], axis=0)
        va[right], vb[left] = corner, corner
        a["vertices"], b["vertices"] = va.round(5).tolist(), vb.round(5).tolist()
        a["shared_corner"], b["shared_corner"] = True, True

    groups.sort(key=lambda group: project(np.array(group[0]["vertices"]))[0][0, 0])
    for near, far in zip(groups, groups[1:]):
        va, vb = np.array(near[0]["vertices"]), np.array(far[0]["vertices"])
        a, za = project(va)
        b, zb = project(vb)
        offset = np.abs(a[1] - b[0])
        height = min(a[2, 1] - a[1, 1], b[3, 1] - b[0, 1])
        if offset[0] > w * 0.025 or offset[1] > h * 0.025 or height < 4:
            continue
        if abs(np.log(za[1] / zb[0])) > 0.31 or abs(a[2, 1] - b[3, 1]) > height * 0.55:
            continue
        if abs(np.dot(_unit(va[1] - va[0]), _unit(vb[1] - vb[0]))) > 0.99:
            continue
        available = list(far)
        for part in near:
            matches = [p for p in available if p["kind"] == part["kind"]]
            if not matches:
                continue
            end, _ = project(np.array(part["vertices"]))

            def separation(p):
                p_start, _ = project(np.array(p["vertices"]))
                return abs(end[1:3, 1].mean() - p_start[[0, 3], 1].mean())

            best = min(matches, key=separation)
            if part["kind"] == "Panel moulding" and separation(best) > max(3.0, height * 0.17):
                continue
            weld(part, best)
            available.remove(best)
    return items


def fuse_banks(items, cameras, intr, width, height):
    """Retain a single rigid chair across adjacent views only after two-view reprojection checks."""
    from collections import defaultdict

    parts = defaultdict(list)
    back = {}
    for i, o in enumerate(items):
        if "seat_index" not in o:
            continue
        key = (o["source_frame"], o["seat_row"], o["seat_index"])
        parts[key].append(i)
        if o["kind"] == "Seat back":
            back[key] = np.array(o["vertices"], float)
    views = sorted({k[0] for k in back})
    chains = {}

    def projection(points, camera):
        xyz = (points - camera[:3, 3]) @ camera[:3, :3]
        if np.any(xyz[:, 2] <= 0.01):
            return None
        return (xyz[:, :2] / xyz[:, 2:] * [intr[0, 0], intr[1, 1]] + [intr[0, 2], intr[1, 2]]) * [
            width,
            height,
        ]

    for prev, cur in zip(views, views[1:]):
        a = [k for k in back if k[0] == prev]
        b = [k for k in back if k[0] == cur]
        projected = [projection(back[k], cameras[cur]) for k in b]
        choices = []
        for k in a:
            q = projection(back[k], cameras[cur])
            if q is None:
                continue
            diam = max(3.0, np.linalg.norm(np.ptp(q, axis=0)))
            for j, target in enumerate(b):
                p = projected[j]
                if p is None:
                    continue
                other = max(3.0, np.linalg.norm(np.ptp(p, axis=0)))
                if abs(np.log(other / diam)) > 0.42:
                    continue
                distance = np.linalg.norm(p.mean(0) - q.mean(0)) / min(other, diam)
                if distance > 0.50:
                    continue
                size = np.linalg.norm(np.ptp(back[target], axis=0))
                delta = np.linalg.norm(back[k].mean(0) - back[target].mean(0)) / max(0.03, size)
                if delta < 1.75:
                    choices.append((distance + 0.16 * delta, k, target))
        left, right = set(), set()
        for score, k, target in sorted(choices, key=lambda v: v[0]):
            if k in left or target in right:
                continue
            left.add(k)
            right.add(target)
            head = chains.get(k, k)
            chains[target] = head
    groups = defaultdict(list)
    for k in back:
        groups[chains.get(k, k)].append(k)
    drop = set()
    fitted = []
    for head, keys in groups.items():
        if len(keys) < 2:
            continue
        observations = np.array([back[k].mean(0) for k in keys])
        size = np.median([np.linalg.norm(np.ptp(back[k], axis=0)) for k in keys])
        center = np.median(observations, axis=0)
        lhs = np.eye(3) * 0.035
        rhs = center * 0.035
        for k, target in zip(keys, observations):
            cam = cameras[k[0]]
            ray = _unit(target - cam[:3, 3])
            ortho = np.eye(3) - np.outer(ray, ray)
            lhs += ortho
            rhs += ortho @ cam[:3, 3]
        solution = np.linalg.solve(lhs, rhs)
        errors = []
        for k in keys:
            projected = projection(back[k], cameras[k[0]])
            estimate = projection(solution.reshape(1, 3), cameras[k[0]])
            if estimate is None or projected is None:
                errors.append(1000.0)
            else:
                errors.append(
                    np.linalg.norm(estimate[0] - projected.mean(0))
                    / max(2.0, np.linalg.norm(np.ptp(projected, axis=0)))
                )
        if (
            max(errors) > 0.33
            or max(np.linalg.norm(observations - solution, axis=1)) > size * 1.15
        ):
            continue
        best = keys[int(np.argmin(np.linalg.norm(observations - solution, axis=1)))]
        movement = solution - back[best].mean(0)
        shown = [items[parts[k][0]] for k in keys]
        for idx in parts[best]:
            piece = items[idx]
            piece["vertices"] = (np.asarray(piece["vertices"]) + movement).round(5).tolist()
            piece["visible_first"] = min(o["visible_first"] for o in shown)
            piece["visible_last"] = max(o["visible_last"] for o in shown)
            piece["source_observations"] = [k[0] for k in keys]
        for k in keys:
            if k != best:
                drop.update(parts[k])
        fitted.append(
            {
                "observations": [k[0] for k in keys],
                "maximum_relative_reprojection_error": max(errors),
            }
        )
    return [o for i, o in enumerate(items) if i not in drop], fitted


def permanent_room(items, cameras, intr, w, h):
    """Fuse view observations into one persistent room, with no within-shot visibility cuts."""
    from collections import defaultdict

    if not any(o["kind"] == "Seat back" and "seat_row" in o for o in items):
        return items, {}
    chair = defaultdict(list)
    support = defaultdict(list)
    props = []
    skip = {
        "Room wall",
        "Floor and circulation",
        "Observed planar shell",
        "Observed architectural shell",
        "Backing • inferred extent",
    }
    for item in items:
        kind = item["kind"]
        if "seat_index" in item:
            chair[(item["source_frame"], item["seat_row"], item["seat_index"])].append(item)
        elif "seat_row" in item:
            support[(item["source_frame"], item["seat_row"])].append(item)
        elif kind not in skip:
            props.append(item)

    def uv(x, frame):
        c = cameras[frame]
        q = (x - c[:3, 3]) @ c[:3, :3]
        z = q[..., 2]
        return (
            q[..., :2] / np.maximum(z[..., None], 0.001) * [intr[0, 0], intr[1, 1]]
            + [intr[0, 2], intr[1, 2]]
        ) * [w, h], z

    result = []
    prior = []
    retained = defaultdict(list)
    ranked = []
    for key, pieces in chair.items():
        back = next(o for o in pieces if o["kind"] == "Seat back")
        observation = min(back.get("source_observations", [key[0]]))
        ranked.append((observation, -len(back.get("source_observations", [])), key, pieces, back))
    for obs, _, key, pieces, back in sorted(ranked, key=lambda x: x[:3]):
        verts = np.array(back["vertices"])
        pos = verts.mean(axis=0)
        projected, z = uv(verts, obs)
        dia = max(3.0, np.linalg.norm(np.ptp(projected, axis=0)))
        size = np.linalg.norm(np.ptp(verts, axis=0))
        duplicate = False
        if prior:
            points = np.array([x[0] for x in prior])
            olduv, oldz = uv(points, obs)
            dist = np.linalg.norm(points - pos, axis=1)
            duplicate = np.any(
                (dist < size * 0.68)
                | (
                    (np.linalg.norm(olduv - projected.mean(0), axis=1) < dia * 0.61)
                    & (oldz > 0)
                    & (np.abs(oldz - z.mean()) < z.mean() * 0.40)
                )
            )
        if duplicate:
            continue
        result.extend(pieces)
        prior.append((pos, obs))
        retained[key[:2]].append((pos, size))
    for key, group in retained.items():
        for obj in support[key]:
            points = np.array(obj["vertices"])
            axis = _unit(points[1] - points[0])
            seat_points = np.array([p for p, _ in group])
            a, b = (seat_points @ axis).min(), (seat_points @ axis).max()
            margin = np.median([s for _, s in group]) * 0.35
            old = points @ axis
            # Reduce a measured platform to the span of the seats it actually supports.
            new = np.where(old > np.median(old), b + margin, a - margin)
            obj["vertices"] = (points + (new - old)[:, None] * axis).round(5).tolist()
            result.append(obj)
    # Keep the widest observation as the construction datum for panels. Newly exposed
    # angular sectors extend the room without adding a second copy in an already observed view.
    spans = defaultdict(list)
    for obj in sorted(props, key=lambda o: o["source_frame"]):
        kind = obj["kind"]
        if kind not in {
            "Handrail",
            "Balustrade panel",
            "Panel moulding",
            "Gallery walkway",
            "Wall opening",
            "Rail or frame",
        }:
            continue
        points = np.array(obj["vertices"])
        center = points.mean(axis=0)
        frame = obj["source_frame"]
        if kind in {"Handrail", "Balustrade panel", "Panel moulding", "Gallery walkway"}:
            group_family = kind
        else:
            group_family = "Other architecture"
        occupied = False
        for oldpts, oldframe in spans[group_family]:
            if oldframe == frame:
                continue
            uv_old, oldz = uv(oldpts, frame)
            uv_new, z = uv(points, frame)
            if oldz.min() <= 0 or z.min() <= 0:
                continue
            lo, hi = uv_new.min(0), uv_new.max(0)
            ol, oh = uv_old.min(0), uv_old.max(0)
            mins = np.maximum(hi - lo, 2)
            overlap = np.maximum(0, np.minimum(hi, oh) - np.maximum(lo, ol)) / mins
            oldcenter, _ = uv(center.reshape(1, 3), oldframe)
            inside = np.all((oldcenter > np.array([0, 0])) & (oldcenter < np.array([w, h])))
            if (overlap[0] > 0.47 and overlap[1] > 0.35) or (
                inside
                and kind in {"Handrail", "Balustrade panel", "Panel moulding", "Gallery walkway"}
            ):
                occupied = True
                break
        if not occupied:
            spans[group_family].append((points, frame))
            result.append(obj)
    first, last = min(cameras), max(cameras)
    if result:
        pts = np.vstack([np.array(o["vertices"]) for o in result])
        camera_centers = np.array([c[:3, 3] for c in cameras.values()])
        # One closed room with a continuous floor and walls enclosing the observed scene.
        cam = cameras[first]
        up = -cam[:3, 1]
        right = cam[:3, 0]
        toward = cam[:3, 2]
        basis = np.column_stack((right, toward, up))
        coords = np.vstack([pts, camera_centers]) @ basis
        low, high = coords.min(0), coords.max(0)
        ext = np.maximum(high - low, 0.5)
        low[:2] -= ext[:2] * [0.12, 0.14]
        high[:2] += ext[:2] * [0.12, 0.11]
        low[2] -= max(0.025, ext[2] * 0.012)
        high[2] += max(0.6, ext[2] * 0.34)
        middle = (low + high) / 2
        half = (high - low) / 2
        thickness = max(0.025, min(half[:2]) * 0.012)
        for axis, sign, name, tone in [
            (2, -1, "Room floor", 0.49),
            (1, 1, "Room wall", 0.67),
            (0, -1, "Room side wall", 0.61),
            (0, 1, "Room side wall", 0.61),
            (1, -1, "Room rear wall", 0.63),
        ]:
            cc = middle.copy()
            cc[axis] = (low[axis] if sign < 0 else high[axis]) + sign * thickness
            sizes = half.copy()
            sizes[axis] = thickness
            verts = _box(basis @ cc, right, toward, up, sizes)
            result.append(_obj(name, first, verts, 3, tone))
    for o in result:
        o["visible_first"], o["visible_last"] = first, last
    return result, {
        "persistent_chairs": len(prior),
        "observed_seat_assemblies": len(chair),
        "physical_parts": len(result),
        "visibility_changes_inside_shot": 0,
    }


def fill_observed_row_gaps(items, frames, cameras, intr, boxes, original):
    """Add a missing physical chair only inside a supported tier and with recurrent source evidence."""
    import copy
    from collections import defaultdict

    h, w = next(iter(frames.values())).shape[:2]
    assemblies = defaultdict(dict)
    tiers = {}
    for o in items:
        if "seat_row" not in o:
            continue
        key = (o["source_frame"], o["seat_row"])
        if o["kind"] == "Stepped seating platform":
            tiers[key] = o
        elif "seat_index" in o:
            assemblies[(*key, o["seat_index"])][o["kind"]] = o
    chairs = {}
    for key, parts in assemblies.items():
        if "Seat back" in parts:
            vs = np.asarray(parts["Seat back"]["vertices"])
            chairs[key] = (vs.mean(0), _unit(vs[1] - vs[0]), np.linalg.norm(vs[1] - vs[0]), vs)
    if not chairs:
        return [], []
    step_frames = max(5, len(frames) // 24)
    times = sorted(
        set([*range(min(frames), max(frames) + 1, step_frames), *{k[0] for k in tiers}])
    )
    observed = []
    additions = []
    confirmed = [p[0] for p in chairs.values()]
    seen_gaps = set()
    dst = np.array([[0, 0], [19, 0], [19, 19], [0, 19]], np.float32)

    def sample(vertices, frame, shift=None):
        camera = cameras[frame]
        xyz = (vertices - camera[:3, 3]) @ camera[:3, :3]
        if np.min(xyz[:, 2]) <= 0.03:
            return None
        face = (
            [0, 1, 5, 4]
            if xyz[[0, 1, 5, 4], 2].mean() < xyz[[2, 3, 7, 6], 2].mean()
            else [2, 3, 7, 6]
        )
        uv = (
            xyz[face, :2] / xyz[face, 2:] * [intr[0, 0], intr[1, 1]] + [intr[0, 2], intr[1, 2]]
        ) * [w, h]
        if shift is not None:
            uv += shift
        center = uv.mean(0)
        uv = center + (uv - center) * 1.35
        if (
            np.any(uv < 1)
            or np.any(uv > [w - 2, h - 2])
            or cv2.contourArea(uv.astype(np.float32)) < 20
        ):
            return None
        for box in boxes.get(frame, []):
            x0, y0, x1, y1 = np.array(box) * np.tile([w / original[0], h / original[1]], 2)
            if x0 < center[0] < x1 and y0 < center[1] < y1:
                return None
        transform = cv2.getPerspectiveTransform(uv.astype(np.float32), dst)
        patch = cv2.warpPerspective(frames[frame], transform, (20, 20))
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if gray.std() < 4:
            return None
        lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)
        return (
            (gray - gray.mean()) / (gray.std() + 1e-5),
            np.median(lab[4:16, 4:16].reshape(-1, 3), axis=0),
        )

    for tag, tier in sorted(tiers.items()):
        own = [k for k in chairs if k[:2] == tag]
        if len(own) < 2:
            continue
        origin, axis, size, _ = chairs[own[0]]
        size = float(np.median([chairs[k][2] for k in own]))
        allcenters = []
        tier_v = np.array(tier["vertices"]) @ axis
        for key, (center, other, other_size, vs) in chairs.items():
            offset = center - origin
            distance = np.linalg.norm(offset - axis * np.dot(offset, axis))
            if (
                distance < size * 0.49
                and abs(np.dot(axis, other)) > 0.9
                and tier_v.min() <= center @ axis <= tier_v.max()
                and 0.63 < other_size / size < 1.58
            ):
                allcenters.append((center @ axis, key))
        ordered = sorted(allcenters, key=lambda e: e[0])
        if len(ordered) < 2:
            continue
        gaps = np.diff([x for x, k in ordered])
        plausible = gaps[(gaps > size * 0.84) & (gaps < size * 1.55)]
        pitch = float(np.median(plausible)) if len(plausible) else size / 0.90
        for (x, ka), (y, kb) in zip(ordered, ordered[1:]):
            endpoints = tuple(sorted([ka, kb]))
            if endpoints in seen_gaps:
                continue
            seen_gaps.add(endpoints)
            count = round((y - x) / pitch)
            if not 2 <= count <= 6 or abs((y - x) / count - pitch) > pitch * 0.19:
                continue
            pa, pb = chairs[ka][0], chairs[kb][0]
            ref = chairs[ka][3]
            refs = {}
            for f in times:
                a, b = sample(ref, f), sample(chairs[kb][3], f)
                if a is not None or b is not None:
                    refs[f] = [p for p in (a, b) if p is not None]
            for slot in range(1, count):
                center = pa + (pb - pa) * (slot / count)
                if np.min(np.linalg.norm(np.array(confirmed) - center, axis=1)) < size * 0.57:
                    continue
                delta = center - pa
                evidence = []
                for f, peers in refs.items():
                    best = -1
                    for offset in [(0.0, 0.0), (-1.4, 0.0), (1.4, 0.0), (0.0, -1.4), (0.0, 1.4)]:
                        patch = sample(ref + delta, f, offset)
                        if patch is None:
                            continue
                        for texture, color in peers:
                            diff = np.abs(color - patch[1])
                            if diff[0] > 48 or np.linalg.norm(diff[1:]) > 15:
                                continue
                            best = max(best, float(np.mean(texture * patch[0])))
                    if best > 0.42:
                        evidence.append((f, round(best, 3)))
                if len(evidence) < 2 or evidence[-1][0] - evidence[0][0] < 6:
                    observed.append(
                        {
                            "row": list(tag),
                            "slots_across_gap": count,
                            "slot": slot,
                            "supported": False,
                            "views": evidence,
                        }
                    )
                    continue
                confirmed.append(center)
                idx = 100000 + len(additions)
                for o in assemblies[ka].values():
                    piece = copy.deepcopy(o)
                    piece["seat_index"] = idx
                    piece["seat_row"] = tag[1]
                    piece["source_frame"] = tag[0]
                    piece["vertices"] = (np.asarray(o["vertices"]) + delta).round(5).tolist()
                    piece["observation_status"] = "inferred repeated seat"
                    piece["source_observations"] = [f for f, _ in evidence]
                    additions.append(piece)
                observed.append(
                    {
                        "row": list(tag),
                        "slots_across_gap": count,
                        "slot": slot,
                        "supported": True,
                        "views": evidence,
                    }
                )
    return additions, observed


def repeated_rows(image, known, candidates):
    if len(candidates) < 3:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    center = np.array([c for c, _ in candidates])
    proposals = []
    lines = cv2.createLineSegmentDetector().detect(gray)[0]
    if lines is None:
        return []
    edges = lines.reshape(-1, 2, 2)
    edge_vec = edges[:, 1] - edges[:, 0]
    edge_len = np.maximum(np.linalg.norm(edge_vec, axis=1), 0.001)
    edge_dir = edge_vec / edge_len[:, None]
    seeds = list(candidates)
    window = np.zeros_like(gray)
    for xy, poly in candidates:
        dx, dy = np.rint(np.clip(np.ptp(poly, axis=0) * 3.2, 12, 45)).astype(int)
        x, y = np.rint(xy).astype(int)
        cv2.rectangle(window, (x - dx, y - dy), (x + dx, y + dy), 255, -1)
    corners = cv2.goodFeaturesToTrack(
        cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray),
        400,
        0.007,
        6,
        mask=window,
        blockSize=5,
    )
    for xy in [] if corners is None else corners[:, 0]:
        distances = np.linalg.norm(center - xy, axis=1)
        ref = np.argmin(distances)
        if distances[ref] < 3:
            continue
        dims = np.clip(np.ptp(candidates[ref][1], axis=0), 5, 14)
        seeds.append((xy, xy + np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * dims * 0.50))
    for i, (xy, poly) in enumerate(seeds):
        wh = np.ptp(poly, axis=0)
        if not 4 <= min(wh) or max(wh) > w * 0.055 or wh[0] / wh[1] > 2.6:
            continue
        a, b = np.rint(np.clip(wh * 0.65, 4, 12)).astype(int)
        x, y = np.rint(xy).astype(int)
        if x - a < 0 or x + a >= w or y - b < 0 or y + b >= h:
            continue
        template = gray[y - b : y + b + 1, x - a : x + a + 1]
        if np.std(template) < 5:
            continue
        response = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        response = cv2.copyMakeBorder(response, b, b, a, a, cv2.BORDER_CONSTANT, value=-1)
        response[~known] = -1
        maximal = response == cv2.dilate(response, np.ones((5, 5), np.uint8))
        yy, xx = np.where(maximal & (response > 0.64))
        pos = np.column_stack((xx, yy))
        vec = pos - xy
        dist = np.linalg.norm(vec, axis=1)
        adjacent = (
            (vec[:, 0] > 0.25 * dist)
            & (dist > max(5, wh[0] * 0.72))
            & (dist < max(11, np.linalg.norm(wh) * 2.25))
        )
        for idx in np.flatnonzero(adjacent):
            direction = vec[idx] / dist[idx]
            step = dist[idx]
            hits = (vec @ direction) / step
            residual = np.abs(vec[:, 0] * direction[1] - vec[:, 1] * direction[0])
            valid = (
                (np.abs(hits - np.rint(hits)) < 0.25)
                & (residual < max(1.8, wh[1] * 0.20))
                & (np.abs(hits) <= 8)
            )
            selected = {}
            for k in np.flatnonzero(valid):
                n = round(hits[k])
                if (
                    n not in selected
                    or response[yy[k], xx[k]] > response[yy[selected[n]], xx[selected[n]]]
                ):
                    selected[n] = k
            ns = sorted(selected)
            if 0 not in selected or 1 not in selected:
                continue
            chunk = next(g for g in np.split(ns, np.flatnonzero(np.diff(ns) > 1) + 1) if 0 in g)
            if len(chunk) < 4:
                continue
            points = pos[[selected[j] for j in chunk]]
            bounds = (points - xy) @ direction
            along = (edges - xy) @ direction
            normal = np.array([-direction[1], direction[0]])
            offset = np.abs((edges.mean(1) - xy) @ normal)
            overlap = np.minimum(along.max(1), bounds.max()) - np.maximum(
                along.min(1), bounds.min()
            )
            rail = (
                (np.abs(edge_dir @ direction) > 0.982)
                & (offset < max(4.5, wh[1] * 0.93))
                & (overlap > step * 0.68)
                & (edge_len > step * 1.25)
            )
            if np.sum(np.maximum(0, overlap[rail])) < np.ptp(bounds) * 0.45:
                continue
            mid = np.rint(0.5 * (points[:-1] + points[1:])).astype(int)
            prom = np.mean(response[points[:, 1], points[:, 0]]) - np.mean(
                response[mid[:, 1], mid[:, 0]]
            )
            if prom < 0.22:
                continue
            # A separate measured chair contour must support the same run.
            confirmed = sum(
                np.any(np.linalg.norm(center - q, axis=1) < max(2.6, step * 0.43)) for q in points
            )
            if confirmed < 1 or (i >= len(candidates) and confirmed < 2):
                continue
            proposals.append(
                (len(chunk) + confirmed * 0.14 + prom * 0.26, points, poly - xy, step)
            )
    keep = []
    for rank, points, shape, step in sorted(proposals, key=lambda p: -p[0]):
        nmatched = 0
        for p in points:
            if any(np.min(np.linalg.norm(q - p, axis=1)) < step * 0.44 for q, _, _ in keep):
                nmatched += 1
        if nmatched > len(points) * 0.37:
            continue
        keep.append((points, shape, step))
    extended = []
    for points, shape, step in keep:
        pts = list(points.astype(float))
        size = np.clip(np.ptp(shape, axis=0) * 0.68, 4, 12)
        for end in (0, 1):
            if not end:
                pts.reverse()
            for k in range(18):
                d = pts[-1] - pts[-2]
                norm = np.linalg.norm(d)
                if norm < 4:
                    break
                basis = d / norm
                rad = np.maximum(3, np.rint(size * np.clip(norm / step, 0.7, 1.6))).astype(int)
                x, y = np.rint(pts[-1]).astype(int)
                a, b = rad
                if x - a < 0 or x + a >= w or y - b < 0 or y + b >= h:
                    break
                template = gray[y - b : y + b + 1, x - a : x + a + 1]
                guess = pts[-1] + d
                off = np.ceil(rad + max(4, norm * 0.40)).astype(int)
                lo = np.maximum(0, np.floor(guess - off).astype(int))
                hi = np.minimum([w, h], np.ceil(guess + off + 1).astype(int))
                search = gray[lo[1] : hi[1], lo[0] : hi[0]]
                if np.any(hi - lo < 2 * rad + 1):
                    break
                response = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
                v, u = np.indices(response.shape)
                px = u + lo[0] + a
                py = v + lo[1] + b
                vx, vy = px - x, py - y
                along = vx * basis[0] + vy * basis[1]
                lateral = np.abs(vx * basis[1] - vy * basis[0])
                allowed = (
                    (along > norm * 0.73)
                    & (along < norm * 1.30)
                    & (lateral < max(2, norm * 0.14))
                    & known[py, px]
                )
                if not np.any(allowed):
                    break
                response[~allowed] = -1
                best = np.unravel_index(response.argmax(), response.shape)
                if response[best] < 0.70:
                    break
                point = np.array([px[best], py[best]], float)
                mid = (point + pts[-1]) * 0.5
                ref = cv2.getRectSubPix(gray, tuple(2 * rad + 1), tuple(mid))
                midscore = cv2.matchTemplate(ref, template, cv2.TM_CCOEFF_NORMED)[0, 0]
                if response[best] - midscore < 0.19:
                    break
                pts.append(point)
            if not end:
                pts.reverse()
        extended.append((np.asarray(pts), shape, step))
    return extended
