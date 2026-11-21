"""Track long, low-texture solid frame members through independently moving foreground people."""

import cv2
import numpy as np


def _segments(image):
    h, w = image.shape[:2]
    core = image[int(h * 0.28) : int(h * 0.71)].astype(float)
    gray = cv2.cvtColor(core.astype(np.uint8), cv2.COLOR_BGR2GRAY)
    med = np.median(gray, axis=0)
    q = np.percentile(gray, [15, 85], axis=0)
    spread = q[1] - q[0]
    stable = (spread < max(3.0, np.percentile(gray, 80) * 0.065)) & (
        np.abs(np.gradient(med)) < 1.3
    )
    stable = (
        cv2.morphologyEx(
            stable.astype(np.uint8)[None, :], cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8)
        ).ravel()
        > 0
    )
    edges = np.diff(np.r_[False, stable, False].astype(int))
    seg = []
    for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        if not w * 0.06 < b - a < w * 0.34:
            continue
        value = np.median(med[a:b])
        ids = np.where(np.abs(med[a:b] - value) < 3)[0]
        if len(ids) < (b - a) * 0.75:
            continue
        a, b = a + ids.min(), a + ids.max() + 1
        if a < 3 or b > w - 3:
            continue
        l, r = np.median(med[max(0, a - 10) : a]), np.median(med[b : b + 10])
        if max(abs(l - value), abs(r - value)) < 3:
            continue
        seg.append(((a + b) * 0.5, b - a, value))
    return seg


def _track(frames):
    ids = list(range(min(frames), max(frames) + 1, 3))
    w = next(iter(frames.values())).shape[1]
    tracks = []
    for f in ids:
        points = _segments(frames[f])
        used = set()
        options = []
        for j, t in enumerate(tracks):
            prev = t[-1]
            gap = f - prev[0]
            if gap > 12:
                continue
            vel = 0 if len(t) < 2 else (prev[1] - t[-2][1]) / (prev[0] - t[-2][0])
            prediction = prev[1] + np.clip(vel, -w * 0.023, w * 0.023) * gap
            for k, (x, width, value) in enumerate(points):
                if (
                    abs(x - prediction) > max(w * 0.045, prev[2] * 0.6) * gap / 3
                    or abs(np.log(width / prev[2])) > 0.47
                    or abs(value - prev[3]) > 5
                ):
                    continue
                cost = (
                    abs(x - prediction) / prev[2]
                    + abs(np.log(width / prev[2]))
                    + abs(value - prev[3]) * 0.06
                )
                options.append((cost, j, k))
        matched = set()
        for cost, j, k in sorted(options):
            if j in matched or k in used:
                continue
            matched.add(j)
            used.add(k)
            tracks[j].append((f, *points[k]))
        tracks.extend([[(f, *p)] for k, p in enumerate(points) if k not in used])
    good = []
    for t in tracks:
        arr = np.array(t)
        if len(arr) < max(7, len(ids) * 0.30) or np.ptp(arr[:, 1]) < w * 0.29:
            continue
        if np.median(np.abs(arr[:, 2] - np.median(arr[:, 2]))) > np.median(arr[:, 2]) * 0.17:
            continue
        good.append(arr)
    if not good:
        return None
    track = max(good, key=len).tolist()
    # Recover the beginning of a low-contrast track with its measured colour and width.
    for f in range(int(track[0][0]) - 3, min(frames) - 1, -3):
        _, prior, width, tone = track[0]
        gray = cv2.cvtColor(frames[f], cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        crop = gray[int(h * 0.28) : int(h * 0.71)]
        lo, hi = np.percentile(crop, [15, 85], axis=0)
        profile = np.median(crop, axis=0)
        ok = ((hi - lo) < 4) & (np.abs(profile - tone) < 2.1)
        cuts = np.diff(np.r_[False, ok, False].astype(int))
        fits = []
        for a, b in zip(np.flatnonzero(cuts == 1), np.flatnonzero(cuts == -1)):
            x = (a + b) * 0.5
            if abs(x - prior) < width * 0.35 and abs((b - a) - width) < width * 0.35:
                fits.append((abs(x - prior) + abs(b - a - width), x, b - a))
        if not fits:
            break
        _, x, width = min(fits)
        track.insert(0, [f, x, width, tone])
    return np.array(track)


def moving_frame(frames, cams, intr, views, scales, head_boxes, orig):
    from math import isfinite

    track = _track(frames)
    if track is None:
        return [], {}
    h, w = next(iter(frames.values())).shape[:2]
    f0, center, width, luma = track[0]
    f0 = int(f0)
    nearest = min(views, key=lambda i: abs(i - f0))
    dep = views[nearest] * scales[nearest]
    dh, dw = dep.shape
    d0 = (
        float(
            np.nanmedian(
                dep[
                    int(dh * 0.35) : int(dh * 0.65),
                    max(0, int(dw * center / w) - 2) : int(dw * center / w) + 3,
                ]
            )
        )
        * 0.78
    )
    if not isfinite(d0) or d0 <= 0:
        return [], {}
    nominal = float(np.median(track[:, 2]))
    half_width = nominal * d0 / (2 * w * intr[0, 0])
    half_height = d0 / intr[1, 1] * 0.73
    half_depth = max(half_width * 0.32, d0 * 0.01)
    faces = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]]

    def solid(dx, dy, dz):
        return [[x * dx, y * dy, z * dz] for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)]

    witnesses = []
    poses = {}
    first, last = int(track[0, 0]), int(track[-1, 0])
    if first - min(frames) <= 6:
        first = min(frames)
    # Extend the measured velocity until the physical column has actually left frame.
    speed = (track[-1, 1] - track[-min(4, len(track)), 1]) / max(
        1, track[-1, 0] - track[-min(4, len(track)), 0]
    )
    if (speed < 0 and track[-1, 1] < nominal * 1.5) or (
        speed > 0 and track[-1, 1] > w - nominal * 1.5
    ):
        last = max(frames)
    for f in range(first, last + 1):
        x = float(np.interp(f, track[:, 0], track[:, 1]))
        if f > track[-1, 0]:
            x += speed * (f - track[-1, 0])
        # The frame is rigid; use a three-sample median width as its scale observation.
        near = np.argsort(np.abs(track[:, 0] - f))[:3]
        local_width = np.clip(np.median(track[near, 2]), nominal * 0.86, nominal * 1.14)
        depth = d0 * nominal / local_width
        camera = cams[f]
        point = (
            np.array([(x / w - intr[0, 2]) / intr[0, 0], (0.5 - intr[1, 2]) / intr[1, 1], 1])
            * depth
        )
        poses[str(f)] = {
            "rotation": camera[:3, :3].round(8).tolist(),
            "center": (camera[:3, :3] @ point + camera[:3, 3]).round(8).tolist(),
        }
        for box in head_boxes.get(f, []):
            x0, y0, x1, y1 = np.array(box) * np.tile([w / orig[0], h / orig[1]], 2)
            ax, bx = max(x - width * 0.35, x0), min(x + width * 0.35, x1)
            ay, by = max(h * 0.28, y0), min(h * 0.71, y1)
            if bx - ax < 3 or by - ay < 4:
                continue
            patch = frames[f][
                max(0, int(ay)) : min(h, int(by) + 1), max(0, int(ax)) : min(w, int(bx) + 1)
            ]
            if not patch.size:
                continue
            intensity = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
            if (
                np.percentile(intensity, 85) - np.percentile(intensity, 15) < 7
                and abs(np.median(intensity) - luma) < 8
            ):
                witnesses.append(f)
                break
    post = {
        "kind": "Tracked structural post",
        "source_frame": f0,
        "vertices": solid(half_width, half_height, half_depth),
        "faces": faces,
        "rgb": [0.36, 0.37, 0.38],
        "tone": 2,
        "motion": poses,
        "visible_first": first,
        "visible_last": last,
    }
    if len(witnesses) >= 2:
        post["occlusion_frames"] = witnesses
    # A frame edge must continue across a substantial part of the source outside the post.
    # Fit the dominant sustained signed transitions near the upper and lower image edge.
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path

    spec = spec_from_file_location(
        "orderflow_scene_aperture", Path(__file__).with_name("fullclip_scene_aperture.py")
    )
    aperture = module_from_spec(spec)
    spec.loader.exec_module(aperture)
    beam_data = aperture.boundaries(frames, track)
    beams = []
    for edge in beam_data:
        upper = edge["side"] == "upper"
        measured = np.median(edge["edge"])
        size_y = max(measured if upper else h - measured, h * 0.035) + h * 0.12
        motion = {}
        for f in range(min(frames), max(frames) + 1):
            y = float(np.interp(f, edge["frames"], edge["edge"]))
            center_y = y - size_y / 2 if upper else y + size_y / 2
            camera = cams[f]
            center_local = (
                np.array(
                    [(0.5 - intr[0, 2]) / intr[0, 0], (center_y / h - intr[1, 2]) / intr[1, 1], 1]
                )
                * d0
            )
            motion[str(f)] = {
                "rotation": camera[:3, :3].round(8).tolist(),
                "center": (camera[:3, :3] @ center_local + camera[:3, 3]).round(8).tolist(),
            }
        beams.append(
            {
                "kind": "Structural crossbeam",
                "source_frame": min(frames),
                "vertices": solid(
                    d0 / intr[0, 0] * 0.66, d0 * size_y / (2 * h * intr[1, 1]), half_depth
                ),
                "faces": faces,
                "tone": 2,
                "rgb": [0.38, 0.39, 0.40],
                "motion": motion,
                "occlusion_frames": witnesses,
                "aperture_side": edge["side"],
            }
        )
    rear, rear_proof = aperture.rear_support(frames, cams, intr, d0, solid, faces)
    return [post, *beams, *rear], {
        "track_samples": len(track),
        "first": first,
        "last": last,
        "head_overlap_frames": witnesses,
        "crossbeams": len(beams),
        "aperture_edges": [
            {
                "side": e["side"],
                "median_edge_row": float(np.median(e["edge"])),
                "median_contrast": e["median_response"],
            }
            for e in beam_data
        ],
        "rear_support": rear_proof,
    }


def fixtures(frames, depth, camera, intr, anchor, known):
    """Fit stable, rounded bright housings outside the observed actor masks."""
    frames[anchor]
    h, w = known.shape
    times = list(range(max(min(frames), anchor - 6), min(max(frames), anchor + 6) + 1, 2))
    samples = np.stack([cv2.cvtColor(frames[f], cv2.COLOR_BGR2GRAY) for f in times])
    gray = np.median(samples, axis=0).astype(np.uint8)
    cutoff = max(float(np.percentile(gray[known], 94)) if known.any() else 255, 35)
    bright = (gray > cutoff) & known
    contours, _ = cv2.findContours(
        bright.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    objects = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if len(contour) < 6 or not w * h * 0.00019 < area < w * h * 0.017:
            continue
        (cx, cy), (aa, bb), angle = cv2.fitEllipse(contour)
        if (
            min(aa, bb) < 3
            or max(aa, bb) / min(aa, bb) > 4.6
            or area / (np.pi * aa * bb / 4) < 0.64
        ):
            continue
        x, y = np.clip(np.rint([cx, cy]).astype(int), [0, 0], [w - 1, h - 1])
        valid = known[
            max(0, int(cy - bb * 0.6)) : min(h, int(cy + bb * 0.6) + 1),
            max(0, int(cx - aa * 0.6)) : min(w, int(cx + aa * 0.6) + 1),
        ]
        if valid.size == 0 or valid.mean() < 0.85 or (samples[:, y, x] > cutoff).mean() < 0.72:
            continue
        z = depth[y, x]
        if not np.isfinite(z) or z <= 0:
            continue
        center = (
            np.array(
                [(cx / w - intr[0, 2]) * z / intr[0, 0], (cy / h - intr[1, 2]) * z / intr[1, 1], z]
            )
            * 1.12
        )
        t = np.deg2rad(angle)
        axis_a = np.array([np.cos(t), np.sin(t), 0]) * aa * z / (2 * w * intr[0, 0])
        axis_b = np.array([-np.sin(t), np.cos(t), 0]) * bb * z / (2 * h * intr[1, 1])
        lamp_depth = min(np.linalg.norm(axis_a), np.linalg.norm(axis_b)) * 0.85

        def world(local):
            return (camera[:3, :3] @ local + camera[:3, 3]).round(6).tolist()

        verts = [world(center - np.array([0.0, 0.0, lamp_depth]))]
        faces = []
        for layer in range(1, 6):
            phi = -np.pi * 0.5 + layer * np.pi / 6
            start = len(verts)
            for k in range(12):
                theta = k * np.pi / 6
                local = (
                    center
                    + np.cos(phi) * (axis_a * np.cos(theta) + axis_b * np.sin(theta))
                    + np.array([0.0, 0.0, np.sin(phi) * lamp_depth])
                )
                verts.append(world(local))
                if layer == 1:
                    faces.append([0, 1 + (k + 1) % 12, 1 + k])
                else:
                    faces.append(
                        [
                            start - 12 + k,
                            start - 12 + (k + 1) % 12,
                            start + (k + 1) % 12,
                            start + k,
                        ]
                    )
        top = len(verts)
        verts.append(world(center + np.array([0.0, 0.0, lamp_depth])))
        faces.extend([[top - 12 + k, top - 12 + (k + 1) % 12, top] for k in range(12)])
        objects.append(
            {
                "source_frame": anchor,
                "kind": "Rounded fixture",
                "vertices": verts,
                "faces": faces,
                "rgb": [0.77, 0.77, 0.77],
                "tone": 5,
            }
        )
    return objects
