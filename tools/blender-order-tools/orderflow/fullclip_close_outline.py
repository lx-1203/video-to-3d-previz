"""Measure the left or right outer back/sleeve silhouette in native depth views."""

import numpy as np


def outside_edges(depth, head):
    """Sample the side facing away from the head. Reject foreground surfaces without a depth step."""
    h, w = depth.shape
    sign = 1 if head[0] > 0.5 else -1
    inv = np.divide(
        1.0, depth, out=np.zeros_like(depth, dtype=float), where=np.isfinite(depth) & (depth > 0)
    )
    inv = inv[:, ::sign]
    toward = head[0] if sign == 1 else 1 - head[0]
    samples = []
    for frac in np.linspace(0.17, 0.82, 9):
        r = int(frac * h)
        row = np.median(inv[max(0, r - 1) : min(h, r + 2)], axis=0)
        delta = (
            np.convolve(row, np.ones(3) / 3, mode="same")[4:]
            - np.convolve(row, np.ones(3) / 3, mode="same")[:-4]
        )
        edge = np.arange(len(delta)) + 2
        valid = (edge > max(3, 0.04 * w)) & (edge < min(toward - 0.14, 0.64) * w)
        if not valid.any():
            continue
        index = int(np.argmax(np.where(valid, delta, -np.inf)))
        x = index + 2
        contrast = delta[index] / max(1e-6, row[x])
        if contrast < 0.14 or np.median(row[x + 2 : x + 6]) <= np.median(
            row[max(0, x - 6) : x - 2]
        ):
            continue
        samples.append((frac, (x + 0.5) / w if sign == 1 else 1 - (x + 0.5) / w))
    return sign, samples


def mask_samples(depth, actor, width, height):
    h, w = depth.shape
    gy, gx = np.mgrid[0:h:2, 0:w:2]
    x, y = (gx + 0.5) / w, (gy + 0.5) / h
    valid = np.isfinite(depth) & (depth > 0)
    if np.count_nonzero(valid) < 20:
        return None
    inverse = np.divide(1.0, depth, out=np.zeros_like(depth), where=valid)
    low, high = np.quantile(inverse[valid], [0.13, 0.87])
    if high <= low * 1.2:
        return None
    contrast = (inverse[gy, gx] - low) / (high - low)
    permitted = (y > 0.1) & (y < 0.84) & (x > 0.015) & (x < 0.985) & valid[gy, gx]
    region = actor.get("head_detection") or actor.get("face")
    if region:
        l, t, r, b = np.asarray(region["bbox"]) / [width, height, width, height]
        permitted &= ((x - (l + r) * 0.5) / max(0.05, (r - l) * 0.57)) ** 2 + (
            (y - (t + b) * 0.5) / max(0.05, (b - t) * 0.57)
        ) ** 2 > 1
    near = (contrast > 0.38) & permitted
    far = (contrast < 0.09) & permitted
    if min(near.sum(), far.sum()) < 14:
        return None
    return np.stack((x, y), axis=-1), near, far


def convex_mask(points, grid):
    # Intersections with each raster row give exact convex-hull spans, including
    # partly cropped modeled sleeves. Degenerate horizontal edges are skipped.
    x1, y1, x2, y2 = points.T
    mask = np.zeros(grid.shape[:2], dtype=bool)
    for n, row in enumerate(grid[:, 0, 1]):
        active = (np.minimum(y1, y2) <= row) & (row <= np.maximum(y1, y2)) & (abs(y2 - y1) > 1e-8)
        if active.any():
            xx = x1[active] + (x2[active] - x1[active]) * (row - y1[active]) / (
                y2[active] - y1[active]
            )
            mask[n] = (grid[n, :, 0] >= xx.min()) & (grid[n, :, 0] <= xx.max())
    return mask


def envelope(parts, vertices, pose, lm, actor, k, height, width, targets, side, depth_mask):
    head = actor["anchors"]["head"]
    depth = k[0, 0] * width / max(1.0, actor["pixels_per_meter"])
    center = np.array(
        [
            (head["u"] / width - k[0, 2]) * depth / k[0, 0],
            depth,
            (k[1, 2] - head["v"] / height) * depth / k[1, 1],
        ]
    )
    loc = center - np.asarray(lm["head"])
    lines = []
    for name, verts in vertices.items():
        origin, rot = pose[name]
        arr = (verts - parts[name]["proximal_endpoint"]) @ np.asarray(rot).T + origin + loc
        if (arr[:, 1] < 0.03).any():
            continue
        uv = np.c_[
            k[0, 0] * arr[:, 0] / arr[:, 1] + k[0, 2], k[1, 2] - k[1, 1] * arr[:, 2] / arr[:, 1]
        ]
        i, j = np.triu_indices(len(uv), k=1)
        lines.append(np.c_[uv[i], uv[j]])
    if not lines:
        return 1.0
    x1, y1, x2, y2 = np.concatenate(lines).T
    errors = []
    for row, edge in targets:
        crosses = (np.minimum(y1, y2) <= row) & (row <= np.maximum(y1, y2)) & (abs(y2 - y1) > 1e-8)
        if not crosses.any():
            errors.append(0.44)
            continue
        xs = x1[crosses] + (x2[crosses] - x1[crosses]) * (row - y1[crosses]) / (
            y2[crosses] - y1[crosses]
        )
        limit = np.min(xs) if side == 1 else np.max(xs)
        errors.append(min(0.55, abs(np.clip(limit, 0, 1) - edge)))
    outline = float(np.mean(errors))
    if depth_mask is None:
        return outline
    grid, near, far = depth_mask
    rendered = np.logical_or.reduce([convex_mask(line, grid) for line in lines])
    missing = np.count_nonzero(near & ~rendered) / np.count_nonzero(near)
    excess = np.count_nonzero(far & rendered) / np.count_nonzero(far)
    return float(0.38 * outline + 0.78 * missing + 0.57 * excess)


def permitted_strength(sequence, axes, turn, parts, mesh, shot, h, w):
    from .fullclip_scene import _depth_samples
    from .fullclip_binding import local_pose
    from .fullclip_close_shoulders import rotation
    from mathutils import Vector

    depths, indices = _depth_samples(shot["depth_samples"], shot["depth_sha256"])
    actors = dict(sequence)
    K = np.asarray(shot["intrinsics"])
    names = ["DEF-spine", "DEF-upper_arm.L", "DEF-upper_arm.R", "DEF-forearm.L", "DEF-forearm.R"]
    silhouette = {
        n: np.unique(
            np.array([tuple(mesh.data.vertices[v].co) for v in parts[n]["vertex_indices"]]), axis=0
        )
        for n in names
    }
    selected = {}
    for depth, frame in zip(depths, indices):
        frame = int(frame)
        if frame not in actors or frame not in axes:
            continue
        actor = actors[frame]
        head = actor["anchors"]["head"]
        side, targets = outside_edges(depth, (head["u"] / w, head["v"] / h))
        if len(targets) < 4:
            continue
        mask = mask_samples(depth, actor, w, h)
        scores = []
        for strength in np.linspace(0, 1, 11):
            Q = rotation(turn * strength)
            pose, lm = local_pose(actor, parts, tuple(Q @ Vector(a) for a in axes[frame]))
            loss = envelope(parts, silhouette, pose, lm, actor, K, h, w, targets, side, mask)
            scores.append(loss)
        # Preserve the strongest joint-supported refinement that is also near the
        # best actual silhouette. Restrict large rotations without contour support.
        best = min(scores)
        permitted = max(n for n, c in enumerate(scores) if c <= best + 0.018)
        selected[frame] = (permitted / 10.0, scores)
    return selected
