"""Fuse shot-local background optical flow with depth-registered camera poses."""

import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve


def _mask(shape, boxes, original):
    h, w = shape
    mask = np.full((h, w), 255, np.uint8)
    for box in boxes:
        p = np.rint(np.array(box) * np.tile([w / original[0], h / original[1]], 2)).astype(int)
        x, y, x2, y2 = p
        left, right = np.clip([x - 2, x2 + 3], 0, w)
        top, bottom = np.clip([y - 2, y2 + 3], 0, h)
        mask[top:bottom, left:right] = 0
    return mask


def _features(gray, nxt, mask0, mask1):
    p = cv2.goodFeaturesToTrack(gray, 360, 0.004, 5, mask=mask0)
    if p is None or len(p) < 10:
        return None
    q, ok, photometric_error = cv2.calcOpticalFlowPyrLK(
        gray, nxt, p, None, winSize=(19, 19), maxLevel=3
    )
    r, bok, _ = cv2.calcOpticalFlowPyrLK(nxt, gray, q, None, winSize=(19, 19), maxLevel=3)
    h, w = gray.shape
    x, y = np.rint(q[:, 0]).astype(int).T
    good = (ok[:, 0] > 0) & (bok[:, 0] > 0) & (np.linalg.norm((r - p)[:, 0], axis=1) < 0.8)
    good &= photometric_error[:, 0] < 18.0
    good &= (x >= 1) & (y >= 1) & (x < w - 1) & (y < h - 1)
    good &= mask1[np.clip(y, 0, h - 1), np.clip(x, 0, w - 1)] > 0
    p, q = p[good, 0], q[good, 0]
    adequate = len(p) >= 12 and np.ptp(p[:, 0]) > 0.20 * w and np.ptp(p[:, 1]) > 0.12 * h
    return (p, q) if adequate else None


def _depth_image(depth, old, cam, K, shape):
    h, w = shape
    z = cv2.resize(np.asarray(depth, dtype=np.float32), (w, h), interpolation=cv2.INTER_NEAREST)
    yy, xx = np.mgrid[0:h:2, 0:w:2]
    dz = z[::2, ::2].ravel()
    pts = np.column_stack((xx.ravel(), yy.ravel(), np.ones(dz.size))) @ np.linalg.inv(K).T
    good = np.isfinite(dz) & (dz > 0)
    if good.sum() < 12:
        return z
    pts = pts[good] * dz[good, None]
    rel = np.linalg.inv(cam) @ old
    p = pts @ rel[:3, :3].T + rel[:3, 3]
    good = p[:, 2] > 0.025
    p = p[good]
    proj = p @ K.T
    uv = np.rint(proj[:, :2] / proj[:, 2, None] / 2).astype(int)
    hh, ww = (h + 1) // 2, (w + 1) // 2
    valid = (uv[:, 0] >= 0) & (uv[:, 0] < ww) & (uv[:, 1] >= 0) & (uv[:, 1] < hh)
    small = np.full((hh, ww), np.inf, np.float32)
    np.minimum.at(small, (uv[valid, 1], uv[valid, 0]), p[valid, 2].astype(np.float32))
    zbuf = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    return np.where(np.isfinite(zbuf), zbuf, z)


def _estimate(p, q, z, K):
    """Robust small-motion reprojection with effective-depth translation units."""
    x = (p[:, 0] - K[0, 2]) / K[0, 0]
    y = (p[:, 1] - K[1, 2]) / K[1, 1]
    z0 = float(np.median(z))
    iv = z0 / z
    zeros = np.zeros_like(x)
    A = np.stack(
        [
            np.column_stack((-x * y, 1 + x * x, -y, iv, zeros, -x * iv)) * K[0, 0],
            np.column_stack((-1 - y * y, x * y, x, zeros, iv, -y * iv)) * K[1, 1],
        ],
        axis=1,
    )
    d = q - p
    weights = np.ones(len(p))
    # Suppress translation/rotation tradeoffs when all features lie on one plane.
    # The geometric low-frequency prior below still retains established dolly travel.
    reg = np.diag([4, 4, 4, 8, 8, 8])
    for _ in range(4):
        W = np.sqrt(weights)[:, None, None]
        a = (A * W).reshape(-1, 6)
        sol = np.linalg.lstsq(
            np.vstack((a, reg)),
            np.r_[(d * W[:, :, 0]).ravel(), np.zeros(6)],
            rcond=None,
        )[0]
        residual = np.linalg.norm(np.einsum("ijk,k->ij", A, sol) - d, axis=1)
        weights = np.minimum(1.0, 0.7 / np.maximum(residual, 1.0e-6))
    keep = residual < 1.5
    if keep.sum() < 10 or np.mean(keep) < 0.45:
        return None
    return sol[:3], sol[3:] * z0, float(np.median(residual)), int(keep.sum())


def _fuse(raw, step, weights):
    """Low-frequency absolute registration with source-derived per-frame motion."""
    n = len(raw)
    if n <= 2 or max(weights, default=0.0) == 0:
        return raw
    W = np.ones(n)
    # One directly fixed gauge per shot. A finite soft anchor lets a long run drift.
    prior = diags(W)
    diff = diags([-np.ones(n - 1), np.ones(n - 1)], [0, 1], shape=(n - 1, n))
    obs = diags(weights)
    lhs = (prior + diff.T @ obs @ diff).tocsc()
    rhs = prior @ raw + diff.T @ obs @ step
    solved = raw.copy()
    solved[1:] = spsolve(lhs[1:, 1:], rhs[1:] - lhs[1:, 0].toarray() * raw[0])
    return solved


def _continuous_log(vec, reference):
    """Choose the SO(3) logarithm nearest the preceding chart coordinate."""
    mag = np.linalg.norm(vec)
    if mag < 1.0e-9:
        old = np.linalg.norm(reference)
        return (
            np.zeros(3)
            if old < np.pi
            else reference / old * (2 * np.pi * round(old / (2 * np.pi)))
        )
    axis = vec / mag
    turns = round((np.dot(reference, axis) - mag) / (2 * np.pi))
    return vec + 2 * np.pi * turns * axis


def stabilize(cams, frames, views, boxes, original, intrinsic, diagnostics):
    """Take CV camera-to-world poses and scale-aligned key depths, for one shot."""
    keys = sorted(set(frames) & set(cams))
    result = {i: np.array(c, dtype=float, copy=True) for i, c in cams.items()}
    diag = {int(i): dict(row) for i, row in diagnostics.items()}
    depth_keys = sorted(set(views) & set(cams))
    if len(keys) < 3 or not depth_keys:
        return result, diag
    h, w = frames[keys[0]].shape[:2]
    K = np.asarray(intrinsic, dtype=float).copy()
    K[0] *= w
    K[1] *= h
    raw = np.stack([result[k] for k in keys])
    increments = []
    weights = []
    breaks = [0]
    last_gray = cv2.cvtColor(frames[keys[0]], cv2.COLOR_BGR2GRAY)
    last_mask = _mask(last_gray.shape, boxes.get(keys[0], []), original)
    for n, i in enumerate(keys[1:], 1):
        prev = keys[n - 1]
        gray = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
        mask = _mask(gray.shape, boxes.get(i, []), original)
        tracks = _features(last_gray, gray, last_mask, mask)
        sad = float(np.mean(cv2.absdiff(last_gray, gray)))
        old_step = np.linalg.inv(raw[n]) @ raw[n - 1]
        estimated = None
        if i != prev + 1 or (sad > 22 and (tracks is None or len(tracks[0]) < 30)):
            breaks.append(n)
        elif tracks is not None:
            p, q = tracks
            anchor = min(depth_keys, key=lambda j: abs(j - prev))
            zmap = _depth_image(views[anchor], cams[anchor], cams[prev], K, gray.shape)
            pix = np.rint(p).astype(int)
            z = zmap[pix[:, 1], pix[:, 0]]
            good = np.isfinite(z) & (z > 0)
            if good.sum() >= 12:
                estimated = _estimate(p[good], q[good], z[good], K)
        entry = diag.setdefault(i, {})
        if estimated is not None:
            omega, t, err, count = estimated
            m = np.eye(4)
            m[:3, :3] = Rotation.from_rotvec(omega).as_matrix()
            m[:3, 3] = t
            # Pixel measurements constrain relative normalized SE(3) displacement.
            # Convert their variance using focal length before combining with a
            # weak gauge-space absolute registration prior.
            weight = (min(K[0, 0], K[1, 1]) / (1.0 + err)) ** 2 * min(1.0, count / 65.0)
            entry["stabilization"] = {
                "status": "background_KLT_depth_reprojection",
                "inliers": count,
                "median_residual_px": err,
            }
        else:
            if breaks[-1] != n:
                breaks.append(n)
            m = old_step
            weight = 0.0
            entry["stabilization"] = {"status": "insufficient_static_evidence"}
        pred = raw[n - 1] @ np.linalg.inv(m)
        increments.append((pred, raw[n - 1]))
        weights.append(weight)
        last_gray, last_mask = gray, mask
    for start, end in zip(breaks, breaks[1:] + [len(keys)]):
        if end - start < 3:
            continue
        subset = raw[start:end]
        origin = Rotation.from_matrix(subset[0, :3, :3])
        rot = (origin.inv() * Rotation.from_matrix(subset[:, :3, :3])).as_rotvec()
        for j in range(1, len(rot)):
            rot[j] = _continuous_log(rot[j], rot[j - 1])
        vector = np.column_stack((subset[:, :3, 3], rot))
        observed = []
        for j, (predicted, old) in enumerate(increments[start : end - 1]):
            dp = predicted[:3, 3] - old[:3, 3]
            r1 = (origin.inv() * Rotation.from_matrix(predicted[:3, :3])).as_rotvec()
            r0 = rot[j]
            r1 = _continuous_log(r1, r0)
            observed.append(np.r_[dp, r1 - r0])
        fit = _fuse(vector, np.array(observed), np.array(weights[start : end - 1]))
        rr = (origin * Rotation.from_rotvec(fit[:, 3:])).as_matrix()
        for j in range(len(fit)):
            i = keys[start + j]
            result[i][:3, :3] = rr[j]
            result[i][:3, 3] = fit[j, :3]
    return result, diag
