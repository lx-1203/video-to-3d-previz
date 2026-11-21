"""Convert multi-view metric samples into untextured, editable mesh groups."""

import cv2
import numpy as np


def _projector(c2w, intrinsics, width, height):
    def project(uv, z):
        uv = np.asarray(uv, float)
        z = np.asarray(z, float)
        direction = np.column_stack(
            (
                (uv[:, 0] / width - intrinsics[0, 2]) / intrinsics[0, 0],
                (uv[:, 1] / height - intrinsics[1, 2]) / intrinsics[1, 1],
                np.ones(len(uv)),
            )
        )
        return direction * z.reshape(-1, 1) @ c2w[:3, :3].T + c2w[:3, 3]

    return project


def _solid(uv, depth, project, thickness, frame, kind, tone):
    uv = np.asarray(uv, float)
    front = project(uv, depth)
    back = project(uv, np.array(depth) + thickness)
    n = len(front)
    faces = [list(range(n - 1, -1, -1)), list(range(n, n * 2))]
    faces.extend([[a, (a + 1) % n, n + (a + 1) % n, n + a] for a in range(n)])
    return {
        "kind": kind,
        "source_frame": frame,
        "tone": int(tone),
        "vertices": np.vstack((front, back)).round(5).tolist(),
        "faces": faces,
    }


def _seating_samples(image, detections, original):
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(float)
    seeds = []
    h, w = image.shape[:2]
    for item in detections:
        if item["semantic_class"] != "chair":
            continue
        x0, y0, x1, y1 = np.rint(
            np.array(item["box_xyxy"]) * np.tile([w / original[0], h / original[1]], 2)
        ).astype(int)
        interior = lab[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)]
        if interior.size:
            # Supply multiple upholstery candidates as seeds; repeated shape filters follow.
            for percentile in (35, 65, 85):
                lightness = np.percentile(interior[:, :, 0], percentile)
                pixels = interior[np.abs(interior[:, :, 0] - lightness) < 8]
                if len(pixels):
                    seeds.append(np.median(pixels, axis=0))
    return seeds


def _seat_candidates(image, known, seeds):
    if not seeds:
        return []
    h, w = image.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(image, (3, 3), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    found = []
    for seed in seeds:
        delta = lab - seed
        dist = (delta[:, :, 0] / 85) ** 2 + (delta[:, :, 1] / 7) ** 2 + (delta[:, :, 2] / 12) ** 2
        component = ((dist < 1) & known).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if not 0.00012 * w * h < area < 0.035 * w * h:
                continue
            rect = cv2.minAreaRect(contour)
            a, b = rect[1]
            if min(a, b) < 3 or max(a, b) / min(a, b) > 5.5 or area / (a * b) < 0.58:
                continue
            hull = cv2.convexHull(contour)
            poly = cv2.approxPolyDP(hull, 0.04 * cv2.arcLength(hull, True), True).reshape(-1, 2)
            if not 4 <= len(poly) <= 7:
                continue
            center = np.array(rect[0])
            if any(
                np.linalg.norm(old[0] - center) < 0.40 * max(5, np.sqrt(area)) for old in found
            ):
                continue
            found.append((center, area, poly))
    # Extremal regions separate individual cushions across exposure gradients. Only
    # shapes whose measured chromaticity agrees with an observed chair can enter the fit.
    mser = cv2.MSER_create(3, max(10, round(w * h * 0.00009)), round(w * h * 0.024), 0.30, 0.12)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    channels = [gray, np.clip(128 + 3 * (lab[:, :, 1] - 128), 0, 255).astype(np.uint8)]
    for channel in channels:
        regions, _ = mser.detectRegions(channel)
        for region in regions:
            hull = cv2.convexHull(region)
            area = cv2.contourArea(hull)
            if area < 16 or len(region) / max(1, area) < 0.75:
                continue
            (x, y), (a, b), _ = cv2.minAreaRect(hull)
            if min(a, b) < 3.5 or max(a, b) > min(a, b) * 3.8 or area / (a * b) < 0.65:
                continue
            col = lab[round(y), round(x)]
            if (
                not known[round(y), round(x)]
                or min(np.linalg.norm((col[1:] - seed[1:]) / [8, 10]) for seed in seeds) > 1.5
            ):
                continue
            poly = cv2.approxPolyDP(hull, 0.04 * cv2.arcLength(hull, True), True).reshape(-1, 2)
            if not 4 <= len(poly) <= 7:
                continue
            c = np.array([x, y])
            if any(
                np.linalg.norm(c - prior) < 0.46 * max(5, np.sqrt(area)) for prior, _, _ in found
            ):
                continue
            found.append((c, area, poly))
    # Detections seed only genuinely repeated forms. Compare scale and row alignment.
    confirmed = []
    for a, area, poly in found:
        peers = [
            b
            for b, size, _ in found
            if 0.36 < area / size < 2.8
            and 0.45 * np.sqrt(area) < np.linalg.norm(a - b) < 7 * np.sqrt(area)
        ]
        if len(peers) >= 2:
            confirmed.append((a, poly))
    return confirmed


def _seat_base(poly, z, project, frame, intr, w, h):
    """Construct the seat pan from the lower edge of the measured upholstered back."""
    points = np.array(poly, float)
    center = np.mean(points, axis=0)
    footprint = np.ptp(points, axis=0)
    half = max(2.0, footprint[0] * 0.46)
    bottom = points[np.argmax(points[:, 1]), 1]
    lower = np.array([[center[0] - half, bottom], [center[0] + half, bottom]])
    width = 2 * half * z / (w * intr[0, 0])
    depth = min(max(width * 0.65, 0.015), z * 0.07)
    lip = np.array([[-half * 0.06, footprint[1] * 0.13], [half * 0.06, footprint[1] * 0.13]])
    uv = np.vstack([lower, lower[::-1] + lip[::-1]])
    verts = project(uv, [z * 0.969, z * 0.969, z * 0.969 - depth, z * 0.969 - depth])
    drop = project(
        uv + [0, max(1.0, footprint[1] * 0.13)],
        [z * 0.969, z * 0.969, z * 0.969 - depth, z * 0.969 - depth],
    )
    return {
        "kind": "Seat cushion",
        "source_frame": frame,
        "tone": 3,
        "vertices": np.vstack([verts, drop]).round(5).tolist(),
        "faces": [
            [3, 2, 1, 0],
            [4, 5, 6, 7],
            [0, 1, 5, 4],
            [1, 2, 6, 5],
            [2, 3, 7, 6],
            [3, 0, 4, 7],
        ],
    }


def _rails(image, depth, known, project, intr, frame, simple):
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if simple:
        gray = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(4, 4)).apply(
            np.uint8(np.sqrt(gray / 255.0) * 255)
        )
    detector = cv2.createLineSegmentDetector()
    detected = detector.detect(gray)[0]
    if detected is None:
        return []
    lines = []
    for x0, y0, x1, y1 in detected[:, 0, :]:
        d = np.array([x1 - x0, y1 - y0], float)
        length = np.linalg.norm(d)
        if length < 0.12 * min(w, h):
            continue
        if d[0] < 0 or (abs(d[0]) < 1e-4 and d[1] < 0):
            d = -d
            x0, y0 = x1, y1
        unit = d / length
        normal = np.array([-unit[1], unit[0]])
        lines.append((np.array([x0, y0]), unit, normal, length))
    lines.sort(key=lambda line: -line[3])
    claimed = set()
    candidates = []
    for index, (p, u, n, length) in enumerate(lines):
        if index in claimed:
            continue
        options = []
        for j, (q, v, _, size) in enumerate(lines):
            if j == index or j in claimed or abs(u @ v) < 0.996:
                continue
            dist = (q - p) @ n
            # Opposite detected edges bound a finite bar, board or architectural moulding.
            if not 1.3 < abs(dist) < max(3, min(w, h) * (0.35 if simple else 0.048)):
                continue
            a, b = sorted([(q - p) @ u, (q + v * size - p) @ u])
            overlap = min(length, b) - max(0.0, a)
            if overlap < 0.69 * length:
                continue
            options.append((abs(dist), j, dist, max(0.0, a), min(length, b)))
        if not options:
            continue
        _, j, dist, low, high = min(options)
        a = p + low * u
        b = p + high * u
        rect = np.array([a, b, b + n * dist, a + n * dist])
        samples = a + (b - a) * np.linspace(0.05, 0.95, 17)[:, None] + n * dist * 0.5
        pix = np.clip(np.rint(samples).astype(int), [0, 0], [w - 1, h - 1])
        good = known[pix[:, 1], pix[:, 0]]
        if good.sum() < 8:
            continue
        z = depth[pix[:, 1], pix[:, 0]]
        mask = good & np.isfinite(z) & (z > 0)
        if mask.sum() < 8:
            continue
        slope, offset = np.polyfit(np.linspace(0.05, 0.95, 17)[mask], z[mask], 1)
        front = np.maximum(
            np.median(z[mask]) * 0.75, [offset, offset + slope, offset + slope, offset]
        ) * (0.80 if simple else 0.982)

        if simple and np.ptp(rect[:, 0]) < 3 and np.ptp(rect[:, 1]) < 3:
            continue
        tone = 1 if gray[pix[:, 1], pix[:, 0]].mean() < 80 else 4
        obj = _solid(
            rect,
            front,
            project,
            max(0.006, np.median(front) * 0.012),
            frame,
            "Rail or frame",
            tone,
        )
        candidates.append(obj)
        claimed.update((index, j))
        if len(candidates) >= 90:
            break
    return candidates


def _axial_frames(frames, key, depth, project, arch):
    """Fit long horizontal and vertical strips from sustained signed edge contrast."""
    indices = sorted(set(max(min(frames), min(max(frames), key + i)) for i in (-4, -2, 0, 2, 4)))
    source = np.median(np.stack([frames[i] for i in indices]), axis=0).astype(np.uint8)
    h, w = depth.shape
    enhanced = cv2.createCLAHE(2.8, (4, 4)).apply(
        np.uint8(np.sqrt(cv2.cvtColor(source, cv2.COLOR_BGR2GRAY) / 255) * 255)
    )
    fits = []
    for vertical in (True, False):
        img = enhanced if vertical else enhanced.T
        rows, cols = img.shape
        dx = cv2.Sobel(cv2.GaussianBlur(img, (5, 5), 1.3), cv2.CV_32F, 1, 0)
        profiles = []
        for sign in (1, -1):
            value = np.maximum(dx * sign, 0)
            occupancy = (value > 9).mean(axis=0)
            strength = np.mean(np.minimum(value, 100), axis=0)
            scores = np.where(occupancy > 0.45, strength, 0)
            for x in range(2, cols - 2):
                if (
                    scores[x] >= 7
                    and scores[x] >= max(scores[max(0, x - 3) : min(cols, x + 4)])
                    and (not profiles or profiles[-1][0] != x)
                ):
                    profiles.append((x, sign, scores[x], value[:, x] > 6))
        used = set()
        profiles.sort(key=lambda e: -e[2])
        for index, (x, sign, energy, support) in enumerate(profiles):
            if index in used:
                continue
            options = []
            for j, (other, op, score, other_support) in enumerate(profiles):
                dist = other - x
                if j in used or op == sign or not 2 <= dist <= cols * 0.12:
                    continue
                present = support & other_support
                if present.mean() < 0.27:
                    continue
                options.append(
                    (
                        abs(dist) / (np.sqrt(energy * score) * present.mean()),
                        j,
                        other,
                        present,
                    )
                )
            if not options:
                continue
            _, j, end, present = min(options, key=lambda e: e[0])
            y = np.flatnonzero(present)
            low, hi = np.percentile(y, [5, 95])
            if hi - low < rows * 0.32:
                continue
            if low < 0.15 * rows:
                low = 0
            if hi > 0.85 * rows:
                hi = rows - 1
            rect = (
                np.array([[x, low], [end, low], [end, hi], [x, hi]])
                if vertical
                else np.array([[low, x], [hi, x], [hi, end], [low, end]])
            )
            cx, cy = np.rint(rect.mean(0)).astype(int)
            z = float(np.nanmedian(depth[max(0, cy - 3) : cy + 4, max(0, cx - 3) : cx + 4]))
            if not np.isfinite(z) or z <= 0:
                continue
            body = _solid(
                rect,
                np.full(4, z * 0.75),
                project,
                z * 0.04,
                key,
                "Structural frame",
                2,
            )
            xx, yy = rect.astype(int).T
            patch = source[min(yy) : max(yy) + 1, min(xx) : max(xx) + 1]
            body["rgb"] = arch.clay(np.median(patch.reshape(-1, 3), axis=0))
            body["edge_occupancy"] = float(present.mean())
            body["axis"] = "vertical" if vertical else "horizontal"
            body["measured_rect"] = rect.tolist()
            body["axial_depth"] = float(z * 0.75)
            fits.append(body)
            used.update((index, j))
    return fits


def occlusion_evidence(items, frames, cams, intr, head_boxes, original):
    h, w = next(iter(frames.values())).shape[:2]
    candidates = [o for o in items if o.get("foreground_candidate")]
    witnessed = set()
    for i in sorted(frames)[::4]:
        boxes = head_boxes.get(i, [])
        if not boxes:
            continue
        camera = cams[i]
        image = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY).astype(float)
        for item in candidates:
            xyz = np.array(item["vertices"][:4])
            loc = (xyz - camera[:3, 3]) @ camera[:3, :3]
            if np.min(loc[:, 2]) <= 0:
                continue
            uv = (
                loc[:, :2] / loc[:, 2:3] * [intr[0, 0], intr[1, 1]] + [intr[0, 2], intr[1, 2]]
            ) * [w, h]
            if np.linalg.norm(uv[1] - uv[0]) > np.linalg.norm(uv[2] - uv[1]):
                uv = np.roll(uv, 1, axis=0)
            start, end = uv[:2].mean(axis=0), uv[2:].mean(axis=0)
            cross = uv[1] - uv[0]
            pts = start + (end - start) * np.linspace(0.02, 0.98, 45)[:, None]
            for box in boxes:
                x0, y0, x1, y1 = np.array(box) * np.tile([w / original[0], h / original[1]], 2)
                keep = (pts[:, 0] > x0) & (pts[:, 0] < x1) & (pts[:, 1] > y0) & (pts[:, 1] < y1)
                if keep.sum() < 4:
                    continue
                p = pts[keep]
                samples = []
                for sign in (0, -0.8, 0.8):
                    xx, yy = np.clip(
                        np.rint(p + cross * sign).astype(int), [0, 0], [w - 1, h - 1]
                    ).T
                    samples.append(image[yy, xx])
                delta = samples[0] - (samples[1] + samples[2]) * 0.5
                strength = max(np.mean(delta > 3), np.mean(delta < -3))
                if strength >= 0.7:
                    witnessed.add(i)
    if len(witnessed) >= 2:
        for item in candidates:
            item["occlusion_frames"] = sorted(witnessed)
    return sorted(witnessed)


def _observed_row_views(points, step, frame, frames, cams, intr, depth, project, boxes, original):
    """Verify the repeated profile with independent image tracks at its measured depth."""
    h, w = depth.shape
    ref = cv2.cvtColor(frames[frame], cv2.COLOR_BGR2GRAY)
    px, py = np.clip(np.rint(points).astype(int), [0, 0], [w - 1, h - 1]).T
    xyz = project(points, depth[py, px])
    if not np.isfinite(xyz).all():
        return []
    source = points.astype(np.float32).reshape(-1, 1, 2)
    seen = []
    for f in (frame - 12, frame - 6, frame + 6, frame + 12):
        if f not in cams:
            continue
        target = cv2.cvtColor(frames[f], cv2.COLOR_BGR2GRAY)
        flowed, status, _ = cv2.calcOpticalFlowPyrLK(
            ref, target, source, None, winSize=(17, 17), maxLevel=2
        )
        returned, back_status, _ = cv2.calcOpticalFlowPyrLK(
            target, ref, flowed, None, winSize=(17, 17), maxLevel=2
        )
        q = (xyz - cams[f][:3, 3]) @ cams[f][:3, :3]
        uv = (
            q[:, :2] / np.maximum(q[:, 2:], 0.001) * [intr[0, 0], intr[1, 1]]
            + [intr[0, 2], intr[1, 2]]
        ) * [w, h]
        loc = flowed[:, 0]
        good = (status[:, 0] > 0) & (back_status[:, 0] > 0) & (q[:, 2] > 0.01)
        good &= np.linalg.norm(loc - uv, axis=1) < max(2.8, step * 0.55)
        good &= np.linalg.norm(returned[:, 0] - points, axis=1) < 1.4
        good &= np.all((loc > 1) & (loc < [w - 2, h - 2]), axis=1)
        for box in boxes.get(f, []):
            x, y, xx, yy = np.asarray(box) * np.tile([w / original[0], h / original[1]], 2)
            good &= ~((loc[:, 0] >= x) & (loc[:, 1] >= y) & (loc[:, 0] <= xx) & (loc[:, 1] <= yy))
        if good.sum() >= max(3, len(points) * 0.60):
            seen.append(f)
    return seen


def measure_views(frames, views, boxes, original, cams, scales, intr, det, anchor, clean_fn):
    h, w = frames[anchor].shape[:2]
    seats = _seating_samples(frames[anchor], det["detections"], original)
    objects = []
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "orderflow_scene_architecture",
        Path(__file__).with_name("fullclip_scene_architecture.py"),
    )
    arch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arch)
    spec = importlib.util.spec_from_file_location(
        "orderflow_scene_seating", Path(__file__).with_name("fullclip_scene_seating.py")
    )
    seating = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seating)

    occupied_seats = []
    evidence = []
    for frame in [anchor, *sorted(set(views) - {anchor})]:
        image, unknown = clean_fn(frames, frame, boxes, original)
        depth = cv2.resize(views[frame], (w, h)) * scales[frame]
        valid = np.isfinite(depth) & (depth > 0) & ~unknown
        project = _projector(cams[frame], intr, w, h)
        simple = (
            np.percentile(cv2.cvtColor(frames[anchor], cv2.COLOR_BGR2GRAY), 75) < 49 and not seats
        )
        candidates = _seat_candidates(image, valid, seats)
        seat_objects, row_evidence = seating.fitted_rows(
            candidates, image, depth, valid, project, cams[frame], intr, frame
        )
        if len(row_evidence) >= 2:
            pitch = np.median([row["world_width"] for row in row_evidence])
            centers = [
                np.mean(o["vertices"], axis=0) for o in seat_objects if o["kind"] == "Seat back"
            ]
            last_row = max(o["seat_row"] for o in seat_objects)
            source_known = valid.copy()
            for box in boxes.get(frame, []):
                left, top, right, bottom = np.rint(
                    np.asarray(box) * np.tile([w / original[0], h / original[1]], 2)
                ).astype(int)
                source_known[max(0, top) : bottom + 1, max(0, left) : right + 1] = False
            for points, shape, step in seating.repeated_rows(
                frames[frame], source_known, candidates
            ):
                xyz = (np.array(centers) - cams[frame][:3, 3]) @ cams[frame][:3, :3]
                uv = (
                    xyz[:, :2] / xyz[:, 2:] * [intr[0, 0], intr[1, 1]] + [intr[0, 2], intr[1, 2]]
                ) * [w, h]
                covered = np.linalg.norm(points[:, None] - uv[None, :], axis=2).min(1) < max(
                    3, step * 0.62
                )
                if covered.mean() > 0.50:
                    continue
                witnesses = _observed_row_views(
                    points,
                    step,
                    frame,
                    frames,
                    cams,
                    intr,
                    depth,
                    project,
                    boxes,
                    original,
                )
                if not witnesses:
                    continue
                contours = [(point, shape + point) for point in points]
                pieces, fit = seating.fitted_rows(
                    contours,
                    image,
                    depth,
                    valid,
                    project,
                    cams[frame],
                    intr,
                    frame,
                    measured_pitch=pitch,
                )
                if not pieces:
                    continue
                last_row += 1
                for piece in pieces:
                    piece["seat_row"] = last_row
                    piece["observation_status"] = (
                        "repeated source profile on aligned structural edge"
                    )
                    piece["source_observations"] = sorted([frame, *witnesses])
                    if piece["kind"] == "Seat back":
                        centers.append(np.mean(piece["vertices"], axis=0))
                seat_objects.extend(pieces)
                fit[0]["observed_periodic_profile"] = True
                row_evidence.extend(fit)
        structured = len(row_evidence) >= 1
        items = (
            list(seating.hall(image, depth, valid, project, frame))
            if structured
            else arch.architecture(image, depth, valid, project, frame, simple)
        )
        objects.extend(items)
        beginning = len(objects) - len(items)
        added = sum(r["seats"] for r in row_evidence)
        objects.extend(seat_objects)
        for center, poly in candidates if not structured else []:
            x, y = np.clip(np.rint(center).astype(int), [0, 0], [w - 1, h - 1])
            z = depth[y, x]
            if not valid[y, x]:
                continue
            xyz = project(center.reshape(1, 2), [z])[0]
            extent = np.linalg.norm(np.ptp(poly, axis=0)) * z / (w * intr[0, 0])
            if any(np.linalg.norm(xyz - c) < max(0.04, extent * 0.25) for c in occupied_seats):
                continue
            occupied_seats.append(xyz)
            # Shallow padding and an extruded rim stand proud of the supporting measured surface.
            front = np.full(len(poly), z * 0.975)
            obj = _solid(poly, front, project, max(0.02, extent * 0.16), frame, "Seat back", 4)
            obj["rgb"] = arch.clay(image[y, x])
            objects.append(obj)
            base = _seat_base(poly, z, project, frame, intr, w, h)
            base["rgb"] = [v * 0.8 for v in obj["rgb"]]
            objects.append(base)
            added += 1
        if structured:
            rail = seating.join_fascia_corners(
                list(seating.fascia(image, depth, valid, project, frame)),
                cams[frame],
                intr,
                w,
                h,
            )
        else:
            rail = _rails(image, depth, valid, project, intr, frame, simple)
        for obj in rail:
            if "rgb" not in obj:
                obj["rgb"] = arch.clay(
                    np.percentile(image[valid], 18 if obj["tone"] == 1 else 74, axis=0)
                )
        objects.extend(rail)
        if simple:
            axial = _axial_frames(frames, frame, depth, project, arch)
            # Repeated long, thin bars measured across most of the picture form a grating.
            # Preserve the continuous spans and omit short fragment duplicates.
            coherent = [r for r in axial if r["edge_occupancy"] > 0.51]
            if len(coherent) >= 4:
                del objects[beginning + len(items) :]
                family_widths = {}
                for direction in ("horizontal", "vertical"):
                    spans = [
                        np.ptp(np.array(r["measured_rect"]), axis=0)
                        for r in coherent
                        if r["axis"] == direction
                    ]
                    if spans:
                        family_widths[direction] = np.median([min(span) for span in spans])
                for i, r in enumerate(coherent):
                    rect = np.array(r["measured_rect"])
                    axis = r["axis"]
                    extent = np.ptp(rect, axis=0)
                    thickness = min(extent)
                    typical = family_widths[axis]
                    if len(coherent) >= 6 and max(extent) / min(h, w) > 0.50:
                        # Observed parallel bars terminate on a shared frame outside the image.
                        # Open ends in the visible aperture are only loss of edge contrast.
                        thickness = np.clip(thickness, typical * 0.72, typical * 1.4)
                        x, y = rect.mean(axis=0)
                        if axis == "vertical":
                            total = np.array(
                                [
                                    [x - thickness / 2, -h * 0.05],
                                    [x + thickness / 2, -h * 0.05],
                                    [x + thickness / 2, h * 1.05],
                                    [x - thickness / 2, h * 1.05],
                                ]
                            )
                        else:
                            total = np.array(
                                [
                                    [-w * 0.05, y - thickness / 2],
                                    [w * 1.05, y - thickness / 2],
                                    [w * 1.05, y + thickness / 2],
                                    [-w * 0.05, y + thickness / 2],
                                ]
                            )
                        body = _solid(
                            total,
                            np.full(4, r["axial_depth"]),
                            project,
                            r["axial_depth"] * 0.035,
                            frame,
                            "Structural frame",
                            2,
                        )
                        r["vertices"], r["faces"] = body["vertices"], body["faces"]
                        r["span_inferred_from_repeated_frame"] = True
                    r["foreground_candidate"] = True
                objects.extend(coherent)
            else:
                objects.extend(axial)
        neighboring = sorted(views)
        # A low-texture close-up has few stable structures. Keep one physical set for the entire shot.
        if simple and frame != anchor:
            del objects[beginning:]
        elif not simple:
            k = neighboring.index(frame)
            start = (neighboring[k - 1] + frame) // 2 + 1 if k else min(frames)
            end = (neighboring[k + 1] + frame) // 2 if k < len(neighboring) - 1 else max(frames)
            for obj in objects[beginning:]:
                obj["visible_first"], obj["visible_last"] = start, end
        occupied_seats.clear()

        evidence.append(
            {
                "frame": frame,
                "known_fraction": float(valid.mean()),
                "seats": added,
                "linear_solids": len(rail),
                "row_fits": row_evidence,
            }
        )
    objects, tracks = seating.fuse_banks(objects, cams, intr, w, h)
    objects, global_room = seating.permanent_room(objects, cams, intr, w, h)
    additions, gaps = seating.fill_observed_row_gaps(objects, frames, cams, intr, boxes, original)
    objects.extend(additions)
    recess_evidence = []
    if len(views) >= 2 and not simple:
        spec = importlib.util.spec_from_file_location(
            "orderflow_scene_recess",
            Path(__file__).with_name("fullclip_scene_recess.py"),
        )
        recess = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recess)
        masonry, recess_evidence = recess.recover(
            frames, views, scales, cams, intr, boxes, original, objects
        )
        objects.extend(masonry)
    if global_room:
        global_room["inferred_chairs"] = sum(o["kind"] == "Seat back" for o in additions)
        global_room["physical_parts"] = len(objects)
    return {
        "objects": objects,
        "evidence": evidence,
        "stationary_chair_tracks": tracks,
        "global_room": global_room,
        "internal_gap_tests": gaps,
        "observed_recesses": recess_evidence,
    }
