"""Fit a fixed hand-held mesh using the camera, candidate wrist depths and temporal continuity."""

import math
import numpy as np


def rotations(parameters):
    """Local XYZ Euler angles with X along the solid's long axis."""
    r, p, a = np.asarray(parameters, float).T
    cr, cp, ca = np.cos(r), np.cos(p), np.cos(a)
    sr, sp, sa = np.sin(r), np.sin(p), np.sin(a)
    return np.stack(
        (
            ca * cp,
            ca * sp * sr - sa * cr,
            ca * sp * cr + sa * sr,
            sa * cp,
            sa * sp * sr + ca * cr,
            sa * sp * cr - ca * sr,
            -sp,
            cp * sr,
            cp * cr,
        ),
        axis=-1,
    ).reshape(-1, 3, 3)


def continuous_depths(centers, rays, frame_numbers, unit, fps):
    """Resolve incompatible hand depths over time while staying on the original camera rays.

    Work in world coordinates so a camera dolly does not introduce apparent object motion.
    The nearer sustained wrist observation limits visibility. An isolated single-frame
    depth error cannot pull the entire approach forward.
    """
    centers = np.asarray(centers, dtype=float)
    rays = np.asarray(rays, dtype=float)
    frame_numbers = np.asarray(frame_numbers)
    result = np.zeros(len(centers))
    # Express speed in measured object lengths per second, independent of frame rate.
    max_step = 8 * unit / fps
    start = 0
    for end in range(1, len(centers) + 1):
        if end < len(centers) and frame_numbers[end] == frame_numbers[end - 1] + 1:
            if np.dot(rays[start], rays[end]) > 0.60:
                continue
        direction = rays[start] / np.linalg.norm(rays[start])
        observations = (centers[start:end] - centers[start]) @ direction
        support = np.array(
            [np.median(observations[max(0, k - 2) : k + 3]) for k in range(end - start)]
        )
        for k in range(1, len(support)):
            support[k] = min(support[k], support[k - 1] + max_step)
        for k in range(len(support) - 2, -1, -1):
            support[k] = min(support[k], support[k + 1] + max_step)
        smooth = np.convolve(
            np.pad(support, 2, mode="edge"), np.array([1, 2, 3, 2, 1]) / 9, mode="valid"
        )
        result[start:end] = (smooth - observations) / (rays[start:end] @ direction)
        start = end
    return result


def fit_pose(
    vertices,
    polygon,
    metric_per_pixel,
    camera_offset,
    depth,
    unit,
    previous,
    perspective,
    max_turn=0.26,
):
    center = (polygon.max(0) + polygon.min(0)) * 0.5
    # Source raster y points down, camera y points up.
    xy = (polygon - center) * np.array([1.0, -1.0])
    angles = np.arange(20) * (2 * math.pi / 20)
    normals = np.array([np.cos(angles), np.sin(angles)])
    target = (xy @ normals).max(0)
    width = max(1.0, np.ptp(xy, axis=0).max())
    vec = np.linalg.eigh(np.cov(xy.T))[1][:, -1]
    if vec[0] < 0:
        vec = -vec
    yaw = math.atan2(vec[1], vec[0])
    object_xyz = vertices * unit

    def evaluate(poses):
        matrices = rotations(poses)
        rotated = object_xyz @ matrices.transpose(0, 2, 1)
        projected = rotated[:, :, :2]
        if perspective:
            # Perspective moves off-axis silhouettes sideways as the body turns.
            z = rotated[:, :, 2:3]
            projected = (projected + z * camera_offset / depth) / (1 - z / depth)
        projected = projected / metric_per_pixel
        middle = (projected.max(1) + projected.min(1)) * 0.5
        support = ((projected - middle[:, None, :]) @ normals).max(1)
        errors = np.mean(((support - target) / width) ** 2, axis=1)
        if previous is not None:
            # A thin solid often admits nearly identical silhouettes for opposing
            # face orientations. Measure actual SO(3) distance, independent of Euler wraps.
            traces = np.sum(matrices * previous_matrix, axis=(1, 2))
            turns = np.arccos(np.clip((traces - 1) * 0.5, -1.0, 1.0))
            errors += 0.004 * turns**2
            errors[turns > max_turn + 1e-7] = np.inf
        return errors, middle

    previous_matrix = None if previous is None else rotations([previous])[0]
    if previous is None:
        starts = [
            [roll, pitch, yaw]
            for pitch in (-1.0, -0.6, 0.0, 0.6, 1.0)
            for roll in (-1.4, -0.65, 0.0, 0.65, 1.4)
        ]
    else:
        starts = [previous, [previous[0], previous[1], yaw]]
        starts += [
            [r, p, yaw] for r in (-1.25, -0.5, 0.25, 1.10) for p in (-1.0, -0.5, 0.0, 0.5, 1.0)
        ]
    starts = np.asarray(starts)
    loss, _ = evaluate(starts)
    best = starts[loss.argmin()]
    step_dirs = np.vstack([np.eye(3), -np.eye(3), np.zeros((1, 3))])
    for step in (0.30, 0.15, 0.075, 0.037):
        for _ in range(5):
            options = best + step * step_dirs
            losses, _ = evaluate(options)
            k = losses.argmin()
            best = options[k]
            if k == len(options) - 1:
                break
    _, middle = evaluate(best[None, :])
    return best, -middle[0] * metric_per_pixel
