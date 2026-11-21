"""Reuse validated local measurements when iterating; all poses and geometry are rebuilt."""

import json
from fractions import Fraction
from pathlib import Path
import shutil
from . import core


def read_input_table(config_file, profile):
    table = profile.get("measurement_inputs", {})
    permitted = {"detections", "scene_jobs", "faces", "heads", "pose_observations"}
    if not isinstance(table, dict) or set(table) - permitted:
        raise ValueError(
            "Unknown measurement input. Only local detector and scene stage receipts are supported."
        )
    result = {}
    for key, value in table.items():
        path = (config_file.parent / value).resolve()
        if not path.is_file():
            raise ValueError(f"Missing prepared {key} data: {path}")
        result[key] = str(path)
    return result


def detection_input(path, dest, source_sha, config, package, shots, timeline):
    source = Path(path)
    receipt = core.read_json(source.with_name("receipt.json"))
    if (
        source.name != "frames.jsonl"
        or not receipt["requested_ranges_complete"]
        or receipt.get("errors")
    ):
        raise ValueError("Prepared detection did not complete")
    verified = {
        "source": source_sha,
        "model": core.sha256(config["detector_model"]),
        "entry_code": core.sha256(package / "auto_box/scripts/multi_actor_detect_t4.py"),
    }
    for name in ("gem/common/detectors/yolox.py", "gem/common/detectors/bytetrack.py"):
        verified[name] = core.sha256(Path(config["detector_repo"]) / name)
    if any(receipt["inputs"][k]["sha256"] != v for k, v in verified.items()):
        raise ValueError("Detector source, code or model changed")
    extent = lambda items: [(s["source_zero_start"], s["source_zero_end"]) for s in items]
    if (
        extent(receipt["shots"]) != extent(shots)
        or core.sha256(source) != receipt["outputs"][source.name]
    ):
        raise ValueError("Prepared detector shot intervals or data digest mismatch")
    rows = source.read_text(encoding="utf-8-sig").splitlines()
    if len(rows) != len(timeline["pts"]):
        raise ValueError("Incomplete detection frame count")
    for i, line in enumerate(rows):
        row = json.loads(line)
        if row["source_zero_based"] != i or Fraction(row["pts"]) * Fraction(
            row["time_base"]
        ) != Fraction(timeline["pts"][i]):
            raise ValueError(f"Detection clock mismatch at {i}")
    dest.mkdir()
    for name in ("receipt.json", "frames.jsonl"):
        shutil.copyfile(source.with_name(name), dest / name)


def image_observations(path, dest, source_sha, model, timeline):
    path = Path(path)
    data = core.read_json(path)
    if data.get("source_sha256") != source_sha or data.get("model_sha256") != core.sha256(model):
        raise ValueError("Image observations belong to another video or model")
    rows = data["frames"]
    if len(rows) != len(timeline["pts"]):
        raise ValueError("Incomplete image observation frames")
    for i, row in enumerate(rows):
        if row["source_frame"] != i or Fraction(row["pts"]) * Fraction(
            row["time_base"]
        ) != Fraction(timeline["pts"][i]):
            raise ValueError("Image observations have a different frame clock")
    shutil.copyfile(path, dest)


def camera_jobs(path, dest, shots):
    jobs = core.read_json(path)
    if [(j["first"], j["last"]) for j in jobs] != [
        (s["source_zero_start"], s["source_zero_end"]) for s in shots
    ]:
        raise ValueError("Prepared scene does not cover the source cuts")
    # measure_scene verifies the independent stage SHA, source and anchor for every shot.
    core.atomic_json(dest, jobs)
