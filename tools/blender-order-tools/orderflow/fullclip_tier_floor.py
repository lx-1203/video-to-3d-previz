"""Keep a fixed room datum between rows and recess it beneath lower seating banks."""

from collections import defaultdict
import numpy as np


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def hull(points):
    points = sorted(set(map(tuple, np.round(points, 7))))
    if len(points) < 3:
        return np.empty((0, 2))

    def half(seq):
        edge = []
        for xy in seq:
            p = np.asarray(xy)
            while len(edge) >= 2 and cross(edge[-1] - edge[-2], p - edge[-1]) <= 1e-8:
                edge.pop()
            edge.append(p)
        return edge

    return np.array(half(points)[:-1] + half(reversed(points))[:-1])


def area(poly):
    x, y = poly.T
    return 0.5 * abs(x @ np.roll(y, -1) - y @ np.roll(x, -1))


def split(poly, a, b):
    """Split a convex polygon at an oriented edge into inside and outside."""
    left = []
    right = []
    d = b - a
    for p, q in zip(poly, np.roll(poly, -1, axis=0)):
        t = cross(d, p - a)
        u = cross(d, q - a)
        if t >= -1e-8:
            left.append(p)
        if t <= 1e-8:
            right.append(p)
        if (t > 1e-8 and u < -1e-8) or (t < -1e-8 and u > 1e-8):
            edge = p + (q - p) * t / (t - u)
            left.append(edge)
            right.append(edge)
    return hull(left) if len(left) >= 3 else np.empty((0, 2)), hull(right) if len(
        right
    ) >= 3 else np.empty((0, 2))


def subtract(poly, cut):
    if np.any(poly.max(0) < cut.min(0)) or np.any(cut.max(0) < poly.min(0)):
        return [poly]
    inside = poly
    outside = []
    for a, b in zip(cut, np.roll(cut, -1, axis=0)):
        inside, other = split(inside, a, b)
        if len(inside) < 3:
            return [poly]
        if len(other) >= 3 and area(other) > 1e-8:
            outside.append(other)
    return outside


def merge_neighbors(pieces):
    """Dissolve shared edges whenever their union remains convex."""
    pending = list(pieces)
    while True:
        edges = {}
        for i, poly in enumerate(pending):
            for a, b in zip(poly, np.roll(poly, -1, axis=0)):
                edge = tuple(sorted((tuple(np.round(a, 6)), tuple(np.round(b, 6)))))
                edges.setdefault(edge, []).append(i)
        pairs = {
            tuple(sorted(ids))
            for ids in edges.values()
            if len(ids) == 2 and ids[0] != ids[1]
        }
        merged = set()
        combined = []
        for i, j in sorted(pairs):
            if i in merged or j in merged:
                continue
            test = hull(np.vstack([pending[i], pending[j]]))
            if abs(area(test) - area(pending[i]) - area(pending[j])) < 1e-6:
                combined.append(test)
                merged.update((i, j))
        if not merged:
            return pending
        pending = [p for i, p in enumerate(pending) if i not in merged] + combined


def cutouts(parts, camera_origins, z):
    """Construction footprints and visual access to a static row are separate convex regions."""
    groups = defaultdict(list)
    for frame, row, kind, vertices in parts:
        v = np.asarray(vertices)
        low = v[v[:, 2] < z + 0.015]
        if not len(low):
            continue
        visual = kind in {
            "Seat back",
            "Seat cushion",
            "Seat back frame",
            "Seat armrest",
        }
        group = groups[(frame, row, visual)]
        group.extend(v[:, :2])
        if visual:
            for cam in camera_origins:
                if cam[2] <= z + 0.015:
                    continue
                pts = low[low[:, 2] < z]
                if len(pts):
                    ray = pts - cam
                    group.extend(
                        cam[:2] + ray[:, :2] * ((z - cam[2]) / ray[:, 2])[:, None]
                    )
    return [
        poly for _, points in sorted(groups.items()) if len(poly := hull(points)) >= 3
    ]


def build(scene, floors, members, center, factor, origins):
    """Keep original floor height on finite convex pieces, retaining the lower base below openings."""
    import bpy
    from mathutils import Matrix, Vector

    if len(floors) != 1 or factor <= 1.015:
        return [], {}
    obj = floors[0]

    def vertices(o):
        return np.asarray([tuple(o.matrix_world @ v.co) for v in o.data.vertices])

    fv = vertices(obj)
    z0, z = fv[:, 2].min(), fv[:, 2].max()
    outline = hull(fv[:, :2])
    if (
        len(outline) != 4
        or max(np.minimum(abs(fv[:, 2] - z), abs(fv[:, 2] - z0))) > 0.002
    ):
        return [], {}
    parts = [
        (
            o.get("source_frame"),
            o.get("seat_row"),
            o.get("reconstruction_kind"),
            vertices(o),
        )
        for o in members
        if "seat_row" in o
    ]
    cuts = cutouts(parts, origins, z)
    if not cuts:
        return [], {}
    pieces = [outline]
    for cut in cuts:
        pieces = [q for poly in pieces for q in subtract(poly, cut)]
    pieces = merge_neighbors(pieces)
    original = obj.matrix_world.copy()
    move = (
        Matrix.Translation(Vector(center))
        @ Matrix.Scale(factor, 4)
        @ Matrix.Translation(Vector(-center))
    )
    obj.matrix_world = move @ original
    low = float(np.max(vertices(obj)[:, 2]))
    if low >= z - 0.01:
        obj.matrix_world = original
        return [], {}
    generated = []
    try:
        for i, poly in enumerate(pieces):
            n = len(poly)
            coords = [(*xy, zz) for zz in (low, z) for xy in poly]
            faces = [list(reversed(range(n))), list(range(n, 2 * n))]
            faces.extend((j, (j + 1) % n, (j + 1) % n + n, n + j) for j in range(n))
            mesh = bpy.data.meshes.new("Room floor partition")
            mesh.from_pydata(coords, [], faces)
            mesh.update()
            part = obj.copy()
            part.data = mesh
            part.name = f"{obj.name} tread {i + 1}"
            obj.users_collection[0].objects.link(part)
            part.matrix_world = Matrix.Identity(4)
            for mat in obj.data.materials:
                mesh.materials.append(mat)
            part["estimated_support"] = True
            part["floor_partition"] = True
            generated.append(part)
        bpy.context.view_layer.update()
    except Exception:
        obj.matrix_world = original
        discard(generated)
        raise
    return generated, dict(
        state="static_row_recesses",
        original_top_m=float(z),
        base_top_m=float(np.max(vertices(obj)[:, 2])),
        original_area_m2=round(area(outline), 5),
        retained_area_m2=round(sum(map(area, pieces)), 5),
        row_bounds=len(cuts),
        convex_parts=len(pieces),
    )


def discard(generated):
    import bpy

    for part in generated:
        data = part.data
        bpy.data.objects.remove(part, do_unlink=True)
        if not data.users:
            bpy.data.meshes.remove(data)
