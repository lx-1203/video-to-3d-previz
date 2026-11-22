"""Extend an edge-truncated, distant body track from repeated close-crop source evidence."""

from collections import defaultdict

import av
import cv2
import numpy as np

if __package__:
    from .fullclip_people_chroma import feature
else:
    from fullclip_people_chroma import feature


def _center(b):
    return (b[:2] + b[2:]) * 0.5


def _crop(im, b, margin):
    h, w = im.shape[:2]
    wh = np.maximum(10, b[2:] - b[:2])
    lo = np.maximum([0, 0], np.floor(b[:2] - margin * wh)).astype(int)
    hi = np.minimum([w, h], np.ceil(b[2:] + margin * wh)).astype(int)
    return im[lo[1] : hi[1], lo[0] : hi[0]], lo


def _clothing(im, xy):
    from scipy.ndimage import gaussian_filter

    colors = im[xy[:, 1], xy[:, 0]].reshape(-1, 1, 3)
    bins = feature(colors)
    counts = np.bincount(bins.ravel(), minlength=24 * 24 * 12).reshape(24, 24, 12)
    hist = gaussian_filter(counts.astype(float), (0.72, 0.72, 0.85)).ravel()
    return hist / max(1, hist.sum())


def _foreground(im, prior, clothing=None):
    """Estimate an editable visible silhouette from colour contrast against the local ring."""
    roi, offset = _crop(im, prior, 0.5)
    if min(roi.shape[:2]) < 12:
        return None
    b = prior - np.tile(offset, 2)
    h, w = roi.shape[:2]
    wh = np.maximum(b[2:] - b[:2], 4)
    rect = (np.indices((h, w))[::-1].transpose(1, 2, 0) - b[:2]) / wh
    # Upper half of an independently identified body typically contains clothing.
    pos = (
        (rect[:, :, 0] > 0.20)
        & (rect[:, :, 0] < 0.80)
        & (rect[:, :, 1] > 0.12)
        & (rect[:, :, 1] < 0.53)
    )
    ring = (rect[:, :, 0] < -0.06) | (rect[:, :, 0] > 1.06) | (rect[:, :, 1] < -0.08)
    if pos.sum() < 35 or ring.sum() < 40:
        return None
    # A fitted colour component must be common inside the observation and
    # discriminative relative to its surround. This also admits lighter clothing.
    code = feature(roi)
    from scipy.ndimage import gaussian_filter

    def histogram(mask):
        hist = np.bincount(code[mask], minlength=24 * 24 * 12).reshape(24, 24, 12)
        return gaussian_filter(hist.astype(float) / mask.sum(), (0.55, 0.55, 0.7)).ravel()

    fg, bg = histogram(pos), histogram(ring)
    likelihood = (fg - 1.3 * bg) / (fg + bg + 0.0004)
    prob = likelihood[code]
    mask = prob > 0.24
    if clothing is not None:
        mask &= clothing[code] > 0.012 * clothing.max()
    mask = mask.astype(np.uint8)
    kernel = max(3, round(min(wh) * 0.035)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for k in range(1, n):
        x, y, bw, bh, area = stats[k]
        in_body = (labels == k) & pos
        if (
            in_body.sum() < 0.18 * pos.sum()
            or area < 0.12 * np.prod(wh)
            or bw < 0.35 * wh[0]
            or bh < 0.32 * wh[1]
        ):
            continue
        if area > 1.22 * np.prod(wh) or area / (bw * bh) < 0.20:
            continue
        if np.mean(prob[labels == k]) < 0.25:
            continue
        score = in_body.sum() / pos.sum() + 0.25 * np.mean(prob[labels == k])
        if best is None or score > best[0]:
            ys, xs = np.nonzero(labels == k)
            # Trim isolated attached shadows while keeping most of the clothes.
            lo = np.array([np.quantile(xs, 0.012), np.quantile(ys, 0.012)]) + offset
            hi = np.array([np.quantile(xs, 0.988), np.quantile(ys, 0.988)]) + offset
            contour = np.column_stack((xs, ys)) + offset
            best = score, np.r_[lo, hi], contour
    return best[1:] if best else None


def _outline(contour):
    lo, hi = np.percentile(contour, [4, 96], axis=0)
    wh = np.maximum(5.0, hi - lo)
    coords = (contour - lo) / wh
    signature = []
    for a, b in ((0, 0.22), (0.22, 0.45), (0.45, 0.7), (0.7, 1.0)):
        strip = coords[(coords[:, 1] >= a) & (coords[:, 1] < b), 0]
        if len(strip) < 3:
            signature.extend([0.5, 0.0])
        else:
            x0, x1 = np.percentile(strip, [7, 93])
            signature.extend([0.5 * (x0 + x1), x1 - x0])
    return dict(
        visible_bbox=np.r_[lo, hi].round(2).tolist(),
        visible_centroid_uv=np.median(contour, axis=0).round(2).tolist(),
        outline_profile=np.round(signature, 3).tolist(),
    )


def _parts(rgb, box, pose):
    js = pose.measure(rgb, box)
    vals = {k: p["score"] for k, p in js.items()}
    return js, sum(sorted(vals.values(), reverse=True)[:7]) / 7


def _detect(im, box, det):
    h, w = im.shape[:2]
    # Include neighbouring furniture and the image boundary: an extremely tight
    # crop of a bent back can be classified as a head with no person context.
    side = int(min(h, w, max(0.40 * min(w, h), 3.25 * max(box[2:] - box[:2]))))
    lo = np.clip(np.rint(_center(box) - side / 2).astype(int), [0, 0], [w - side, h - side])
    roi = im[lo[1] : lo[1] + side, lo[0] : lo[0] + side]
    if min(roi.shape[:2]) < 18:
        return []
    objects = det.detect(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB), size=512)
    candidates = []
    extent = np.maximum(12.0, box[2:] - box[:2])
    for o in objects:
        xy = np.array(o["bbox"]) + np.tile(lo, 2)
        c = _center(xy)
        if np.linalg.norm((c - _center(box)) / extent) > 1.6:
            continue
        if not 0.18 < np.sqrt(np.prod(xy[2:] - xy[:2]) / np.prod(extent)) < 3.40:
            continue
        candidates.append((o["class_id"], o["score"], xy))
    return candidates


def _support(im, expected, det, pose, clothing):
    raw = _detect(im, expected, det)
    rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    persons = [(score, b) for label, score, b in raw if label == 0 and score >= 0.27]
    heads = [(score, b) for label, score, b in raw if label == 1 and score >= 0.27]
    choice = None
    extent = np.maximum(10.0, expected[2:] - expected[:2])
    for ps, person in persons:
        # The local head class can cover much of a deeply bent person. Use its
        # centre to corroborate a body, without labelling its box an anatomical head.
        heads_near = [
            (s, b)
            for s, b in heads
            if np.all(_center(b) >= person[:2] - 0.12 * extent)
            and np.all(_center(b) <= person[2:] + 0.12 * extent)
        ]
        if ps < 0.48 and not heads_near:
            continue
        if (
            not heads_near
            and np.linalg.norm((_center(person) - _center(expected)) / extent) > 0.93
        ):
            continue
        for prior in [expected, person, *[b for s, b in heads_near]]:
            segment = _foreground(im, prior, clothing)
            if segment is None:
                continue
            box, contour = segment
            delta = np.linalg.norm((_center(box) - _center(expected)) / extent)
            if delta > 0.7:
                continue
            wh = box[2:] - box[:2]
            if np.any(wh / extent > 1.38) or np.any(wh / extent < 0.48):
                continue
            sim = np.sqrt(_clothing(im, contour) * clothing).sum()
            if sim < 0.67:
                continue
            if heads_near and not any(
                np.all(_center(hb) >= box[:2] - 0.12 * wh)
                and np.all(_center(hb) <= box[2:] + 0.12 * wh)
                for _, hb in heads_near
            ):
                continue
            # RTMPose expects a full-body crop; measure with a narrow margin.
            padded = np.r_[box[:2] - 0.10 * wh, box[2:] + 0.10 * wh]
            js, score = _parts(rgb, padded, pose)
            if score < (0.16 if heads_near and ps >= 0.40 else 0.28) or (
                not heads_near and score < 0.41
            ):
                continue
            rank = (
                score
                + 0.23 * ps
                + 0.10 * bool(heads_near)
                - 0.22 * delta
                + 0.7 * sim
                - 0.18 * np.linalg.norm(np.log(wh / extent))
            )
            if choice is None or rank > choice[0]:
                choice = rank, box, contour, js, ps, score, "local_person_and_contour"
    if choice is None:
        for hs, hb in heads:
            if hs < 0.37:
                continue
            center = _center(hb)
            shift = center - np.clip(
                center, expected[:2] + 0.2 * extent, expected[2:] - 0.2 * extent
            )
            if np.linalg.norm(shift / extent) > 0.8:
                continue
            for candidate in (expected + np.tile(shift, 2), hb):
                seg = _foreground(im, candidate, clothing)
                if seg is None:
                    continue
                box, contour = seg
                wh = box[2:] - box[:2]
                if np.any(wh / extent < 0.33) or np.any(wh / extent > 1.4):
                    continue
                if not (
                    np.all(center >= box[:2] - 0.20 * wh) and np.all(center <= box[2:] + 0.20 * wh)
                ):
                    continue
                sim = np.sqrt(_clothing(im, contour) * clothing).sum()
                if sim < 0.74:
                    continue
                js, score = _parts(rgb, np.r_[box[:2] - 0.08 * wh, box[2:] + 0.08 * wh], pose)
                if score < 0.20:
                    continue
                rank = score + 0.35 * hs + 0.7 * sim
                if choice is None or rank > choice[0]:
                    choice = (
                        rank,
                        box,
                        contour,
                        js,
                        hs,
                        score,
                        "local_head_and_tracked_clothing",
                    )
    return choice


def _flow(before, after, box):
    a, off = _crop(before, box, 0.40)
    b, _ = _crop(after, box, 0.40)
    if min(a.shape[:2]) < 12:
        return None
    gray = [cv2.cvtColor(v, cv2.COLOR_BGR2GRAY) for v in (a, b)]
    mask = np.zeros(gray[0].shape, np.uint8)
    lo = np.maximum(0, np.rint(box[:2] - off).astype(int))
    hi = np.maximum(0, np.rint(box[2:] - off).astype(int))
    mask[lo[1] : hi[1] + 1, lo[0] : hi[0] + 1] = 255
    pts = cv2.goodFeaturesToTrack(gray[0], 55, 0.005, 3, mask=mask)
    if pts is None or len(pts) < 5:
        return None
    q, ok, _ = cv2.calcOpticalFlowPyrLK(gray[0], gray[1], pts, None)
    if q is None:
        return None
    rev, ok2, _ = cv2.calcOpticalFlowPyrLK(gray[1], gray[0], q, None)
    if rev is None:
        return None
    valid = (ok[:, 0] > 0) & (ok2[:, 0] > 0) & (np.linalg.norm((rev - pts)[:, 0], axis=1) < 1.5)
    if np.sum(valid) < 5:
        return None
    p = pts[valid, 0]
    c = q[valid, 0]
    M, inside = cv2.estimateAffinePartial2D(p, c, method=cv2.RANSAC, ransacReprojThreshold=1.8)
    if M is None or inside.sum() < 5:
        return None
    s = np.linalg.norm(M[:, 0])
    if not 0.93 < s < 1.08:
        return None
    midpoint = M[:, :2] @ (_center(box) - off) + M[:, 2] + off
    wh = (box[2:] - box[:2]) * s
    return np.r_[midpoint - wh / 2, midpoint + wh / 2]


def _available(row, box, track_id):
    center = _center(box)
    area = max(1.0, np.prod(box[2:] - box[:2]))
    for a in row["actors"]:
        if a["track_id"] == track_id:
            return False
        other = np.array(a["bbox"])
        shared = np.maximum(0, np.minimum(box[2:], other[2:]) - np.maximum(box[:2], other[:2]))
        if (
            np.prod(shared) / area > 0.60
            and np.all(center >= other[:2])
            and np.all(center <= other[2:])
        ):
            return False
    return True


def _march(images, rows, start, stop, seed, det, pose):
    step = 1 if stop > start else -1
    track = seed["track_id"]
    b = np.array(seed["bbox"], float)
    seed_mask = _foreground(images[start], b)
    if seed_mask is None:
        return []
    b, xy = seed_mask
    original_clothing = _clothing(images[start], xy)
    clothing = original_clothing.copy()
    staged = []
    last_confirm = start
    direct = 0
    for i in range(start + step, stop + step, step):
        if rows[i]["shot_id"] != rows[start]["shot_id"]:
            break
        predicted = _flow(images[i - step], images[i], b)
        if predicted is None:
            break
        if not _available(rows[i], predicted, track):
            break
        measured = _support(images[i], predicted, det, pose, clothing)
        if measured is not None:
            _, box, contour, joints, prob, score, state = measured
            last_confirm = i
            direct += 1
            b = 0.76 * box + 0.24 * predicted
            learned = _clothing(images[i], contour)
            if np.sqrt(original_clothing * learned).sum() > 0.58:
                clothing = 0.88 * clothing + 0.10 * learned + 0.02 * original_clothing
        else:
            if abs(i - last_confirm) > 9:
                break
            visible = _foreground(images[i], predicted, clothing)
            if visible is None:
                break
            box, contour = visible
            size = np.maximum(10, predicted[2:] - predicted[:2])
            if np.linalg.norm((_center(box) - _center(predicted)) / size) > 0.25:
                break
            if (
                np.any((box[2:] - box[:2]) / size > 1.32)
                or np.sqrt(_clothing(images[i], contour) * clothing).sum() < 0.70
            ):
                break
            b = 0.45 * box + 0.55 * predicted
            joints, score = _parts(cv2.cvtColor(images[i], cv2.COLOR_BGR2RGB), b, pose)
            if score < 0.16 and abs(i - last_confirm) > 3:
                break
            prob = None
            state = "source_flow_and_contour"
        lo = np.percentile(contour, 4, axis=0)
        hi = np.percentile(contour, 96, axis=0)
        if min(hi - lo) < 7:
            break
        staged.append(
            (
                i,
                dict(
                    track_id=track,
                    body_id=None,
                    bbox=b.round(2).tolist(),
                    bbox_kind="source_supported_partial_body",
                    detector_score=prob,
                    joints2d=joints,
                    partial_body_track=dict(
                        state=state,
                        **_outline(contour),
                        pose_support=round(score, 4),
                        seed_frame=start,
                        association="shot_local_source_extension",
                    ),
                ),
            )
        )
    if direct < 3:
        return []
    return [(i, a) for i, a in staged if (i - last_confirm) * step <= 0]


def _extend_edge(images, rows, start, end, actor, det, pose):
    direction = 1 if end > start else -1
    records = dict(_march(images, rows, start, end, actor, det, pose))
    while records:
        edge = max(records) if direction > 0 else min(records)
        if edge == end:
            break
        # Re-localize on a recently confirmed person: a fixed colour template
        # can become obsolete as the actor turns from back toward a side profile.
        seeds = [
            i
            for i, a in records.items()
            if 2 <= abs(i - edge) <= 12
            and a["partial_body_track"]["state"] == "local_person_and_contour"
        ]
        progressed = False
        for restart in sorted(seeds, key=lambda i: abs(i - edge))[:3]:
            trial = dict(_march(images, rows, restart, end, records[restart], det, pose))
            novel = [i for i in trial if (i - edge) * direction > 0]
            if not novel or edge not in trial:
                continue
            old = np.array(records[edge]["bbox"])
            new = np.array(trial[edge]["bbox"])
            wh = np.maximum(old[2:] - old[:2], 1.0)
            if np.linalg.norm((_center(new) - _center(old)) / wh) > 0.28:
                continue
            for i in novel:
                a = trial[i]
                a["partial_body_track"].update(seed_frame=start, relocalized_at=restart)
                records[i] = a
            progressed = True
            break
        if not progressed:
            break
    return sorted(records.items())


class _Window:
    """Small direction-aware random-access cache with exact source PTS verification."""

    def __init__(self, video, rows, lo, hi, step, width, height):
        self.video = video
        self.rows = rows
        self.lo = lo
        self.hi = hi
        self.step = step
        self.capacity = max(2, min(20, 100 * 1024**2 // max(1, width * height * 3)))
        self.by_pts = {rows[f]["pts"]: f for f in range(lo, hi + 1)}
        self.frames = {}

    def __getitem__(self, index):
        if index not in self.frames:
            begin = max(self.lo, index - self.capacity + 1) if self.step < 0 else index
            end = min(self.hi, begin + self.capacity - 1)
            stream = self.video.streams.video[0]
            self.video.seek(int(self.rows[begin]["pts"]), stream=stream, backward=True)
            self.frames.clear()
            for frame in self.video.decode(stream):
                n = self.by_pts.get(frame.pts)
                if n is None or n < begin:
                    continue
                if n > end:
                    break
                if str(frame.time_base) != self.rows[n]["time_base"]:
                    raise ValueError("Partial-body source time base mismatch")
                self.frames[n] = frame.to_ndarray(format="bgr24")
                if n == end:
                    break
        if index not in self.frames:
            raise ValueError("Cannot map partial-body crop to source PTS")
        return self.frames[index]


def extend_partial_bodies(source, rows, width, height, pose_model, body_head_model):
    if not body_head_model or not pose_model:
        return {}
    if __package__:
        from .fullclip_people_heads import HeadDetector
        from .fullclip_people_observe import BodyPose
    else:
        from fullclip_people_heads import HeadDetector
        from fullclip_people_observe import BodyPose
    groups = defaultdict(list)
    shots = defaultdict(list)
    for row in rows:
        i = row["source_frame"]
        shots[row["shot_id"]].append(i)
        for a in row["actors"]:
            groups[row["shot_id"], a["track_id"]].append((i, a))
    work = defaultdict(list)
    for (shot, track), seq in groups.items():
        measured = [(i, a) for i, a in seq if a["bbox_kind"] == "person_detection"]
        if len(measured) < 5:
            continue
        for (start, actor), end in (
            (seq[0], shots[shot][0]),
            (seq[-1], shots[shot][-1]),
        ):
            if abs(end - start) < 3 or actor["bbox_kind"] != "person_detection":
                continue
            b = np.array(actor["bbox"])
            wh = b[2:] - b[:2]
            if height - b[3] > max(3, 0.028 * wh[1]) or wh[1] > 0.32 * height:
                continue
            others = [
                v["bbox"][3] - v["bbox"][1]
                for v in rows[start]["actors"]
                if v["track_id"] != track and v["bbox_kind"] == "person_detection"
            ]
            if len(others) < 2 or wh[1] > 0.68 * np.median(others):
                continue
            work[shot].append((start, end, actor))
    if not work:
        return {}
    det = HeadDetector(body_head_model)
    pose = BodyPose(pose_model)
    report = {}
    with av.open(str(source)) as video:
        for shot, edges in work.items():
            for start, end, actor in edges:
                frames = _Window(
                    video,
                    rows,
                    shots[shot][0],
                    shots[shot][-1],
                    1 if end > start else -1,
                    width,
                    height,
                )
                additions = _extend_edge(frames, rows, start, end, actor, det, pose)
                for f, a in additions:
                    rows[f]["actors"].append(a)
                if not additions:
                    continue
                tid = actor["track_id"]
                report.setdefault(str(tid), []).extend(f for f, a in additions)
                for f, detected in groups[shot, tid]:
                    if detected.get("partial_body_track"):
                        continue
                    seg = _foreground(frames[f], np.asarray(detected["bbox"]))
                    if seg is None:
                        continue
                    _, xy = seg
                    detected["partial_body_track"] = dict(
                        state="detected_seed",
                        seed_frame=f,
                        **_outline(xy),
                        association="shot_local_source_extension",
                    )
    return report
