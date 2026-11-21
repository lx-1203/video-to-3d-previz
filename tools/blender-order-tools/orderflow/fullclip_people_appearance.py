"""Conservative colour continuity from shot-local, source-image appearance."""

from collections import defaultdict
import cv2
import numpy as np
import av


def descriptor(rgb, actor):
    head = actor["anchors"]["head"]
    shoulder = actor["anchors"]["shoulder"]
    if head["score"] < 0.4 or shoulder["score"] < 0.4:
        return None
    h, w = rgb.shape[:2]
    p = np.array([head["u"], head["v"]])
    s = np.array([shoulder["u"], shoulder["v"]])
    d = np.linalg.norm(p - s)
    if d < 6:
        return None
    center = s + (s - p) * 0.64
    x, y = np.rint(center).astype(int)
    rad = int(max(3, d * 0.39))
    if not (rad <= x < w - rad and rad <= y < h - rad):
        return None
    patch = rgb[y - rad : y + rad, x - rad : x + rad]
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
    return hist / max(1.0, hist.sum())


def label(video, frames):
    tracks = defaultdict(list)
    shots = defaultdict(list)
    for r in frames:
        shots[r["shot_id"]].append(r)
        for a in r["actors"]:
            tracks[a["track_id"]].append((r["source_frame"], a))
    selected = defaultdict(list)
    for seq in tracks.values():
        for f, a in seq[:: max(1, len(seq) // 12)]:
            selected[f].append(a)
    samples = defaultdict(list)
    with av.open(str(video)) as c:
        for i, fr in enumerate(c.decode(video=0)):
            if i not in selected:
                continue
            rgb = fr.to_ndarray(format="rgb24")
            for a in selected[i]:
                feat = descriptor(rgb, a)
                if feat is not None:
                    samples[a["track_id"]].append(feat)
    descriptors = {k: np.mean(v, axis=0) for k, v in samples.items() if len(v) >= 3}
    previous = []
    serial = 0
    for shot, rows in shots.items():
        # A face occupying the image with no second persistent character can
        # share a neutral swatch without implying cross-shot identity.
        ids = sorted({a["track_id"] for row in rows for a in row["actors"]})
        single = (
            np.mean([sum(a.get("renderable", True) for a in r["actors"]) <= 1 for r in rows])
            >= 0.90
        )
        colors = {}
        for tid in ids:
            desc = descriptors.get(tid)
            options = []
            if desc is not None:
                for prev, key, old in previous:
                    distance = float(np.sqrt(max(0.0, 1 - np.sqrt(desc * old).sum())))
                    options.append((distance, key))
            options.sort()
            unique = (
                options
                and options[0][0] < 0.20
                and (len(options) == 1 or options[1][0] - options[0][0] > 0.12)
            )
            if unique and options[0][1] not in colors:
                key = options[0][1]
                status = "appearance_similarity_unverified"
            elif single:
                key = "neutral-close"
                status = "neutral_identity_unresolved"
            else:
                serial += 1
                key = f"ensemble-{serial:03d}"
                status = "anonymous_shot_local"
            colors[key] = True
            for _, a in tracks[tid]:
                a["color_key"] = key
                a["color_evidence"] = status
            if desc is not None and key != "neutral-close":
                colors[key] = desc
        previous = [(shot, key, d) for key, d in colors.items() if isinstance(d, np.ndarray)]
