"""Fit a rigid solid to multiple views of an already motion-confirmed pale handover."""

import math

import cv2
import numpy as np


def shaded_outline(image, sample):
    """Recover contiguous muted folds below a bright face without growing through a hand."""
    poly = np.asarray(sample["polygon_uv"], np.int32)
    span = sample["axes_px"][0]
    pad = int(np.ceil(0.35 * span))
    x, y = np.maximum(0, poly.min(0) - pad)
    xx, yy = np.minimum(image.shape[1::-1], poly.max(0) + pad + 1)
    tile = image[y:yy, x:xx]
    old = np.zeros(tile.shape[:2], np.uint8)
    local = poly - np.int32([x, y])
    cv2.fillPoly(old, [local], 1)
    hull = np.zeros_like(old)
    cv2.fillConvexPoly(hull, cv2.convexHull(local), 1)
    lum, a, b = cv2.split(cv2.cvtColor(tile, cv2.COLOR_BGR2LAB).astype(float))
    blue, green, red = tile.astype(float).transpose(2, 0, 1)
    ref = np.median(np.stack((lum, a, b))[:, old > 0], axis=1)
    v = np.arange(len(old))[:, None]
    bottom = len(hull) - 1 - np.argmax(hull[::-1], axis=0)
    top = np.argmax(hull, axis=0)
    shared = len({c["track_id"] for c in sample["contacts"]}) > 1
    extra = (bottom - top) * 0.15 + span * (0.14 if shared else 0.065)
    zone = (v >= top) & (v < bottom + extra) & hull.any(axis=0)
    # Dark folds stay close in chroma to the pale seed. A warmer intervening
    # finger or a dark sleeve ends the search, even if a matching seat lies below.
    fold = (
        (a < min(132, ref[1] + 13))
        & (a > ref[1] - 6)
        & (b < min(146, ref[2] + 16))
        & (b > ref[2] - 12)
        & (lum > max(31, ref[0] * 0.225))
        & (lum < ref[0] * 1.07)
        & (red < green + 14)
    )
    zone &= np.cumsum((v > bottom) & ~fold, axis=0) < 2
    allowed = zone & fold
    labels = np.zeros_like(old)
    labels[zone & (a < 134) & (lum > 28)] = cv2.GC_PR_BGD
    labels[allowed | (old > 0)] = cv2.GC_PR_FGD
    interior = cv2.erode(old, np.ones((3, 3), np.uint8)) > 0
    labels[interior | ((hull > 0) & allowed)] = cv2.GC_FGD
    labels[allowed & (v <= bottom + 0.62 * extra) & (green > red + 3)] = cv2.GC_FGD
    if not np.any(labels == cv2.GC_FGD):
        return poly
    cv2.setRNGSeed(13)
    cv2.grabCut(tile, labels, None, np.zeros((1, 65)), np.zeros((1, 65)), 3, cv2.GC_INIT_WITH_MASK)
    contours, _ = cv2.findContours(labels % 2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return poly
    largest = max(contours, key=cv2.contourArea)
    return cv2.approxPolyDP(largest, max(0.8, span * 0.008), True).reshape(-1, 2) + [x, y]


def solid(vertices_by_frame, samples):
    """Loft a watertight body from the median broad, one-actor silhouette."""
    profiles = []
    turned_profiles = []
    bins = np.linspace(-0.49, 0.49, 13)
    for poly, sample in zip(vertices_by_frame, samples):
        shared = len({c["track_id"] for c in sample["contacts"]}) > 1
        # Image y points down. Fit the long axis toward positive image x.
        vec = np.linalg.eigh(np.cov(poly.T))[1][:, -1]
        if vec[0] < 0:
            vec = -vec
        coords = poly @ np.array([vec, [vec[1], -vec[0]]]).T
        width = np.ptp(coords[:, 0])
        if width < 8 or (not shared and np.ptp(coords[:, 1]) < 0.25 * width):
            continue
        coords = (coords - (coords.min(0) + coords.max(0)) / 2) / width
        chain = np.concatenate((coords, coords[:1]))
        profile = []
        for x in bins:
            cuts = []
            for a, b in zip(chain, chain[1:]):
                if min(a[0], b[0]) <= x < max(a[0], b[0]):
                    t = (x - a[0]) / (b[0] - a[0])
                    cuts.append(a[1] + t * (b[1] - a[1]))
            if len(cuts) < 2:
                break
            profile.append((min(cuts), max(cuts)))
        if len(profile) == len(bins):
            if shared:
                turned_profiles.append(profile)
            else:
                profiles.append(profile)
    if len(profiles) < 5:
        return None
    profile = np.median(profiles, axis=0)
    # Hidden depth is unobservable. Use a shallow rounded section and preserve
    # this physical depth for the entire track, including at partial occlusions.
    # Under a turn the line of the fold shifts along the length. Use its
    # observed curvature as a depth prior, subtracting the fitted straight slope.
    bends = np.zeros(len(bins))
    if len(turned_profiles) >= 4:
        centers = np.mean(turned_profiles, axis=2)
        bends = np.median(centers, axis=0)
        bends -= np.polyval(np.polyfit(bins, bends, 1), bins)
    verts = []
    for x, (lo, hi), bend in zip(bins, profile, bends):
        mid = (lo + hi) * 0.5
        radius = max(0.015, (hi - lo) * 0.5)
        for phase in np.arange(12) * (2 * math.pi / 12):
            verts.append(
                [x, mid + radius * math.cos(phase), bend + radius * 0.28 * math.sin(phase)]
            )
    faces = [list(reversed(range(12))), list(range(len(verts) - 12, len(verts)))]
    for start in range(0, len(verts) - 12, 12):
        for i in range(12):
            a, b = start + i, start + (i + 1) % 12
            faces.append([a, b, b + 12, a + 12])
    return np.asarray(verts), faces


def hand_depth_candidates(samples, polygons, observations):
    """Check palm proximity against a visible shaded face and adjacent stable contacts."""
    if __package__:
        from .fullclip_props import contacts
    else:
        from fullclip_props import contacts
    direct = {
        sample["frame"]: {(c["track_id"], c["side"]) for c in sample["contacts"]}
        for sample in samples
    }
    stable = {
        i: keys & (direct.get(i - 1, set()) | direct.get(i + 1, set()))
        for i, keys in direct.items()
    }
    for sample, poly in zip(samples, polygons):
        i = sample["frame"]
        near = set().union(*(stable.get(f, set()) for f in range(i - 2, i + 3)))
        keep = set(stable[i])
        # Temporal support identifies the prospective hand; the same-frame
        # expanded contour must also lie within its measured reach.
        for hand in contacts(observations["frames"][i]["actors"]):
            key = (hand["track_id"], hand["side"])
            if key in near:
                dist = cv2.pointPolygonTest(poly.astype(np.float32), tuple(hand["palm"]), True)
                if dist >= -0.30 * hand["radius"]:
                    keep.add(key)
        if keep:
            sample["depth_contacts"] = [dict(track_id=k, side=v) for k, v in sorted(keep)]


def fit_handover_solids(source, tracks, observations):
    """Attach a fixed mesh only to long, chromatically compatible multi-actor tracks."""
    import av

    eligible = []
    for track in tracks:
        samples = track["samples"]
        if len(samples) < 12:
            continue
        exchanged = sum(len({c["track_id"] for c in s["contacts"]}) > 1 for s in samples)
        color = np.median([s["rgb"] for s in samples], axis=0)
        # This shaded-patch estimator requires a measurable cool/neutral body.
        if exchanged < 3 or color.mean() < 105 or color[1] < color[0] + 4 or np.ptp(color) > 56:
            continue
        eligible.append(track)
    if not eligible:
        return
    by_frame = {}
    outlines = {}
    for track in eligible:
        outlines[track["id"]] = []
        for sample in track["samples"]:
            by_frame.setdefault(sample["frame"], []).append((track, sample))
    last_frame = max(by_frame)
    with av.open(str(source)) as movie:
        for i, frame in enumerate(movie.decode(video=0)):
            if i > last_frame:
                break
            if i in by_frame:
                image = frame.to_ndarray(format="bgr24")
                for track, sample in by_frame[i]:
                    outlines[track["id"]].append(shaded_outline(image, sample))
    for track in eligible:
        polygons = outlines[track["id"]]
        geometry = solid(polygons, track["samples"])
        if geometry is None:
            continue
        vertices, faces = geometry
        hand_depth_candidates(track["samples"], polygons, observations)
        track["rigid_solid"] = dict(
            vertices=vertices.round(6).tolist(),
            faces=faces,
            geometry_basis="median broad visible outlines; closed cross sections with inferred depth",
        )
        for sample, polygon in zip(track["samples"], polygons):
            sample["solid_outline_uv"] = polygon.astype(int).tolist()
