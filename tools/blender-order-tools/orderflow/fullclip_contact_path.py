"""Fit two fixed-length rigid segments across a contiguous shot in camera-relative angles."""

import numpy as np


# Lateral offset shared by both segments, then independent thigh and shin pitch.
# Keeping this grid fixed makes runtime proportional to the observed clip length.
GRIDS = tuple(np.linspace(-limit, limit, n) for limit, n in ((0.88, 17), (0.72, 13), (1.12, 15)))
SHAPE = tuple(map(len, GRIDS))
ANGLES = np.stack(np.meshgrid(*GRIDS, indexing="ij"), axis=-1).reshape(-1, 3)
ZERO = len(ANGLES) // 2


def rotations(axis, angles):
    cosine = np.cos(angles)[:, None, None]
    sine = np.sin(angles)[:, None, None]
    identity = np.eye(3)
    return (
        cosine * identity + (1 - cosine) * np.outer(axis, axis) + sine * np.cross(axis, identity).T
    )


def axes(view):
    axis = view[1][:, 2].copy()
    axis[2] = 0
    if np.linalg.norm(axis) < 1e-6:
        return None
    axis /= np.linalg.norm(axis)
    return axis, np.cross(axis, [0, 0, 1])


def support_cost(upper, lower, ends, solids):
    """Use separating face planes and the finite footprint of each potential support."""
    count = len(ends)
    foot_penetration = np.zeros(count)
    limb_penetration = np.zeros(count)
    gap_cost = np.full(count, 0.85)
    low = np.minimum(upper.min((0, 1)), lower.min((0, 1)))
    high = np.maximum(upper.max((0, 1)), lower.max((0, 1)))
    for box_low, box_high, normals, offsets in solids:
        if np.any(low > box_high + [0.01, 0.01, 1.5]) or np.any(high < box_low - 0.01):
            continue

        def separation(points):
            return (points @ normals.T - offsets).min(1).max(1)

        foot_penetration = np.maximum(foot_penetration, 0.007 - separation(ends))
        upper_lower = np.minimum(separation(upper), separation(lower))
        limb_penetration = np.maximum(limb_penetration, 0.003 - upper_lower)
        index = np.argmax(normals[:, 2])
        normal = normals[index]
        if normal[2] < 0.65:
            continue
        gap = (ends @ normal - offsets[index]).min(1) / normal[2]
        on_top = ends.mean(1)
        on_top[:, 2] -= (on_top @ normal - offsets[index]) / normal[2]
        inside_footprint = (on_top @ normals.T - offsets).max(1) < 0.003
        gap_cost = np.minimum(gap_cost, np.where(inside_footprint & (gap > -0.01), abs(gap), 2))
    return foot_penetration, limb_penetration, gap_cost


def _probes(vertices):
    return np.concatenate(
        (
            vertices,
            vertices.mean(1, keepdims=True),
            (vertices + np.roll(vertices, 1, axis=1)) * 0.5,
        ),
        axis=1,
    )


def oriented_proxy(points):
    center = points.mean(0)
    _, basis = np.linalg.eigh((points - center).T @ (points - center))
    normals = np.concatenate((basis.T, -basis.T))
    return points.min(0), points.max(0), normals, (points @ normals.T).max(0)


def obstacle_cost(up, down, obstacles):
    # Signed finite-box separation is a conservative search heuristic. The second
    # pass tests the actual meshes and both moving legs together.
    penalty = np.zeros(len(up))
    for part in (up, down):
        low, high = part.min(1), part.max(1)
        sweep_min, sweep_max = low.min(0), high.max(0)
        for box_low, box_high, normals, offsets in obstacles:
            if np.any(sweep_min > box_high + 0.004) or np.any(sweep_max < box_low - 0.004):
                continue
            relevant = ((low <= box_high + 0.004) & (high >= box_low - 0.004)).all(1)
            if not relevant.any():
                continue
            distance = np.zeros(len(up))
            separation = (part[relevant] @ normals.T - offsets).min(1).max(1)
            distance[relevant] = np.maximum(0.004 - separation, 0)
            penalty += 18 * np.maximum(
                0, np.minimum(distance / 0.02, 1) - min(distance[ZERO] / 0.02, 1)
            )
            penalty += 8 * distance
    return penalty


def evaluate(hip, knee, upper, lower, distal, view, depth, solids, observed, obstacles=()):
    """Score endpoints, complete limbs, exposed material and distance above native treads."""
    axis, cross = axes(view)
    lateral = rotations(axis, ANGLES[:, 0])
    thigh = lateral @ rotations(cross, ANGLES[:, 1])
    shin = lateral @ rotations(cross, ANGLES[:, 2])
    bent_knee = hip + np.einsum("nij,j->ni", thigh, knee - hip)
    up = (upper - hip) @ thigh.transpose(0, 2, 1) + hip
    down = (lower - knee) @ shin.transpose(0, 2, 1) + bent_knee[:, None]
    end = (distal - knee) @ shin.transpose(0, 2, 1) + bent_knee[:, None]
    penetration, limb, gap = support_cost(up, down, end, solids)
    samples = np.concatenate((_probes(up), _probes(down)), axis=1)
    origin, basis, focal, principal = view
    local = (samples - origin) @ basis
    uv = local[:, :, :2] / np.maximum(local[:, :, 2:], 0.01) * focal + principal
    image, low = depth
    xy = np.floor(uv).astype(int) - low
    x, y = xy[..., 0], xy[..., 1]
    inside = (x >= 0) & (x < image.shape[1]) & (y >= 0) & (y < image.shape[0])
    distances = image[np.clip(y, 0, image.shape[0] - 1), np.clip(x, 0, image.shape[1] - 1)]
    visible = (local[:, :, 2] < distances - 0.01) | ~inside
    changed_visibility = (visible != visible[ZERO]).mean(1)
    # Normalize to a 1280-wide source image. The depth tile is sampled at 640-wide.
    displacement = np.linalg.norm(uv - uv[ZERO], axis=2) * 2
    exposed_motion = ((visible | visible[ZERO]) * np.minimum(displacement / 4, 4)).mean(1)
    cost = (
        30 * np.minimum(penetration / 0.016, 1)
        + 15 * penetration
        + 5 * limb
        + 8.4 * gap
        + 3 * np.minimum(gap / 0.10, 1)
        + obstacle_cost(up, down, obstacles)
        + 8 * exposed_motion
        + 9 * changed_visibility
        + (abs(ANGLES) * [0.34, 0.34, 0.24]).sum(1)
    )
    if observed:
        cost += np.maximum(displacement.max(1) - 1.0, 0) * 20
    correction = end.mean(1) - end[ZERO].mean(0)
    return cost, correction, bool(penetration[ZERO] > 0.007), observed


def _path(cost):
    """Shortest path with at most one angular lattice step per axis and time knot."""
    order = np.arange(len(ANGLES)).reshape(SHAPE)
    distance = cost[0].reshape(SHAPE).copy()
    back = []
    for row in cost[1:]:
        parent = order.copy()
        for axis, weight in enumerate((0.24, 0.26, 0.26)):
            options = [distance]
            parents = [parent]
            for step in (-1, 1):
                candidate = np.roll(distance, step, axis=axis) + weight
                boundary = [slice(None)] * 3
                boundary[axis] = 0 if step == 1 else -1
                candidate[tuple(boundary)] = np.inf
                options.append(candidate)
                parents.append(np.roll(parent, step, axis=axis))
            winner = np.argmin(options, axis=0)[None, ...]
            distance = np.take_along_axis(np.stack(options), winner, axis=0)[0]
            parent = np.take_along_axis(np.stack(parents), winner, axis=0)[0]
        distance += row.reshape(SHAPE)
        back.append(parent.ravel())
    indices = [distance.argmin()]
    for parent in reversed(back):
        indices.append(parent[indices[-1]])
    return np.array(indices[::-1])


def fit_sequence(rows):
    if len(rows) < 8 or not any(row[2] for row in rows):
        return np.zeros((len(rows), 3))
    costs = np.stack([row[0] for row in rows])
    corrections = np.stack([row[1] for row in rows])
    motion = np.linalg.norm(np.diff(corrections, axis=0), axis=2)
    excess = np.maximum(motion - 0.018, 0) * 350
    costs[:-1] += excess
    costs[1:] += excess
    knots = np.r_[np.arange(0, len(rows) - 1, 3), len(rows) - 1]
    knot_cost = np.stack([costs[max(0, k - 1) : k + 2].mean(0) for k in knots])
    indices = _path(knot_cost)
    frames = np.arange(len(rows))
    track = np.stack([np.interp(frames, knots, column) for column in ANGLES[indices].T], axis=1)
    smoothed = np.stack(
        [
            np.convolve(
                np.pad(v, (3, 3), mode="edge"),
                np.array([1, 2, 3, 4, 3, 2, 1]) / 16,
                "valid",
            )
            for v in track.T
        ],
        axis=1,
    )

    observed = np.flatnonzero([row[3] for row in rows])
    if len(observed):
        # Preserve the original pose on observed frames and blend into it from both sides.
        distance = np.min(abs(frames[:, None] - observed), axis=1)
        smoothed *= np.clip(distance / 5, 0, 1)[:, None]
    return smoothed
