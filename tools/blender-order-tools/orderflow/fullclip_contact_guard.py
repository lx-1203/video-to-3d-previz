"""Validate continuous contact proposals against finite scene and opposing limb meshes."""

import numpy as np

from .fullclip_contact_path import rotations

GAINS = np.linspace(0, 1, 13)


def bounds(vertices, tree):
    return vertices.min(0), vertices.max(0), vertices.mean(0), tree


def hull(points):
    import bmesh
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    bm = bmesh.new()
    try:
        for pt in points:
            bm.verts.new(Vector(pt))
        bmesh.ops.convex_hull(bm, input=list(bm.verts))
        return bounds(points, BVHTree.FromBMesh(bm))
    finally:
        bm.free()


def _inside(point, tree):
    from mathutils import Vector

    direction = Vector((0.52384, 0.68358, 0.11727)).normalized()
    origin = Vector(point)
    crossings = 0
    for _ in range(32):
        hit, _, _, _ = tree.ray_cast(origin, direction)
        if hit is None:
            break
        crossings += 1
        origin = hit + direction * 1e-5
    return crossings % 2 == 1


def _hit(a, b):
    if np.any(a[1] < b[0]) or np.any(a[0] > b[1]):
        return False
    return bool(a[3].overlap(b[3])) or _inside(a[2], b[3]) or _inside(b[2], a[3])


def _candidates(axes, data, angles, nearby):
    from mathutils import Vector

    hip, knee, upper, lower, distal = data
    lateral = rotations(axes[0], GAINS * angles[0])
    thigh = lateral @ rotations(axes[1], GAINS * angles[1])
    shin = lateral @ rotations(axes[1], GAINS * angles[2])
    new_knees = hip + np.einsum("nij,j->ni", thigh, knee - hip)
    top = (upper - hip) @ thigh.transpose(0, 2, 1) + hip
    low = (lower - knee) @ shin.transpose(0, 2, 1) + new_knees[:, None]
    end = (distal - knee) @ shin.transpose(0, 2, 1) + new_knees[:, None]
    out = []
    for i in range(len(GAINS) if np.any(abs(angles) > 1e-6) else 1):
        bones = hull(top[i]), hull(low[i])
        tip = hull(end[i])
        collisions = set()
        end_hits = set()
        support_distance = 0.85
        for name, tread, solid in nearby:
            for block, bone in enumerate(bones):
                if _hit(bone, solid):
                    collisions.add((block, name))
            if not tread:
                continue
            if _hit(tip, solid):
                end_hits.add(name)
                continue
            x, y = tip[2][:2]
            if np.any(tip[2][:2] < solid[0][:2]) or np.any(tip[2][:2] > solid[1][:2]):
                continue
            hit, normal, _, _ = solid[3].ray_cast(
                Vector((x, y, solid[1][2] + 0.02)), Vector((0, 0, -1))
            )
            if hit is not None and abs(normal.z) >= 0.65 and hit.z <= tip[0][2] + 0.004:
                support_distance = min(support_distance, max(0, tip[0][2] - hit.z))
        out.append((bones, collisions, end_hits, support_distance))
    return out if len(out) > 1 else out * len(GAINS)


def _pair_hits(left, right):
    return {(i, j) for i, a in enumerate(left) for j, b in enumerate(right) if _hit(a, b)}


def _select(cost):
    """At most one gain increment per frame for either limb; the zero path is feasible."""
    n = len(GAINS)
    order = np.arange(n * n).reshape(n, n)
    distance = cost[0].copy()
    back = []
    for c in cost[1:]:
        parent = order.copy()
        for axis in (0, 1):
            options = [distance]
            parents = [parent]
            for step in (-1, 1):
                trial = np.roll(distance, step, axis=axis) + 0.18
                bound = [slice(None)] * 2
                bound[axis] = 0 if step == 1 else -1
                trial[tuple(bound)] = np.inf
                options.append(trial)
                parents.append(np.roll(parent, step, axis=axis))
            pick = np.argmin(options, axis=0)[None]
            distance = np.take_along_axis(np.stack(options), pick, axis=0)[0]
            parent = np.take_along_axis(np.stack(parents), pick, axis=0)[0]
        distance += c
        back.append(parent.ravel())
    path = [distance.argmin()]
    for row in reversed(back):
        path.append(row[path[-1]])
    return np.array(np.unravel_index(path[::-1], (n, n))).T


def restrict(proposals, samples):
    if set(proposals) != {"L", "R"}:
        return {side: np.zeros_like(q) for side, q in proposals.items()}
    costs = []
    for f, (axes, limbs, scene_objects) in enumerate(samples):
        has_change = any(np.any(abs(q[f]) > 1e-6) for q in proposals.values())
        if not has_change:
            costs.append(np.zeros((len(GAINS), len(GAINS))))
            continue
        center = np.mean([data[0] for data in limbs.values()], axis=0)
        reach = (
            max(
                np.linalg.norm(k - h) + np.linalg.norm(lo - k, axis=1).max()
                for h, k, _, lo, _ in limbs.values()
            )
            + 0.35
        )
        nearby = [
            obj
            for obj in scene_objects
            if np.all(center + reach >= obj[2][0])
            and np.all(center - reach <= obj[2][1] + [0, 0, 0.8])
        ]
        sides = [_candidates(axes, limbs[s], proposals[s][f], nearby) for s in ("L", "R")]
        original_pairs = _pair_hits(sides[0][0][0], sides[1][0][0])
        unary = []
        for choices in sides:
            _, base_collisions, base_ends, _ = choices[0]
            costs_one = []
            for index, (_, contacts, ends, gap) in enumerate(choices):
                if contacts - base_collisions or ends - base_ends:
                    value = np.inf
                else:
                    value = (
                        8 * len(ends)
                        + 1.4 * len(contacts)
                        + 3 * min(gap, 0.65)
                        + 1.4 * min(gap / 0.08, 1)
                        + 0.06 * GAINS[index]
                    )
                costs_one.append(value)
            unary.append(np.array(costs_one))
        pair_cost = unary[0][:, None] + unary[1][None, :]
        for i, j in zip(*np.where(np.isfinite(pair_cost))):
            crossed = _pair_hits(sides[0][i][0], sides[1][j][0])
            if crossed - original_pairs:
                pair_cost[i, j] = np.inf
            else:
                pair_cost[i, j] += 1.5 * len(crossed)
        costs.append(pair_cost)
    gains = GAINS[_select(costs)]
    return {side: proposals[side] * gains[:, i, None] for i, side in enumerate(("L", "R"))}
