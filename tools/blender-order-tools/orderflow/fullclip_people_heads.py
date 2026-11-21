"""Optional local YOLO person/head detector for close framings without facial landmarks."""

import argparse
import json
from pathlib import Path
import av
import cv2
import numpy as np
import onnxruntime as ort

if __package__:
    from .fullclip_people_faces import digest, overlap, undo_rotation
else:
    from fullclip_people_faces import digest, overlap, undo_rotation


class HeadDetector:
    def __init__(self, model):
        opt = ort.SessionOptions()
        opt.intra_op_num_threads, opt.inter_op_num_threads = 2, 1
        self.session = ort.InferenceSession(str(model), opt, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        if [o.name for o in self.session.get_outputs()] != ["boxes", "scores", "classes"]:
            raise ValueError("Expected person/head detector with boxes,scores,classes outputs")

    def detect(self, image, turns=0, size=512):
        h, w = image.shape[:2]
        view = np.ascontiguousarray(np.rot90(image, turns))
        vh, vw = view.shape[:2]
        scale = size / max(vh, vw)
        rw, rh = round(vw * scale), round(vh * scale)
        ox, oy = (640 - rw) // 2, (640 - rh) // 2
        inp = np.full((640, 640, 3), 114, np.uint8)
        inp[oy : oy + rh, ox : ox + rw] = cv2.resize(view, (rw, rh), interpolation=cv2.INTER_AREA)
        blob = np.ascontiguousarray(inp.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
        boxes, scores, classes = self.session.run(None, {self.input: blob})
        output = []
        for idx in np.flatnonzero(scores[0, :, 0] > 0.23):
            b = boxes[0, idx]
            xy = (np.stack((b[:2] - b[2:] / 2, b[:2] + b[2:] / 2)) - [ox, oy]) / scale
            pts = undo_rotation(xy, turns, w, h)
            lo = np.maximum(pts.min(axis=0), [0, 0])
            hi = np.minimum(pts.max(axis=0), [w - 1, h - 1])
            if np.any(hi - lo < 4):
                continue
            cl = int(classes[0, idx, 0])
            output.append(
                {
                    "bbox": [*lo.tolist(), *hi.tolist()],
                    "score": float(scores[0, idx, 0]),
                    "class_id": cl,
                    "turns": turns,
                }
            )
        # Decode outputs per class. A full-person box may surround the head.
        keep = []
        for cand in sorted(output, key=lambda c: -c["score"]):
            if all(
                cand["class_id"] != prev["class_id"] or overlap(cand["bbox"], prev["bbox"]) < 0.45
                for prev in keep
            ):
                keep.append(cand)
        return keep


def scan(video, model, detections, faces, output, probe=()):
    rows = [json.loads(line) for line in Path(detections).read_text().splitlines()]
    face_rows = json.loads(Path(faces).read_text(encoding="utf8"))["frames"] if faces else None
    detector = HeadDetector(model)
    cv2.setNumThreads(2)
    frames = []
    with av.open(str(video)) as c:
        for i, fr in enumerate(c.decode(video=0)):
            out = []
            if (not probe or i in probe) and (
                probe
                or not rows[i]["observed_tracks"]
                or sum(
                    1
                    for t in rows[i]["observed_tracks"]
                    if t["box_xyxy"][3] - t["box_xyxy"][1] > 0.85 * fr.height
                )
                > 0
            ):
                if probe or not (
                    face_rows
                    and any(
                        t["score"] > 0.76 and t["bbox"][3] - t["bbox"][1] > 0.2 * fr.height
                        for t in face_rows[i]["faces"]
                    )
                ):
                    img = fr.to_ndarray(format="bgr24")
                    if np.mean(img) < 70:
                        img = cv2.LUT(
                            img, np.rint(255 * (np.arange(256) / 255) ** 0.54).astype("uint8")
                        )
                    for turn in (0, 1, 3, 2):
                        curr = detector.detect(img, turn)
                        out.extend(curr)
                        if any(
                            q["class_id"] == 1
                            and q["score"] > 0.72
                            and max(q["bbox"][2] - q["bbox"][0], q["bbox"][3] - q["bbox"][1])
                            > 0.18 * fr.height
                            for q in out
                        ):
                            break
            frames.append(
                {"source_frame": i, "pts": fr.pts, "time_base": str(fr.time_base), "items": out}
            )
            if probe and i in probe:
                print(
                    i,
                    [
                        (t["class_id"], round(t["score"], 2), list(map(round, t["bbox"])))
                        for t in sorted(out, key=lambda t: -t["score"])[:6]
                    ],
                    flush=True,
                )
    result = {
        "schema": "orderflow.fullclip_heads.v1",
        "source_sha256": digest(video),
        "model_sha256": digest(model),
        "frames": frames,
    }
    Path(output).write_text(json.dumps(result, separators=(",", ":")), encoding="utf8")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for n in ("video", "model", "detections", "output"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--faces")
    p.add_argument("--probe", default="")
    a = p.parse_args()
    scan(
        a.video,
        a.model,
        a.detections,
        a.faces,
        a.output,
        [int(s) for s in a.probe.split(",") if s],
    )
