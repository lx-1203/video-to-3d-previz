# -*- coding: utf-8 -*-
"""Lightweight appearance-aware tracker (IoU + HSV histogram + Hungarian).

Written to stop an occluded track from latching onto a different person who
happens to be standing where the occluded one used to be.
"""
import numpy as np

try:
    import cv2
except ImportError:  # offline simulation may not need image features
    cv2 = None
from scipy.optimize import linear_sum_assignment


def color_hist(image, box, upper=0.55):
    """HSV histogram over the upper part of the box (head / shoulders / chest).

    The upper band carries the identity cues that survive here: the white scarf,
    the pink jacket, hair colour.  A full-box histogram washes them out because
    everyone is wearing a dark suit below the chest.
    """
    if cv2 is None or image is None:
        return None
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    h, w = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 6 or y2 - y1 < 12:
        return None
    y2 = y1 + max(12, int((y2 - y1) * upper))
    crop = image[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [24, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def hist_distance(a, b):
    if a is None or b is None:
        return 0.5
    return float(cv2.compareHist(a.astype(np.float32), b.astype(np.float32),
                                 cv2.HISTCMP_BHATTACHARYYA))


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Track:
    __slots__ = ("id", "box", "score", "hist", "hits", "time_since_update",
                 "velocity", "last_box")

    def __init__(self, tid, box, score, hist):
        self.id = tid
        self.box = [float(v) for v in box]
        self.last_box = list(self.box)
        self.score = float(score)
        self.hist = hist
        self.hits = 1
        self.time_since_update = 0
        self.velocity = [0.0, 0.0, 0.0, 0.0]


class AppearanceTracker:
    """One-to-one association with an appearance gate against ID hijacking."""

    def __init__(self, max_age=90, geometry_iou=0.30, max_hist=0.40,
                 max_center_drift=1.4, iou_weight=0.55):
        self.max_age = max_age
        self.geometry_iou = geometry_iou
        self.max_hist = max_hist
        self.max_center_drift = max_center_drift
        self.iou_weight = iou_weight
        self.tracks = []
        self.next_id = 0
        self.debug = []

    # -- geometry helpers -------------------------------------------
    @staticmethod
    def _predict(track):
        """Extrapolate from the last observed box, never from a predicted one."""
        if track.time_since_update <= 0:
            return list(track.last_box)
        k = float(track.time_since_update)
        v, b = track.velocity, track.last_box
        return [b[0] + v[0] * k, b[1] + v[1] * k,
                b[2] + v[2] * k, b[3] + v[3] * k]

    @staticmethod
    def _center(box):
        return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)

    # -- main step ---------------------------------------------------
    def update(self, boxes, scores, image=None):
        """Associate detections to tracks; returns [(box, id, score)].

        Two stages: geometry decides while overlap exists, appearance is only
        allowed to reconnect a track that has already lost its subject.
        """
        det_boxes = [[float(v) for v in b] for b in boxes]
        det_scores = [float(s) for s in scores]
        det_hists = [color_hist(image, b) for b in det_boxes]

        for t in self.tracks:
            t.time_since_update += 1
            t.box = self._predict(t)

        n_det, n_trk = len(det_boxes), len(self.tracks)
        matched_det = [False] * n_det
        matched_trk = [False] * n_trk

        if n_det and n_trk:
            big = 1e6
            cost = np.full((n_det, n_trk), big, dtype=np.float64)
            for i, db in enumerate(det_boxes):
                dcx, dcy = self._center(db)
                dh = max(1.0, db[3] - db[1])
                for j, t in enumerate(self.tracks):
                    ov = iou(self._predict(t), db)
                    cd = hist_distance(det_hists[i], t.hist)
                    tcx, tcy = self._center(t.box)
                    drift = (((dcx - tcx) ** 2 + (dcy - tcy) ** 2) ** 0.5) / dh
                    if ov >= self.geometry_iou:
                        # subject still overlapping where we expect it
                        cost[i, j] = (1.0 - ov) + 0.05 * cd
                    elif cd <= self.max_hist and drift <= self.max_center_drift:
                        # re-identify after an occlusion, appearance-led
                        cost[i, j] = 0.5 + cd
                    elif t.time_since_update > 3:
                        self.debug.append({'det': i, 'track': t.id, 'iou': round(ov, 3),
                                           'hist': round(cd, 3),
                                           'drift': round(drift, 2),
                                           'reason': 'hist' if cd > self.max_hist else 'drift'})

            rows, cols = linear_sum_assignment(cost)
            for i, j in zip(rows, cols):
                if cost[i, j] >= 1e5:
                    continue
                self._apply(self.tracks[j], det_boxes[i], det_scores[i],
                            det_hists[i])
                matched_det[i] = True
                matched_trk[j] = True

        for i, db in enumerate(det_boxes):
            if matched_det[i]:
                continue
            self.tracks.append(Track(self.next_id, db, det_scores[i], det_hists[i]))
            self.next_id += 1
            matched_det[i] = True

        self.tracks = [t for t in self.tracks if t.time_since_update <= self.max_age]

        observed = []
        for t in self.tracks:
            if t.time_since_update == 0:
                observed.append((list(t.box), t.id, t.score))
        return observed

    def _apply(self, track, box, score, hist):
        prev = track.last_box
        gap = track.time_since_update
        if gap > 0:
            # keep the last known velocity across a gap: a walking subject keeps
            # walking, a standing one keeps standing, so extrapolation separates
            # them instead of collapsing both onto the same spot
            pass
        else:
            track.velocity = [box[k] - prev[k] for k in range(4)]
        track.box = [float(v) for v in box]
        track.last_box = list(track.box)
        track.score = float(score)
        track.time_since_update = 0
        track.hits += 1
        if hist is not None:
            if track.hist is None:
                track.hist = hist
            else:
                track.hist = 0.8 * track.hist + 0.2 * hist
