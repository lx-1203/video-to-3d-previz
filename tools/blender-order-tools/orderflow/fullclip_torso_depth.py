"""Infer the visible back surface and torso inclination from the measured camera depth."""

import numpy as np
from .fullclip_scene import _depth_samples


def unit(direction):
    return direction / max(np.linalg.norm(direction), 1e-7)


def torso_track(shot, sequence, width, height):
    """Measure each depth map against the actor observed at exactly that timestamp."""
    if not any(
        a.get("orientation", {}).get("facing_state") == "back_or_down_inferred"
        for _, a in sequence
    ):
        return {}
    depths, indices = _depth_samples(shot["depth_samples"], shot["depth_sha256"])
    lookup = {i: a for i, a in sequence}
    measured = {}
    for slot, idx in enumerate(indices):
        actor = lookup.get(int(idx))
        if (
            actor is None
            or actor.get("orientation", {}).get("facing_state") != "back_or_down_inferred"
        ):
            continue
        a, b = [
            np.array([actor["anchors"][part]["u"] / width, actor["anchors"][part]["v"] / height])
            for part in ("shoulder", "head")
        ]
        if not np.isfinite([*a, *b]).all() or np.linalg.norm(b - a) < 0.03:
            continue
        axes = measured_axes(depths[slot], np.asarray(shot["intrinsics"]), a, b, width / height)
        if axes is not None:
            measured[int(idx)] = axes
    if not measured:
        return {}
    keys = sorted(measured)
    result = {}
    for frame, actor in sequence:
        if actor.get("orientation", {}).get("facing_state") != "back_or_down_inferred":
            continue
        slot = np.searchsorted(keys, frame)
        lo, hi = keys[max(0, slot - 1)], keys[min(slot, len(keys) - 1)]
        t = (frame - lo) / (hi - lo) if hi > lo else 0
        result[frame] = tuple(
            unit(a * (1 - t) + b * t) for a, b in zip(measured[lo], measured[hi])
        )
    return result


def measured_axes(depth, k, shoulder, head, aspect):
    axis = head - shoulder
    ratio = np.array([aspect, 1.0])
    along = unit(axis * ratio)
    transverse = np.array([-along[1], along[0]])
    yy, xx = np.mgrid[: depth.shape[0], : depth.shape[1]]
    uv = np.stack([(xx + 0.5) / depth.shape[1], (yy + 0.5) / depth.shape[0]], axis=-1)
    offsets = (uv - shoulder) * ratio
    length = max(0.09, np.linalg.norm(axis * ratio))
    keep = (
        (abs(offsets @ transverse) < 0.48 * length)
        & (offsets @ along < 0.20 * length)
        & (offsets @ along > -0.94 * length)
    )
    keep &= np.isfinite(depth) & (depth > 0)
    if keep.sum() < 14:
        return None
    uv = uv[keep]
    z = depth[keep]
    # Fit inverse depth, which is linear on a pinhole-projected plane.
    xy = np.column_stack([(uv[:, 0] - k[0, 2]) / k[0, 0], (k[1, 2] - uv[:, 1]) / k[1, 1]])
    design = np.column_stack([xy, np.ones(len(xy))])
    inv = 1 / z
    active = np.abs(z - np.median(z)) < 0.36 * np.median(z)
    for _ in range(4):
        if active.sum() < 14:
            return None
        coeff = np.linalg.lstsq(design[active], inv[active], rcond=None)[0]
        res = np.abs(inv - design @ coeff)
        active = res < max(0.015 * np.median(inv), 2.8 * np.median(res))
    if active.sum() < 0.55 * len(z):
        return None

    def unproject(uv):
        x = (uv[0] - k[0, 2]) / k[0, 0]
        h = (k[1, 2] - uv[1]) / k[1, 1]
        d = 1 / max(0.1 * np.median(inv), coeff @ [x, h, 1])
        return np.array([x * d, d, h * d])

    up = unit(unproject(shoulder + axis * 0.16) - unproject(shoulder - axis * 0.16))
    normal = unit(np.array([-coeff[0], -coeff[2], -coeff[1]]))
    across = unit(np.cross(normal, up))
    if across[2] * along[0] + across[0] * (-along[1]) < 0:
        across = -across
    return up, across
