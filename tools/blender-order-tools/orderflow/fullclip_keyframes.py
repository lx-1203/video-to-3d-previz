"""Reduce redundant native action keys while preserving endpoints and a bounded error."""

from collections import defaultdict
import numpy as np


def retained_samples(times, values, tolerance):
    times = np.asarray(times, float)
    values = np.asarray(values, float)
    kept = {0, len(times) - 1}
    stack = [(0, len(times) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        t = (times[a + 1 : b] - times[a]) / (times[b] - times[a])
        estimate = values[a] + t[:, None] * (values[b] - values[a])
        errors = np.linalg.norm(values[a + 1 : b] - estimate, axis=1)
        worst = int(np.argmax(errors))
        if errors[worst] > tolerance:
            mid = a + 1 + worst
            kept.add(mid)
            stack.extend([(a, mid), (mid, b)])
    return kept


def action_curves(action):
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    return [
        c
        for layer in action.layers
        for strip in layer.strips
        for bag in strip.channelbags
        for c in bag.fcurves
    ]


def reduce_pose_keys(rig):
    groups = defaultdict(list)
    for curve in action_curves(rig.animation_data.action):
        if curve.data_path.endswith(("location", "rotation_quaternion")):
            groups[curve.data_path].append(curve)
    before = after = 0
    for path, curves in groups.items():
        arrays = []
        for curve in curves:
            data = np.empty(2 * len(curve.keyframe_points), np.float32)
            curve.keyframe_points.foreach_get("co", data)
            arrays.append(data.reshape(-1, 2))
        if (
            not arrays
            or len(arrays[0]) < 2
            or not all(np.array_equal(v[:, 0], arrays[0][:, 0]) for v in arrays)
        ):
            continue
        t = arrays[0][:, 0]
        val = np.column_stack([v[:, 1] for v in arrays])
        keep = set()
        # No simplification across a break in measured visibility.
        for interval in np.split(np.arange(len(t)), np.flatnonzero(np.diff(t) > 1) + 1):
            if len(interval) == 1:
                keep.add(int(interval[0]))
            else:
                keep.update(
                    int(interval[0]) + i
                    for i in retained_samples(
                        t[interval],
                        val[interval],
                        0.005 if path.endswith("rotation_quaternion") else 0.002,
                    )
                )
        for curve, arr in zip(curves, arrays):
            before += len(arr)
            points = curve.keyframe_points
            if len(keep) < len(arr):
                compact = arr[sorted(keep)]
                points.clear()
                points.add(len(compact))
                points.foreach_set("co", compact.ravel())
            for point in points:
                point.interpolation = "LINEAR"
            curve.update()
            after += len(points)
    return dict(original=before, retained=after)
