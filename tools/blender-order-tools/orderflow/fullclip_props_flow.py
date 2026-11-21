"""Extend a motion-confirmed visible prop across partial occlusion using source pixels."""

import numpy as np
import cv2


def recover(prev, cur, last, seed, hands, faces):
    polygon = np.asarray(last["polygon_uv"], np.float32)
    axis0 = np.asarray(seed["axes_px"])
    length = axis0[0]
    center = np.asarray(last["center_uv"])
    near = [h for h in hands if np.linalg.norm(center - h["palm"]) < 1.5 * h["radius"]]
    if not near:
        return
    h, w = cur.shape[:2]
    margin = max(19.0, length * 0.8)
    x, y = np.maximum(0, np.floor(polygon.min(axis=0) - margin)).astype(int)
    xx, yy = np.minimum([w, h], np.ceil(polygon.max(axis=0) + margin)).astype(int)
    a, b = prev[y:yy, x:xx], cur[y:yy, x:xx]
    ga, gb = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) for img in (a, b))
    old = np.zeros(ga.shape, np.uint8)
    cv2.fillPoly(old, [(polygon - [x, y]).astype(np.int32)], 255)
    features = cv2.goodFeaturesToTrack(ga, 40, 0.005, 2, mask=old)
    if features is None or len(features) < 4:
        return
    opts = dict(winSize=(15, 15), maxLevel=2)
    moved, ok, _ = cv2.calcOpticalFlowPyrLK(ga, gb, features, None, **opts)
    back, okback, _ = cv2.calcOpticalFlowPyrLK(gb, ga, moved, None, **opts)
    good = (
        ok[:, 0].astype(bool)
        & okback[:, 0].astype(bool)
        & (np.linalg.norm(features[:, 0] - back[:, 0], axis=1) < 1.3)
    )
    if good.sum() < 4 or good.mean() < 0.48:
        return
    flow = (moved - features)[good, 0, :]
    offset = np.median(flow, axis=0)
    consistent = np.linalg.norm(flow - offset, axis=1) < max(2.2, 0.06 * length)
    if consistent.mean() < 0.60 or np.linalg.norm(offset) > 0.37 * length:
        return
    warped = np.zeros_like(old)
    predicted = polygon - [x, y] + offset
    affine, inliers = cv2.estimateAffinePartial2D(
        features[good, 0, :], moved[good, 0, :], method=cv2.RANSAC, ransacReprojThreshold=1.7
    )
    if affine is not None and inliers.mean() >= 0.60:
        stretch = np.linalg.norm(affine[0, :2])
        turn = abs(np.arctan2(affine[1, 0], affine[0, 0]))
        if 0.72 < stretch < 1.36 and turn < 0.45:
            predicted = (polygon - [x, y]) @ affine[:, :2].T + affine[:, 2]
    cv2.fillPoly(warped, [np.rint(predicted).astype(np.int32)], 255)
    pad = int(max(2, min(6, length * 0.07)))
    zone = cv2.dilate(warped, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8)) > 0
    hsv = cv2.cvtColor(b, cv2.COLOR_BGR2HSV)
    skin = ((hsv[:, :, 0] < 23) | (hsv[:, :, 0] > 171)) & (hsv[:, :, 1] >= 37)
    rgb = np.uint8([[seed["rgb"][::-1]]])
    ref = cv2.cvtColor(rgb, cv2.COLOR_BGR2LAB)[0, 0].astype(float)
    lab = cv2.cvtColor(b, cv2.COLOR_BGR2LAB).astype(float)
    chroma = np.linalg.norm(lab[:, :, 1:] - ref[1:], axis=2)
    # A turning surface can darken during a handover. Retain the seed chroma and bound the permissible lightness change from the most recent visible patch.
    recent = cv2.cvtColor(np.uint8([[last["rgb"][::-1]]]), cv2.COLOR_BGR2LAB)[0, 0].astype(float)
    low_l = max(44, ref[0] - 105, min(ref[0] - 39, recent[0] - 40))
    allowed = zone & ~skin & (chroma < 18) & (lab[:, :, 0] > low_l) & (lab[:, :, 0] < ref[0] + 58)
    mask = allowed.astype(np.uint8) * 255
    for fx, fy, fxx, fyy in faces:
        cv2.rectangle(mask, (int(fx - x), int(fy - y)), (int(fxx - x), int(fyy - y)), 0, -1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        area = cv2.contourArea(c)
        if area < max(12, 0.066 * axis0.prod()):
            continue
        cm = np.zeros_like(mask)
        cv2.drawContours(cm, [c], -1, 255, -1)
        common = np.count_nonzero((cm > 0) & (warped > 0))
        if (
            common / max(1, np.count_nonzero(cm)) < 0.52
            or common / np.count_nonzero(warped) < 0.28
        ):
            continue
        if best is None or common > best[0]:
            best = (common, c, cm)
    if best is None:
        return
    _, contour, cm = best
    (cx, cy), (ax, ay), ang = cv2.minAreaRect(contour)
    long, short = max(ax, ay), min(ax, ay)
    if short < 2.8 or not 0.40 * length < long < 1.4 * length:
        return
    cnt = contour + np.asarray([[[x, y]]], np.int32)
    grips = []
    for hand in near:
        dist = -cv2.pointPolygonTest(cnt, tuple(map(float, hand["palm"])), True)
        if dist < 0.30 * hand["radius"]:
            grips.append(
                (dist / hand["radius"], dict(track_id=hand["track_id"], side=hand["side"]))
            )
    if not grips:
        return
    poly = cv2.approxPolyDP(cnt, max(1.0, 0.012 * long), True).reshape(-1, 2)
    if len(poly) < 3:
        return
    return dict(
        center_uv=[round(cx + x, 2), round(cy + y, 2)],
        axes_px=[round(long, 2), round(short, 2)],
        angle_deg=round((ang + (90 if ax < ay else 0)) % 180, 2),
        polygon_uv=poly.tolist(),
        contacts=[c for _, c in sorted(grips, key=lambda q: q[0])],
        rgb=np.median(b[cm > 0], axis=0)[::-1].astype(int).tolist(),
        evidence="source_contour_and_forward_backward_optical_flow",
    )
