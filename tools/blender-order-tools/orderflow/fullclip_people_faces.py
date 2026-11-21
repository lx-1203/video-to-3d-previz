"""Independent local YuNet observations for close, rotated or partially visible faces."""

import argparse
import hashlib
import json
from pathlib import Path

import av
import cv2
import numpy as np


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def undo_rotation(points, turns, width, height):
    x, y = np.asarray(points, dtype=float).T
    if turns == 1:
        return np.column_stack((width - 1 - y, x))
    if turns == 2:
        return np.column_stack((width - 1 - x, height - 1 - y))
    if turns == 3:
        return np.column_stack((y, height - 1 - x))
    return np.column_stack((x, y))


def overlap(a, b):
    w = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - w * h)


def observe(detector, raw):
    height, width = raw.shape[:2]
    scale = min(1.0, 640 / max(width, height))
    img = cv2.resize(raw, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    # A fixed luminance lift exposes shadows while preserving saturated pixels.
    luma = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if np.median(luma) < 62:
        img = cv2.LUT(img, np.rint(255 * (np.arange(256) / 255) ** 0.62).astype("uint8"))
    candidates = []
    for turns in (0, 1, 3, 2):
        view = np.ascontiguousarray(np.rot90(img, turns))
        detector.setInputSize((view.shape[1], view.shape[0]))
        _, faces = detector.detect(view)
        for face in [] if faces is None else faces:
            x, y, fw, fh = face[:4]
            corners = undo_rotation([[x, y], [x + fw, y + fh]], turns, w, h) / scale
            box = [*corners.min(axis=0), *corners.max(axis=0)]
            landmarks = undo_rotation(face[4:14].reshape(5, 2), turns, w, h) / scale
            candidates.append(
                {
                    "bbox": list(map(float, box)),
                    "landmarks": landmarks.tolist(),
                    "score": float(face[-1]),
                    "turns": turns,
                }
            )
    selected = []
    for face in sorted(candidates, key=lambda f: -f["score"]):
        if all(overlap(face["bbox"], other["bbox"]) < 0.35 for other in selected):
            selected.append(face)
    return selected


def scan(video, model, output):
    cv2.setNumThreads(2)
    detector = cv2.FaceDetectorYN.create(str(model), "", (640, 360), 0.65, 0.3, 200)
    frames = []
    with av.open(str(video)) as container:
        for i, frame in enumerate(container.decode(video=0)):
            faces = observe(detector, frame.to_ndarray(format="bgr24"))
            frames.append(
                {
                    "source_frame": i,
                    "pts": frame.pts,
                    "time_base": str(frame.time_base),
                    "faces": faces,
                }
            )
    data = {
        "schema": "orderflow.fullclip_faces.v1",
        "source_sha256": digest(video),
        "model_sha256": digest(model),
        "frames": frames,
    }
    Path(output).write_text(
        json.dumps(data, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    return data


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ("video", "model", "output"):
        p.add_argument("--" + name, required=True)
    a = p.parse_args()
    scan(a.video, a.model, a.output)
