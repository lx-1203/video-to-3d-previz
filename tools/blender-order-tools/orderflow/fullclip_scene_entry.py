"""Generate source-bound shot observations using installed local COCO80 and MoGe2 models."""

import argparse
from fractions import Fraction
from pathlib import Path
import subprocess
import uuid

if __package__:
    from . import fullclip_scene_entry_config as cfg
else:
    import fullclip_scene_entry_config as cfg


def _ranges(shots):
    if isinstance(shots, dict):
        shots = shots["shots"]
    ranges = []
    for shot in shots:
        if isinstance(shot, dict):
            if "source_zero_start" in shot:
                pair = shot["source_zero_start"], shot["source_zero_end"]
            else:
                pair = shot["first"], shot["last"]
        else:
            pair = shot
        if len(pair) != 2 or any(type(f) is not int for f in pair):
            raise ValueError("integer inclusive source-frame range required")
        a, b = pair
        if a < 0 or b < a or (ranges and ranges[-1][1] + 1 != a):
            raise ValueError("invalid, overlapping, or discontinuous shot ranges")
        ranges.append((a, b))
    if not ranges:
        raise ValueError("empty shot list")
    return ranges


def run_scene_jobs(source, shots, runtime, output_dir):
    """Return completed native records compatible with the scene measurement stage."""
    source = Path(source).resolve()
    runtime = Path(runtime).resolve()
    config = cfg.runtime(runtime)
    out = Path(output_dir).resolve()
    batch = out / ("native_" + uuid.uuid4().hex)
    batch.mkdir(parents=True)
    jobs = [
        {"first": a, "last": b, "job": str(batch / f"shot_{a:07d}_{b:07d}")}
        for a, b in _ranges(shots)
    ]
    plan = batch / "source_plan.json"
    cfg.save(plan, {"source": cfg.artifact(source), "runtime": str(runtime), "jobs": jobs})
    subprocess.run(
        [config["cpu_python"], "-B", str(Path(__file__).resolve()), "--observe-plan", str(plan)],
        check=True,
    )
    subprocess.run(
        [
            config["geometry_python"],
            "-B",
            str(Path(__file__).with_name("fullclip_scene_depth.py")),
            "--video",
            str(source),
            "--runtime",
            str(runtime),
            "--plan",
            str(plan),
        ],
        check=True,
    )
    for job in jobs:
        root = Path(job["job"]) / "stages"
        if not all(
            (root / (stage + "-native.json")).is_file()
            for stage in ("select", "objects", "geometry")
        ):
            raise ValueError("required native observation stage missing")
        job["exit_code"] = 0
    if cfg.digest(source) != cfg.read(plan)["source"]["sha256"]:
        raise ValueError("source video changed during observation")
    cfg.save(batch / "scene-jobs.json", jobs)
    return jobs


def _select(plan):
    import av
    import cv2

    source = plan["source"]
    if cfg.digest(source["path"]) != source["sha256"]:
        raise ValueError("source content changed before keyframe selection")
    jobs = iter(plan["jobs"])
    job = next(jobs)
    rows = []
    candidates = []
    best = None
    with av.open(source["path"]) as container:
        stream = container.streams.video[0]
        stream.thread_count = 2
        fps = str(stream.average_rate)
        previous = None
        for i, frame in enumerate(container.decode(stream)):
            a, b = job["first"], job["last"]
            if i < a:
                continue
            if frame.pts is None:
                raise ValueError("source timestamp missing")
            timestamp = Fraction(frame.pts) * frame.time_base
            if previous is not None and timestamp <= previous:
                raise ValueError("non-increasing source timestamp")
            previous = timestamp
            row = {
                "source_frame": i,
                "pts": frame.pts,
                "time_base": str(frame.time_base),
                "source_time": str(timestamp),
            }
            rows.append(row)
            if (i - a) % max(1, (b - a + 1) // 12) == 0 or i == b:
                image = frame.to_ndarray(format="bgr24")
                gray = cv2.cvtColor(cv2.resize(image, (640, 360)), cv2.COLOR_BGR2GRAY)
                score = float(cv2.Laplacian(gray, cv2.CV_32F).var())
                candidates.append({"source_frame": i, "focus_score": score})
                if best is None or score > best[0]:
                    best = score, i, image, row
            if i < b:
                continue
            folder = Path(job["job"]) / "stages/select/data"
            folder.mkdir(parents=True)
            image_path = folder / "selected.png"
            if not cv2.imwrite(str(image_path), best[2]):
                raise RuntimeError("failed to preserve source anchor image")
            selection = folder / "selection.json"
            cfg.save(
                selection,
                {
                    "source_video": source,
                    "source_start": a,
                    "frame_count": b - a + 1,
                    "frames": rows,
                    "source_fps_metadata": fps,
                    "candidates": candidates,
                    "selected_frame": best[1],
                    "selected_pts": best[3],
                    "image": cfg.artifact(image_path),
                    "selection_method": "maximum Laplacian variance at evenly spaced candidate frames",
                    "llm_mode": "off",
                    "legacy_scene_used": False,
                },
            )
            cfg.stage(job["job"], "select", [selection, image_path])
            job = next(jobs, None)
            if job is None:
                break
            rows, candidates, best = [], [], None
    if job is not None:
        raise ValueError("input ends before last requested source frame")


def _decode(raw, ratio):
    import numpy as np
    from rtmlib.tools.object_detection.post_processings import multiclass_nms

    if raw.shape != (1, 8400, 85) or not np.isfinite(raw).all():
        raise ValueError("local detector must provide COCO80 YOLOX raw 1x8400x85")
    grid = []
    strides = []
    for step in (8, 16, 32):
        yy, xx = np.mgrid[: 640 // step, : 640 // step]
        grid.append(np.stack([xx, yy], axis=-1).reshape(1, -1, 2))
        strides.append(np.full((1, xx.size, 1), step))
    values = raw.copy()
    stride = np.concatenate(strides, axis=1)
    values[:, :, :2] = (values[:, :, :2] + np.concatenate(grid, axis=1)) * stride
    values[:, :, 2:4] = np.exp(values[:, :, 2:4]) * stride
    center, size = values[0, :, :2], values[0, :, 2:4]
    boxes = np.column_stack((center - size * 0.5, center + size * 0.5)) / ratio
    kept, _ = multiclass_nms(boxes, values[0, :, 4:5] * values[0, :, 5:], 0.45, 0.25)
    return [] if kept is None else kept


def _detect(plan):
    import cv2
    import numpy as np
    import onnxruntime as ort
    from rtmlib.tools.object_detection.yolox import YOLOX

    config = cfg.runtime(plan["runtime"])
    model = config["coco_model"]
    if cfg.digest(model["path"]) != model["sha256"]:
        raise ValueError("local COCO80 detector checksum mismatch")
    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        model["path"], sess_options=options, providers=["CPUExecutionProvider"]
    )
    input_spec = session.get_inputs()[0]
    if input_spec.shape != [1, 3, 640, 640]:
        raise ValueError("local COCO80 YOLOX model must have a static 640px input")
    for job in plan["jobs"]:
        root = Path(job["job"])
        selected = cfg.read(root / "stages/select/data/selection.json")
        anchor = selected["image"]
        if cfg.digest(anchor["path"]) != anchor["sha256"]:
            raise ValueError("selected image changed before object detection")
        image = cv2.imread(anchor["path"])
        h, w = image.shape[:2]
        ratio = min(640 / h, 640 / w)
        resized = cv2.resize(image, (int(w * ratio), int(h * ratio)))
        padded = np.full((640, 640, 3), 114, np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        outputs = session.run(
            None, {input_spec.name: padded.transpose(2, 0, 1)[None].astype(np.float32)}
        )
        raw = outputs[0]
        rows = []
        for idx, d in enumerate(sorted(_decode(raw, ratio), key=lambda d: -d[4])):
            box = d[:4].copy()
            box[[0, 2]] = np.clip(box[[0, 2]], 0, w)
            box[[1, 3]] = np.clip(box[[1, 3]], 0, h)
            if not (box[0] < box[2] and box[1] < box[3]):
                continue
            label = int(d[5])
            rows.append(
                {
                    "id": f"det_{idx:03d}",
                    "box_xyxy": box.tolist(),
                    "class_id": label,
                    "semantic_class": YOLOX.COCO_CLASSES[label],
                    "score": float(d[4]),
                    "semantic_verified": False,
                }
            )
        folder = root / "stages/objects/data"
        folder.mkdir(parents=True)
        observations = folder / "detections.json"
        raw_file = folder / "raw_detections.npz"
        np.savez_compressed(raw_file, raw=raw)
        cfg.save(
            observations,
            {
                "source_video": selected["source_video"],
                "selected_frame": selected["selected_frame"],
                "selected_pts": selected["selected_pts"],
                "source_image": anchor,
                "detections": rows,
                "resolution": [w, h],
                "model": model,
                "raw_shape": list(raw.shape),
                "score_threshold": 0.25,
                "llm_mode": "off",
                "backend_code": cfg.artifact(__file__),
                "delivery_ready": False,
            },
        )
        cfg.stage(root, "objects", [observations, raw_file], {"YOLOX_COCO80": model["sha256"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--observe-plan", type=Path)
    parser.add_argument("--video")
    parser.add_argument("--runtime")
    parser.add_argument("--shots-json")
    parser.add_argument("--output")
    a = parser.parse_args()
    if a.observe_plan:
        cfg.local_only()
        import cv2

        cv2.setNumThreads(2)
        plan = cfg.read(a.observe_plan)
        _select(plan)
        _detect(plan)
    else:
        if not all((a.video, a.runtime, a.shots_json, a.output)):
            parser.error("--video, --runtime, --shots-json and --output are required")
        records = run_scene_jobs(a.video, cfg.read(a.shots_json), a.runtime, a.output)
        print(str(Path(records[0]["job"]).parent / "scene-jobs.json"))
