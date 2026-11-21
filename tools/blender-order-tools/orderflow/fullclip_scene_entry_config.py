"""Relative-path local model configuration shared by the native and existing scene profiles."""

import hashlib
import json
from pathlib import Path
import sys


def read(path):
    return json.loads(Path(path).read_bytes())


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(base, value):
    return str((Path(base) / Path(value)).resolve())


def _model(data, base):
    path = data.get("path") or data.get("model")
    sha = data.get("official_lfs_sha256") or data.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(x not in "0123456789abcdef" for x in sha):
        raise ValueError("local model requires its SHA-256 checksum")
    return {"path": relative(base, path), "sha256": sha}


def runtime(path):
    location = Path(path).resolve()
    data = read(location)
    base = location.parent
    result = dict(data)
    for key in ("cpu_python", "geometry_python"):
        if key in data:
            result[key] = relative(base, data[key])
    if "geometry_model" in data:
        result["depth_model"] = _model(data["geometry_model"], base)
        result["depth_sources"] = [
            {"root": relative(base, p), "files": []} for p in data.get("geometry_source_roots", [])
        ]
    else:
        receipt = Path(relative(base, data["geometry_assets"]))
        assets = read(receipt)
        result["depth_model"] = _model(assets["model"], receipt.parent)
        result["depth_sources"] = [
            {
                "root": relative(receipt.parent, s["root"]),
                "files": [
                    {"path": relative(receipt.parent, f["path"]), "sha256": f["sha256"]}
                    for f in s["files"]
                ],
            }
            for s in assets["sources"]
        ]
    if "object_model" in data:
        spec = data["object_model"]
        if isinstance(spec, dict):
            result["coco_model"] = _model(spec, base)
        else:
            receipt = Path(relative(base, spec))
            result["coco_model"] = _model(read(receipt), receipt.parent)
    return result


def local_only():
    def reject(event, args):
        if event in ("socket.connect", "socket.sendto"):
            raise RuntimeError("local scene inference forbids network connections")

    sys.addaudithook(reject)


def artifact(path):
    path = Path(path).resolve()
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


def save(path, data):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        encoding="utf8",
    )


def stage(job, stage_name, files, models=None):
    record = {
        "stage": stage_name,
        "status": "completed",
        "exit_code": 0,
        "llm_mode": "off",
        "delivery_ready": False,
        "backend": "orderflow_native",
        "producer": artifact(Path(__file__)),
        "models": models or {},
        "outputs": [artifact(f) for f in files],
    }
    dest = Path(job) / "stages" / f"{stage_name}-native.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    save(dest, record)
