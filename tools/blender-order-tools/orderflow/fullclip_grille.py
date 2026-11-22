"""Measure fixed intersecting grille members from repeated, signed source contrast."""

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_widths


def _profile(gray, axis):
    strips = gray if axis == 0 else gray.T
    strips = gaussian_filter1d(strips.astype(np.float32), 0.85, axis=1)
    width = strips.shape[1]
    residual = strips - gaussian_filter1d(strips, max(4, width * 0.043), axis=1, mode="reflect")
    energy = gaussian_filter1d(residual**2, max(4, width * 0.09), axis=1)
    signed = residual / np.maximum(2, np.sqrt(energy))
    return np.clip(signed, -2, 2)


def _bands(profile):
    peaks, _ = find_peaks(profile, height=0.48, prominence=0.48, distance=4)
    if not len(peaks):
        return []
    _, _, left, right = peak_widths(profile, peaks, rel_height=0.5)
    return [
        (float(lo), float(hi), float(profile[peak]))
        for peak, lo, hi in zip(peaks, left, right)
        if 2 <= hi - lo <= 0.12 * len(profile)
    ]


def _repeated(bands, width):
    if len(bands) < 3:
        return None
    centers = np.array([(a + b) / 2 for a, b, _ in bands])
    gaps = np.diff(centers)
    best = None
    for i, gap in enumerate(gaps):
        if gap < 0.035 * width or gap > width * 0.45:
            continue
        aligned = abs(gaps - gap) <= 0.17 * gap
        start, end = i, i
        while start and aligned[start - 1]:
            start -= 1
        while end + 1 < len(gaps) and aligned[end + 1]:
            end += 1
        length = end - start + 2
        if length < 3 or centers[end + 1] - centers[start] < width * 0.38:
            continue
        typical = np.median([b - a for a, b, _ in bands[start : end + 2]])
        if typical > 0.44 * gap:
            continue
        if best is None or length > best[0]:
            best = length, start, end + 1, gap
    return best


def _family(observations, axis, frame_ids):
    profiles = [_profile(im, axis) for im in observations]
    means = np.array([p.mean(axis=0) for p in profiles])
    median = np.median(means, axis=0)
    choices = []
    for polarity in (1, -1):
        bands = _bands(polarity * median)
        regular = _repeated(bands, len(median))
        if regular is None:
            continue
        length, start, end, _ = regular
        peers = bands[start : end + 1]
        typical = np.median([hi - lo for lo, hi, _ in peers])
        strength = np.median([value for _, _, value in peers])
        frame_bands = [_bands(profile * polarity) for profile in means]
        found = []
        for index, (lo, hi, value) in enumerate(bands):
            center = (lo + hi) / 2
            at = round(center)
            if (index < start or index > end) and not (
                value >= 0.53 * strength and 0.5 * typical <= hi - lo <= 1.75 * typical
            ):
                continue
            occupied = np.array([np.mean(p[:, at] * polarity > 0.13) for p in profiles])
            confirmed = (means[:, at] * polarity > 0.32) & (occupied > 0.49)
            if confirmed.mean() < 0.65:
                continue
            centers = {}
            for frame, candidates, supported in zip(frame_ids, frame_bands, confirmed):
                if not supported:
                    continue
                matches = [
                    (abs((a + b) / 2 - center), (a + b) / 2)
                    for a, b, _ in candidates
                    if abs((a + b) / 2 - center) < max(3, (hi - lo) * 0.65)
                    and 0.45 * (hi - lo) < b - a < 2 * (hi - lo)
                ]
                if matches:
                    centers[str(frame)] = round(float(min(matches)[1]), 3)
            if len(centers) < max(4, len(frame_ids) // 2):
                continue
            found.append(
                dict(
                    center=float(center),
                    width=float(hi - lo),
                    support=float(np.median(occupied)),
                    contrast=float(value),
                    frames=centers,
                    polarity=polarity,
                )
            )
        if len(found) >= 3 and _repeated(
            [
                (r["center"] - r["width"] / 2, r["center"] + r["width"] / 2, r["contrast"])
                for r in found
            ],
            len(median),
        ):
            choices.append((length * strength / np.sqrt(typical), found))
    return max(choices, key=lambda row: row[0])[1] if choices else []


def measure(frames):
    """Return measured bars only when both independent axes repeat across the image and time."""
    if len(frames) < 5:
        return None
    ordered = sorted(frames)
    ids = np.unique(np.linspace(0, len(ordered) - 1, min(11, len(ordered))).round().astype(int))
    images = [cv2.cvtColor(frames[ordered[i]], cv2.COLOR_BGR2GRAY) for i in ids]
    families = [_family(images, axis, [ordered[i] for i in ids]) for axis in (0, 1)]
    return families if all(families) else None
