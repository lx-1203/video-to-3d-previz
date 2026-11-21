"""Shot-local chroma likelihood for a visible fragment of a previously detected head."""

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter


def feature(image):
    b, g, r = cv2.split(image.astype(np.float32))
    total = np.maximum(5.0, r + g + b)
    # Chromaticity is stable across the exposure changes typical of a cut-in.
    a = np.clip(((r - b) / total + 0.45) * 28, 0, 23).astype(np.int32)
    c = np.clip(((g - b) / total + 0.35) * 32, 0, 23).astype(np.int32)
    light = np.clip(np.sqrt(total / (3 * 255)) * 11, 0, 11).astype(np.int32)
    return (a * 24 + c) * 12 + light


def learn(images, samples, scale):
    pos = np.zeros(24 * 24 * 12)
    neg = np.zeros_like(pos)
    usable = 0
    h, w = images[0].shape[:2]
    yy, xx = np.ogrid[:h, :w]
    for i, a in samples:
        region = a.get("face") or a.get("head_detection")
        if not region or region["score"] < 0.43:
            continue
        b = np.array(region["bbox"]) * scale
        bw, bh = b[2:] - b[:2]
        if min(bw, bh) < 8:
            continue
        cx, cy = (b[:2] + b[2:]) / 2
        d = ((xx - cx) / bw) ** 2 + ((yy - cy) / bh) ** 2
        fg = d < 0.13
        bg = (d > 0.35) & (d < 1.55)
        f = feature(images[i])
        hp = np.bincount(f[fg], minlength=len(pos))
        hn = np.bincount(f[bg], minlength=len(neg))
        if not hp.sum() or not hn.sum():
            continue
        pos += hp / hp.sum()
        neg += hn / hn.sum()
        usable += 1
    if usable < 2:
        return None
    pos = gaussian_filter(pos.reshape(24, 24, 12), (0.72, 0.72, 1.1)).ravel()
    neg = gaussian_filter(neg.reshape(24, 24, 12), (0.72, 0.72, 1.1)).ravel()
    return np.clip((pos - 1.4 * neg) / (pos + neg + 0.0008 * usable), 0, 1)


def localization(images, index, lut, seed, scale):
    if lut is None:
        return None
    # Small likelihood maps preserve chroma evidence from thin visible strips.
    raw = cv2.resize(images[index], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    score = lut[feature(raw)]
    sh, sw = score.shape
    box = np.array(seed) * scale / 2
    bw, bh = np.maximum(4, box[2:] - box[:2])
    x, y = (box[:2] + box[2:]) / 2
    yy, xx = np.mgrid[:sh, :sw]
    # Mean-shift converges on discriminative head colour near the interpolation
    # of two real observations. A low separation from the surround yields unknown.
    for _ in range(6):
        dist = ((xx - x) / (0.60 * bw)) ** 2 + ((yy - y) / (0.60 * bh)) ** 2
        weights = np.where(dist < 3.5, score**2 * np.exp(-dist), 0.0)
        mass = weights.sum()
        if mass < 0.013 * bw * bh:
            return None
        nx, ny = (np.sum(weights * z) / mass for z in (xx, yy))
        if abs(nx - x) + abs(ny - y) < 0.5:
            break
        x, y = nx, ny
    d = ((xx - x) / bw) ** 2 + ((yy - y) / bh) ** 2
    core = d < 0.17
    ring = (d > 0.32) & (d < 0.95)
    if not np.any(ring):
        return None
    contrast = float(np.mean(score[core]) - np.mean(score[ring]))
    if contrast < 0.065:
        return None
    # Near an image boundary the visible colour mass estimates only a fragment.
    # Reconstruct the centre from its inward edge and the observed head diameter.
    chosen = (score > 0.40) & (d < 0.60)
    if np.count_nonzero(chosen) >= 12:
        ys, xs = np.nonzero(chosen)
        for values, extent, limit, is_x in ((xs, bw, sw, True), (ys, bh, sh, False)):
            low, high = np.quantile(values, [0.08, 0.92])
            coord = x if is_x else y
            if high - low < 0.64 * extent:
                if high > limit - 0.08 * extent and coord > limit - 0.54 * extent:
                    coord = max(coord, low + 0.46 * extent)
                if low < 0.08 * extent and coord < 0.54 * extent:
                    coord = min(coord, high - 0.46 * extent)
            if is_x:
                x = coord
            else:
                y = coord
    return np.array([x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2]) / (scale / 2), contrast
