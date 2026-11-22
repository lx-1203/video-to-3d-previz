"""Track directly witnessed garment patches into neighbouring low-contrast frames."""

import cv2
import numpy as np


def local_frame(actor):
    s = actor["anchors"].get("shoulder", {})
    h = actor["anchors"].get("hip", {})
    joints = actor["joints2d"]
    if min(s.get("score", 0), h.get("score", 0)) < 0.46:
        return
    pair = [joints.get(k, {}) for k in ("left_shoulder", "right_shoulder")]
    if not all(j.get("source") == "rtmpose" and j.get("score", 0) > 0.46 for j in pair):
        return
    span = abs(pair[0]["u"] - pair[1]["u"])
    if span < 12:
        return
    a = np.array([s["u"], s["v"]])
    axis = np.array([h["u"], h["v"]]) - a
    if axis[1] < 16:
        return
    return a, axis, span


def background(gray, next_gray, actors):
    h, w = gray.shape
    m = np.full((h, w), 255, np.uint8)
    for a in actors:
        x, y, x2, y2 = np.rint(a["bbox"]).astype(int)
        left, right = np.clip([x - 4, x2 + 5], 0, w)
        top, bottom = np.clip([y - 4, y2 + 5], 0, h)
        m[top:bottom, left:right] = 0
    p = cv2.goodFeaturesToTrack(gray, 250, 0.004, 7, mask=m)
    if p is None or len(p) < 12:
        return None
    q, o, _ = cv2.calcOpticalFlowPyrLK(gray, next_gray, p, None, winSize=(19, 19), maxLevel=3)
    ok = o[:, 0] > 0
    if ok.sum() < 12:
        return None
    H, ok = cv2.findHomography(p[ok], q[ok], cv2.RANSAC, 2.0)
    return H if H is not None and ok.sum() >= 12 else None


def edge_support(gray, pt, span):
    """Reject an uninterrupted foreground line across the centre and its flank."""
    x, y = pt
    xs = np.arange(round(x - 1.05 * span), round(x + 1.05 * span), 2)
    xs = xs[(xs > 1) & (xs < gray.shape[1] - 2)]
    if len(xs) < 8:
        return False, False
    core = abs(xs - x) < 0.25 * span
    wing = abs(xs - x) > 0.68 * span
    if not core.any() or wing.sum() < 3:
        return False, False
    interrupted = False
    for m in np.linspace(-0.28, 0.28, 7):
        yy = np.rint(y + (xs - x) * m).astype(int)
        if yy.min() < 4 or yy.max() > gray.shape[0] - 5:
            continue
        # A bright/dark finite rail can have identical outer flanks at its
        # centre. Check both edges against the centre before following a coat.
        mid = gray[yy, xs].astype(float)
        for radius in (3, 6):
            if yy.min() < radius or yy.max() + radius >= gray.shape[0]:
                continue
            outer_a = gray[yy - radius, xs].astype(float)
            outer_b = gray[yy + radius, xs].astype(float)
            band = (np.minimum(abs(mid - outer_a), abs(mid - outer_b)) > 12) & (
                (mid - outer_a) * (mid - outer_b) > 0
            )
            if band[core].mean() > 0.65 and band[wing].mean() > 0.38:
                return True, False
        g = gray[yy + 3, xs].astype(float) - gray[yy - 3, xs].astype(float)
        for sign in (-1, 1):
            active = sign * g > 10
            if active[wing].mean() > 0.36 and active[core].mean() > 0.62:
                return True, False
            if active[wing].mean() > 0.55 and active[core].mean() < 0.16:
                interrupted = True
    return False, interrupted


def motion_witnesses(grays, actors, direct):
    """All arguments are keyed by absolute source frame, within a single shot."""
    out = {}
    diag = {}
    keys = sorted(grays)
    cv2.setNumThreads(2)
    contrast = cv2.createCLAHE(2.0, (12, 8))
    bright = {i: contrast.apply(grays[i]) for i in keys}
    bg = {i: background(grays[i], grays[i + 1], actors[i]) for i in keys[:-1] if i + 1 in grays}
    geometry = {
        i: {a["track_id"]: g for a in actors[i] if (g := local_frame(a)) is not None} for i in keys
    }
    seeds = {}
    for i in keys:
        samples = {}
        for tid, pts in direct.get(str(i), {}).items():
            geom = geometry[i].get(tid)
            if geom is None:
                continue
            a, v, w = geom
            for u, y, t in pts:
                if t < 0:
                    continue
                dx = (u - (a + t * v)[0]) / w
                tag = (tid, round(dx, 1), t)
                samples[tag] = np.array([u, y], np.float32)
        seeds[i] = samples
    for sequence in [keys, keys[::-1]]:
        carry = {}
        for step, i in enumerate(sequence):
            if step:
                prev = sequence[step - 1]
                if abs(i - prev) != 1:
                    carry = {}
                H = bg.get(min(i, prev))
                if H is not None and i < prev:
                    H = np.linalg.inv(H)
                tags = list(carry)
                if tags and H is not None:
                    pts = np.array([carry[k][0] for k in tags], np.float32).reshape(-1, 1, 2)
                    # Use the patch centre and four nearby samples, accepting only
                    # consistent forward/backward displacements on the garment.
                    offsets = np.array([[0, 0], [-3, 0], [3, 0], [0, 3], [0, -3]], np.float32)
                    pi = (pts + offsets).reshape(-1, 1, 2)
                    dest, ok, err = cv2.calcOpticalFlowPyrLK(
                        bright[prev],
                        bright[i],
                        pi,
                        None,
                        winSize=(17, 17),
                        maxLevel=2,
                        minEigThreshold=0.000013,
                    )
                    rev, bok, _ = cv2.calcOpticalFlowPyrLK(
                        bright[i],
                        bright[prev],
                        dest,
                        None,
                        winSize=(17, 17),
                        maxLevel=2,
                        minEigThreshold=0.000013,
                    )
                    good = (
                        (ok[:, 0] > 0)
                        & (bok[:, 0] > 0)
                        & (err[:, 0] < 8)
                        & (np.linalg.norm((rev - pi)[:, 0], axis=1) < 0.6)
                    )
                    dd = (dest - pi).reshape(-1, len(offsets), 2)
                    bgpts = cv2.perspectiveTransform(pts, H)[:, 0]
                    new = {}
                    for n, tag in enumerate(tags):
                        tid, dx, t = tag
                        if tag in seeds[i] or tid not in geometry[i]:
                            continue
                        gs = good.reshape(-1, len(offsets))[n]
                        if gs.sum() < 3:
                            continue
                        delta = np.median(dd[n, gs], axis=0)
                        if np.max(np.linalg.norm(dd[n, gs] - delta, axis=1)) > 1.1:
                            continue
                        old, seed, total, seed_gray = carry[tag]
                        pt = old + delta
                        a, v, span = geometry[i][tid]
                        if abs(i - seed) > 17 or np.linalg.norm(
                            pt - (a + t * v + [dx * span, 0])
                        ) > max(5.0, 0.16 * span):
                            continue
                        x, y = np.rint(pt).astype(int)
                        if not (3 < x < grays[i].shape[1] - 4 and 3 < y < grays[i].shape[0] - 4):
                            continue
                        lum = np.median(grays[i][y - 2 : y + 3, x - 2 : x + 3])
                        if abs(float(lum) - seed_gray) > 12:
                            continue
                        total = total + (pt - bgpts[n])
                        crossing, interrupt = edge_support(grays[i], pt, span)
                        if crossing:
                            continue
                        new[tag] = (pt, seed, total, seed_gray)
                        if np.linalg.norm(total) > 1.35 or interrupt:
                            rec = [round(float(pt[0]), 2), round(float(pt[1]), 2), t]
                            prev_rec = out.setdefault(i, {}).setdefault(tid, {})
                            prev_rec[tag] = rec
                            diag.setdefault(i, {}).setdefault(tid, []).append(
                                [
                                    t,
                                    seed,
                                    round(float(np.linalg.norm(total)), 2),
                                    interrupt,
                                ]
                            )
                    carry = new
                else:
                    carry = {}
            for tag, pt in seeds[i].items():
                # Require a second direct observation of the same region. A
                # single unsupported patch never propagates across the shot.
                if not any(tag in seeds.get(k, {}) for k in range(i - 4, i + 5) if k != i):
                    continue
                x, y = np.rint(pt).astype(int)
                if not (3 < x < grays[i].shape[1] - 4 and 3 < y < grays[i].shape[0] - 4):
                    continue
                carry[tag] = (
                    pt,
                    i,
                    np.zeros(2),
                    float(np.median(grays[i][y - 2 : y + 3, x - 2 : x + 3])),
                )
    return {i: {t: list(v.values()) for t, v in row.items()} for i, row in out.items()}, diag
