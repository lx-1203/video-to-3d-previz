"""Plant a pair of inferred feet on a fixed finite surface using rigid two-link IK."""

import numpy as np

SOLE_CLEARANCE_M = 0.0018
TREAD_GUARD_M = 0.00035
OBSTACLE_GUARD_M = 0.004


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def swing(points, source, target):
    """Shortest-arc rotation of a point cloud about each pivot, with no scaling."""
    a, b = unit(source), unit(target)
    axis = np.cross(a, b)[:, None]
    cosine = np.sum(a * b, axis=1)[:, None, None]
    c = np.cross(axis, points)
    result = points + c + np.cross(axis, c) / np.maximum(1 + cosine, 1.0e-12)
    # At pi the cross product loses its direction. Construct a deterministic
    # perpendicular axis, then align -source to the actual near-antipodal target.
    opposite = cosine[:, 0, 0] < -0.999999
    if opposite.any():
        start = a[opposite]
        basis = np.eye(3)[np.abs(start).argmin(axis=1)]
        pivot = unit(np.cross(start, basis))[:, None]
        vec = points[opposite]
        flipped = 2 * pivot * (pivot * vec).sum(-1, keepdims=True) - vec
        adjust = np.cross(-start, b[opposite])[:, None]
        delta = np.cross(adjust, flipped)
        result[opposite] = (
            flipped + delta + np.cross(adjust, delta) / (1 - cosine[opposite])
        )
    return result


def solve_leg(data, translation, soles, normal, pole):
    hip, knee, tip, thigh, shin, corners = data
    h = hip + translation
    upper = knee - hip
    lower = tip - knee
    lengths = np.linalg.norm(upper, axis=1), np.linalg.norm(lower, axis=1)
    sole_center = soles.copy()
    # Fit the lowest corner of the rigid distal face to the plane. Keeping its
    # centroid xy fixed permits the actor to pivot on a stationary pair of feet.
    for _ in range(5):
        span = sole_center - h
        distance = np.linalg.norm(span, axis=1)
        along = unit(span)
        bend = unit(pole - (pole * along).sum(1)[:, None] * along)
        u, v = lengths
        offset = (u * u - v * v + distance * distance) / np.maximum(2 * distance, 1e-6)
        angle = np.sqrt(np.maximum(u * u - offset * offset, 0))
        k = h + along * offset[:, None] + bend * angle[:, None]
        b = sole_center - k
        offsets = swing(corners - tip[:, None], lower, b)
        sole_center[:, 2] = (
            soles[:, 2]
            + SOLE_CLEARANCE_M
            - np.min(offsets @ normal, axis=1) / normal[2]
        )
    valid = (distance < u + v - 0.002) & (distance > abs(u - v) + 0.002)
    high = swing(thigh - hip[:, None], upper, k - h) + h[:, None]
    low = swing(shin - knee[:, None], lower, b) + k[:, None]
    return valid, high, low, k - h, b


def solid_planes(points, polygons):
    center = points.mean(0)
    normals, offsets = [], []
    for face in polygons:
        q = points[face]
        n = unit(np.cross(q[1] - q[0], q[2] - q[0]))
        if np.linalg.norm(n) < 0.5:
            continue
        if (q.mean(0) - center) @ n < 0:
            n = -n
        d = q[0] @ n
        if np.max(points @ n - d) > 0.001:
            return None
        if not any(
            n @ old > 0.99995 and abs(d - val) < 0.0001
            for old, val in zip(normals, offsets)
        ):
            normals.append(n)
            offsets.append(d)
    if len(normals) < 4:
        return None
    return np.array(normals), np.array(offsets)


def penetrates(points, solids, support_name=None):
    """Conservative finite face-plane separation plus broad-phase bounds."""
    low, high = points.min(1), points.max(1)
    blocked = np.zeros(len(low), dtype=bool)
    for name, lo, hi, n, d in solids:
        gap = (
            TREAD_GUARD_M if support_name and name in support_name else OBSTACLE_GUARD_M
        )
        keep = ((high >= lo - gap) & (low <= hi + gap)).all(1) & ~blocked
        if not keep.any():
            continue
        separation = (points[keep] @ n.T - d).min(1).max(1)
        blocked[keep] = separation < gap
    return blocked


def project_top(xy, solid, margin=0):
    _, lo, hi, n, d = solid
    if np.any(xy < lo[:2]) or np.any(xy > hi[:2]):
        return None
    ids = np.flatnonzero(n[:, 2] > 0.05)
    z = (d[ids] - n[ids, :2] @ xy) / n[ids, 2]
    i = ids[z.argmin()]
    q = np.r_[xy, z.min()]
    if n[i, 2] < 0.8 or np.max(q @ n.T - d) > 0.001:
        return None
    if margin:
        for direction in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            offset = np.array(direction) * margin
            edge = q + np.r_[offset, -offset @ n[i, :2] / n[i, 2]]
            if np.max(edge @ n.T - d) > 0.001:
                return None
    return q, n[i]


def lateral_axis(data):
    lateral = np.median(data[0][0] - data[1][0], axis=0)
    lateral[2] = 0
    return unit(lateral)


def options(data, origins, shoulders, support, reach, obstacles):
    """Search for stationary soles entirely inside one measured planar face."""
    middle = (data[0][0] + data[1][0]) * 0.5
    forward = unit(np.median(shoulders - origins, axis=0) * [1, 1, 0])
    across = np.cross(forward, [0, 0, 1])
    lateral = lateral_axis(data)
    ray = (shoulders - origins) / ((shoulders - origins) @ forward)[:, None]
    spread = max(
        0.10, np.median(np.linalg.norm(data[0][0] - data[1][0], axis=1)) * 0.75
    )
    candidates = []
    ref = middle.mean(0)
    reach_xy = np.abs(across) * reach * 0.9 + np.abs(forward) * reach * 3
    for name, patches in support:
        # The two feet can plant on separate convex tiles of a continuous
        # landing. Test each sample against the actual union, keeping holes.
        patches = [
            p
            for p in patches
            if np.all(p[2][:2] >= ref[:2] - reach_xy[:2])
            and np.all(p[1][:2] <= ref[:2] + reach_xy[:2])
        ]
        if not patches:
            continue
        low = np.maximum(
            np.min([p[1][:2] for p in patches], axis=0), ref[:2] - reach_xy[:2]
        )
        high = np.minimum(
            np.max([p[2][:2] for p in patches], axis=0), ref[:2] + reach_xy[:2]
        )
        sites = []
        for x in np.arange(low[0], high[0], 0.045):
            for y in np.arange(low[1], high[1], 0.045):
                xy = np.array([x, y])
                nearby = [
                    p
                    for p in patches
                    if np.all(xy >= p[1][:2] - 0.065) and np.all(xy <= p[2][:2] + 0.065)
                ]
                center = next(
                    (v for p in nearby if (v := project_top(xy, p)) is not None), None
                )
                if center is None:
                    continue
                offsets = ((0.052, 0), (-0.052, 0), (0, 0.052), (0, -0.052))
                covered = True
                for dx, dy in offsets:
                    pt = xy + [dx, dy]
                    if not any(
                        (v := project_top(pt, p)) is not None
                        and abs((v[0] - center[0]) @ center[1]) < 0.001
                        for p in nearby
                    ):
                        covered = False
                        break
                if covered:
                    sites.append(center)
        if len(sites) < 2:
            continue
        points = np.array([q for q, n in sites])
        normals = np.array([n for q, n in sites])
        candidate_depth = (points - ref) @ forward
        expected_hip_height = ref[2] + candidate_depth * np.median(ray[:, 2])
        plausible = (
            (abs((points - ref) @ across) < reach * 0.95)
            & (points[:, 2] < expected_hip_height - reach * 0.29)
            & (points[:, 2] > expected_hip_height - reach * 1.25)
        )
        # Require a clear sole and ankle-sized volume on top of each tread.
        if plausible.any():
            ids = np.flatnonzero(plausible)
            probes = points[ids, None, :] + np.array(
                [
                    [x, y, z]
                    for x in (-0.06, 0.06)
                    for y in (-0.06, 0.06)
                    for z in (0.035, 0.19)
                ]
            )
            plausible[ids] &= ~penetrates(probes, obstacles)
        points, normals = points[plausible], normals[plausible]
        for left, nl in zip(points, normals):
            diff = left - points
            dist_xy = np.linalg.norm(diff[:, :2], axis=1)
            ids = np.flatnonzero(
                (diff @ lateral > 0.045) & (dist_xy > 0.17) & (dist_xy < 0.36)
            )
            for j in ids:
                soles = np.stack([left, points[j]])
                center_foot = soles.mean(0)
                separation = (center_foot - middle) @ forward
                trans = ray * separation[:, None]
                dist = np.linalg.norm(
                    np.stack([v[0] for v in data], 1) + trans[:, None] - soles, axis=2
                )
                height = (
                    np.stack([v[0][:, 2] for v in data], 1)
                    + trans[:, 2, None]
                    - soles[:, 2]
                )
                if (
                    dist.max() > reach + 0.095
                    or height.min() < reach * 0.37
                    or height.max() > reach + 0.09
                ):
                    continue
                score = (
                    abs(separation).mean()
                    + 3 * np.maximum(dist.max() - reach + 0.02, 0)
                    + 2 * np.mean(abs(height - reach * 0.82))
                    + abs((center_foot - ref) @ across) * 0.75
                    + 0.12 * np.linalg.norm(diff[j] - 2 * spread * lateral)
                )
                candidates.append(
                    (score, name, soles, np.stack([nl, normals[j]]), ray, forward)
                )
    return sorted(candidates, key=lambda x: x[0])


def _planar_hull(points):
    pts = sorted(set(map(tuple, points)))

    def chain(order):
        half = []
        for pt in order:
            while len(half) >= 2:
                a, b = half[-2:]
                if (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (
                    pt[0] - a[0]
                ) > 1.0e-12:
                    break
                half.pop()
            half.append(pt)
        return half

    return np.array(chain(pts)[:-1] + chain(reversed(pts))[:-1])


def _area(poly):
    if len(poly) < 3:
        return 0.0
    return 0.5 * abs(
        np.dot(poly[:, 0], np.roll(poly[:, 1], 1))
        - np.dot(poly[:, 1], np.roll(poly[:, 0], 1))
    )


def _clip(poly, values):
    inside = values <= 1.0e-8
    if inside.all():
        return poly
    edges = []
    for i, end in enumerate(poly):
        a, b = values[i - 1], values[i]
        if inside[i] != inside[i - 1]:
            edges.append(poly[i - 1] + a / (a - b) * (end - poly[i - 1]))
        if inside[i]:
            edges.append(end)
    return np.asarray(edges)


def footprint(points, sole, normal, patches):
    """Exact projected area over disjoint convex tiles and support under the lowest point."""
    outline = _planar_hull(points[:, :2])
    total = _area(outline)
    if total < 1.0e-10:
        return 0.0, False, []
    lowest = points[points[:, 2].argmin(), :2]
    minimum, maximum = outline.min(0), outline.max(0)
    covered = 0.0
    anchored = False
    names = []
    for name, lo, hi, normals, offsets in patches:
        if np.any(maximum < lo[:2]) or np.any(minimum > hi[:2]):
            continue
        # Substitute z(x,y) of the contact plane into each 3D half-space.
        axes = normals[:, :2] - np.outer(normals[:, 2], normal[:2] / normal[2])
        bound = offsets - normals[:, 2] * (sole @ normal / normal[2])
        anchored |= bool(np.all(axes @ lowest - bound <= 1.0e-7))
        poly = outline
        for a, b in zip(axes, bound):
            if len(poly) < 3:
                break
            poly = _clip(poly, poly @ a - b)
        area = _area(poly)
        if area > 1.0e-10:
            names.append(name)
            covered += area
    return min(covered / total, 1.0), anchored, names


def _supported_pose(data, poses, soles, normals, patches):
    names = set()
    for i, (leg, sole, normal) in enumerate(zip(data, soles, normals)):
        hip, knee, tip, _, _, corners = leg
        upper, lower = poses[:, 1 + 2 * i], poses[:, 2 + 2 * i]
        end = hip + poses[:, 0] + upper + lower
        actual = swing(corners - tip[:, None], tip - knee, lower) + end[:, None]
        for points in actual:
            area, anchored, touching = footprint(points, sole, normal, patches)
            if not anchored or area < 0.85:
                return None
            names.update(touching)
    return tuple(sorted(names))


def _boxes_overlap(left, right):
    # Compare the two finite evaluated hulls, including enclosed parts.
    from . import fullclip_contact_guard as guard

    return any(guard._hit(a, b) for a in left for b in right)


def _smooth_verified(
    data, poses, soles, normals, ray, depth_limits, solids, support_name
):
    """Smooth only branches that still plant both rigid soles and clear the set."""
    from .fullclip_contact_guard import hull

    # Keep camera-ray root motion and measured bone lengths while blending the
    # two bending planes. Alternating frames makes each energy check independent.
    for _ in range(5):
        for parity in (0, 1):
            ids = np.arange(1 + parity, len(poses) - 1, 2)
            if not len(ids):
                continue
            previous, current, following = poses[ids - 1], poses[ids], poses[ids + 1]
            mixed = 0.25 * previous + 0.50 * current + 0.25 * following
            direction = unit(ray[ids])
            travel = (mixed[:, 0] * direction).sum(1)
            translation = travel[:, None] * direction
            ok = (
                np.ones(len(ids), bool)
                if depth_limits is None
                else travel <= depth_limits[ids]
            )
            candidates = [translation]
            limbs = []
            for side, (leg, sole, normal) in enumerate(zip(data, soles, normals)):
                subset = [v[ids] for v in leg]
                pole = mixed[:, 1 + 2 * side]
                feasible, high, low, upper, lower = solve_leg(
                    subset,
                    translation,
                    np.broadcast_to(sole, (len(ids), 3)),
                    normal,
                    pole,
                )
                ok &= (
                    feasible
                    & ~penetrates(high, solids, support_name)
                    & ~penetrates(low, solids, support_name)
                )
                candidates.extend((upper, lower))
                limbs.append((high, low))
            trial = np.stack(candidates, axis=1)

            def energy(q):
                return ((q - previous) ** 2 + (q - following) ** 2).sum((1, 2))

            ok &= energy(trial) < energy(current) - 1.0e-10
            for i in np.flatnonzero(ok):
                left, right = [[hull(part[i]) for part in limb] for limb in limbs]
                if not _boxes_overlap(left, right):
                    poses[ids[i]] = trial[i]
    return poses


def plan(
    data, origins, shoulders, support, solids, max_candidates=180, depth_limits=None
):
    u, k, t, *_ = data[0]
    reach = float(
        np.median(np.linalg.norm(u - k, axis=1) + np.linalg.norm(t - k, axis=1))
    )
    middle = (data[0][0] + data[1][0]) * 0.5
    axis = lateral_axis(data)
    forward_knee = np.cross([0.0, 0.0, 1.0], axis)
    options_list = options(data, origins, shoulders, support, reach, solids)
    # Cover the finite surface before considering nearby variations in stance.
    representatives = {}
    for candidate in options_list:
        cell = tuple(np.floor(candidate[2].mean(0)[:2] / 0.15).astype(int))
        representatives.setdefault((candidate[1], cell), candidate)
    options_list = list(representatives.values())
    solids = [
        s
        for s in solids
        if np.all(s[2] > middle.min(0) - [1.4, 3.5, 1.4])
        and np.all(s[1] < middle.max(0) + [1.4, 3.5, 0.25])
    ]

    for _, name, soles, normals, ray, forward in options_list[:max_candidates]:
        # Allow the pelvis to move fore/aft of planted feet while preserving the
        # original camera ray through the shoulder midpoint.
        base = np.clip((soles.mean(0) - middle) @ forward, -reach * 2.9, reach * 2.9)
        ray_scale = (ray * unit(shoulders - origins)).sum(1)
        if depth_limits is not None and np.any(
            (base - 0.36) * ray_scale > depth_limits
        ):
            continue
        best = [None] * len(base)
        # Reject unreachable stances on the most constrained frame first.
        sample = np.unique(
            np.r_[
                np.linspace(0, len(base) - 1, 7, dtype=int),
                np.argmax(data[0][0][:, 2]),
                np.argmin(data[1][0][:, 0]),
            ]
        )
        active = [np.asarray(sample), np.arange(len(base))]
        for subset in active:
            sample_data = [[v[subset] for v in leg] for leg in data]
            subset_ok = False
            for bias in (0, -0.12, 0.12, -0.24, 0.24, -0.36, 0.36):
                trans = ray[subset] * (base[subset] + bias)[:, None]
                bilateral = []
                for i, (leg, sole, normal) in enumerate(
                    zip(sample_data, soles, normals)
                ):
                    choices = []
                    for rotation in (0, 0.52, -0.52, 1.05, -1.05, 2.1, -2.1, 3.14):
                        p = forward_knee * np.cos(rotation) + axis * np.sin(rotation)
                        res = solve_leg(
                            leg,
                            trans,
                            np.broadcast_to(sole, (len(subset), 3)),
                            normal,
                            p,
                        )
                        valid, hi, lo, upper, lower = res
                        clear = (
                            valid
                            & ~penetrates(hi, solids, name)
                            & ~penetrates(lo, solids, name)
                        )
                        if depth_limits is not None:
                            allowed = depth_limits[subset]
                            # A candidate shoulder must stay in front of the
                            # witnessed upper-body occluders along its camera ray.
                            change = np.sum(
                                trans * unit(shoulders[subset] - origins[subset]),
                                axis=1,
                            )
                            clear &= change <= allowed
                        choices.append((clear, hi, lo, upper, lower))
                    bilateral.append(choices)
                for idx, f in enumerate(subset):
                    if best[f] is not None:
                        continue
                    for left in bilateral[0]:
                        if not left[0][idx]:
                            continue
                        for right in bilateral[1]:
                            if not right[0][idx]:
                                continue
                            from .fullclip_contact_guard import hull

                            if _boxes_overlap(
                                [hull(left[1][idx]), hull(left[2][idx])],
                                [hull(right[1][idx]), hull(right[2][idx])],
                            ):
                                continue
                            best[f] = (
                                trans[idx],
                                left[3][idx],
                                left[4][idx],
                                right[3][idx],
                                right[4][idx],
                            )
                            break
                        if best[f] is not None:
                            break
                if all(best[f] is not None for f in subset):
                    subset_ok = True
                    break
            if not subset_ok:
                break
        if all(x is not None for x in best):
            poses = _smooth_verified(
                data, np.array(best), soles, normals, ray, depth_limits, solids, name
            )
            patches = [solid for solid in solids if solid[0] in name]
            touching = _supported_pose(data, poses, soles, normals, patches)
            if touching:
                return touching, soles, poses
    return None
