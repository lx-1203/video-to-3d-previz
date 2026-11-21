"""Fit a shared shoulder orientation offset when a bent close-up supplies repeated sleeve roots."""

import numpy as np
from mathutils import Matrix, Vector


def rotation(vector):
    angle = float(np.linalg.norm(vector))
    if angle < 1e-9:
        return Matrix.Identity(3)
    return Matrix.Rotation(angle, 3, Vector(vector / angle))


def refine(sequence, axes, parts, shot, width, height, mesh=None):
    from .fullclip_binding import local_pose

    if not axes:
        return axes, {}
    K = np.asarray(shot["intrinsics"])
    fx, fy = K[0, 0] * width, K[1, 1] * height
    cx, cy = K[0, 2] * width, K[1, 2] * height
    frames = []
    for i, actor in sequence:
        if i not in axes or not actor.get("shoulder_support"):
            continue
        joints = actor["joints2d"]
        points = [joints[side + "_shoulder"] for side in ("left", "right")]
        if not all(j.get("score", 0) >= 0.31 and j.get("u") is not None for j in points):
            continue
        sh = np.array([(j["u"], j["v"]) for j in points])
        if (
            (sh < 0).any()
            or (sh > [width, height]).any()
            or np.linalg.norm(sh[0] - sh[1]) < 0.13 * height
        ):
            continue
        head = actor["anchors"]["head"]
        uv = np.array([head["u"], head["v"]])
        depth = fx / max(1.0, actor["pixels_per_meter"])
        headcam = np.array([(uv[0] - cx) / fx * depth, depth, (cy - uv[1]) / fy * depth])
        frames.append((i, actor, sh, headcam))
    if len(frames) < 4:
        return axes, {}
    frames = frames[:: max(1, len(frames) // 12)]

    def projected_error(turn):
        Q = rotation(turn)
        err = []
        for i, actor, sh, headcam in frames:
            rotated = tuple(Q @ Vector(a) for a in axes[i])
            _, lm = local_pose(actor, parts, rotated)
            delta = np.array(
                [np.asarray(lm[side + "_shoulder"]) - lm["head"] for side in ("left", "right")]
            )
            pts = delta + headcam
            uv = np.stack(
                (
                    fx * pts[:, 0] / pts[:, 1].clip(0.1) + cx,
                    cy - fy * pts[:, 2] / pts[:, 1].clip(0.1),
                ),
                axis=1,
            )
            err.extend(((uv - sh) / height).ravel())
        return np.asarray(err)

    dq = 0.006
    q = np.zeros(3)
    baseline = projected_error(q)
    for _ in range(10):
        error = projected_error(q)
        J = np.stack([(projected_error(q + v) - error) / dq for v in np.eye(3) * dq], axis=1)
        weights = np.sqrt(np.minimum(1.0, 0.14 / np.maximum(abs(error), 1e-6)))
        # Keep the depth surface as a prior. Single-view joint observations are noisy.
        prior = np.eye(3) * (0.16 * np.sqrt(len(frames)))
        step = np.linalg.lstsq(
            np.vstack((J * weights[:, None], prior)),
            np.r_[-error * weights, -prior @ q],
            rcond=None,
        )[0]
        step *= min(1.0, 0.24 / max(1e-9, np.linalg.norm(step)))
        if np.linalg.norm(step) < 0.001:
            break
        trial = q + step
        if np.linalg.norm(trial) > 1.45:
            trial *= 1.45 / np.linalg.norm(trial)
        q = trial
    old = float(np.median(np.linalg.norm(baseline.reshape(-1, 2), axis=1)))
    new = float(np.median(np.linalg.norm(projected_error(q).reshape(-1, 2), axis=1)))
    report = dict(
        frames=[f[0] for f in frames],
        before_median_px=old * height,
        after_median_px=new * height,
        applied=False,
    )
    if new >= 0.8 * old or old - new < 0.025:
        return axes, report
    Q = rotation(q)
    report["applied"] = True
    report["shared_angle_degrees"] = float(np.rad2deg(np.linalg.norm(q)))
    if mesh is not None:
        from .fullclip_close_outline import permitted_strength

        limits = permitted_strength(sequence, axes, q, parts, mesh, shot, height, width)
        if limits:
            times = sorted(limits)
            selected = [limits[i][0] for i in times]
            report["silhouette_strength"] = {str(i): value for i, value in limits.items()}
            corrected = {}
            for i, ax in axes.items():
                strength = float(np.interp(i, times, selected))
                Q = rotation(q * strength)
                corrected[i] = tuple(Q @ Vector(a) for a in ax)
            report["full_strength_median_px"] = report.pop("after_median_px")
            return corrected, report
    return {i: tuple(Q @ Vector(a) for a in ax) for i, ax in axes.items()}, report
