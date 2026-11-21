"""Offline metric depth inference for selected keyframes and additional camera views."""

import argparse
from pathlib import Path
import sys

if __package__:
    from . import fullclip_scene_entry_config as cfg
else:
    import fullclip_scene_entry_config as cfg


def infer(runtime, video, frames, output, *, plan=None):
    cfg.local_only()
    import cv2
    import numpy as np
    import torch

    cv2.setNumThreads(2)
    config = cfg.runtime(runtime)
    for source in config["depth_sources"]:
        for item in source["files"]:
            if cfg.digest(item["path"]) != item["sha256"]:
                raise ValueError("depth model source hash changed")
        sys.path.insert(0, source["root"])
    weight = config["depth_model"]
    if cfg.digest(weight["path"]) != weight["sha256"]:
        raise ValueError("depth weights differ from configured checksum")
    from moge.model.v2 import MoGeModel

    device = config.get("geometry_device", "cpu")
    if device not in ("cpu", "cuda") or (device == "cuda" and not torch.cuda.is_available()):
        raise ValueError("requested geometry device unavailable")
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(0)
    torch.backends.cudnn.benchmark = False
    checkpoint = torch.load(weight["path"], map_location="cpu", weights_only=True)
    model = MoGeModel(**checkpoint["model_config"])
    model.load_state_dict(checkpoint["model"], strict=True)
    del checkpoint
    model.eval().to(device)
    capture = None if plan else cv2.VideoCapture(str(video))
    max_edge = int(config.get("max_edge", 640)) if plan else 512
    tokens = int(config.get("tokens", 1200)) if plan else 460
    if not 128 <= max_edge <= 1024 or tokens < 128:
        raise ValueError("invalid depth inference settings")
    source_sha = cfg.digest(video)
    try:
        if plan:
            work = []
            for job in cfg.read(plan)["jobs"]:
                folder = Path(job["job"])
                selection_file = folder / "stages/select/data/selection.json"
                selection = cfg.read(selection_file)
                if (
                    selection["source_video"]["sha256"] != source_sha
                    or cfg.digest(selection["image"]["path"]) != selection["image"]["sha256"]
                ):
                    raise ValueError("refusing changed anchor image/source")
                work.append((selection["selected_frame"], folder, selection, selection_file))
        else:
            work = [(int(f), None, None, None) for f in frames]
        for f, folder, selection, selection_file in work:
            if plan:
                image = cv2.imread(selection["image"]["path"])
            else:
                capture.set(cv2.CAP_PROP_POS_FRAMES, f)
                ok, image = capture.read()
                if not ok:
                    raise ValueError(f"missing source frame {f}")
            h, w = image.shape[:2]
            scale = min(1.0, max_edge / max(h, w))
            small = cv2.resize(
                image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA
            )
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            tensor = torch.from_numpy(rgb.astype(np.float32) / 255).permute(2, 0, 1).to(device)
            with torch.inference_mode():
                result = model.infer(
                    tensor,
                    num_tokens=tokens,
                    use_fp16=device == "cuda",
                    apply_mask=True,
                    force_projection=True,
                    fov_x=None,
                )
            keys = (
                ("points", "depth", "intrinsics", "mask", "normal")
                if plan
                else ("depth", "intrinsics", "mask")
            )
            arrays = {k: result[k].detach().cpu().numpy() for k in keys}
            dst = (
                (folder / "stages/geometry/data/raw_geometry.npz")
                if plan
                else (output / f"view_{f:07d}.npz")
            )
            dst.parent.mkdir(parents=True, exist_ok=True)
            if (
                not np.isfinite(arrays["intrinsics"]).all()
                or np.mean(arrays["mask"] & np.isfinite(arrays["depth"]) & (arrays["depth"] > 0))
                < 0.05
            ):
                raise ValueError(f"insufficient valid geometry for frame {f}")
            np.savez_compressed(dst, **arrays)
            if plan:
                rec = dst.with_name("inference_receipt.json")
                cfg.save(
                    rec,
                    {
                        "status": "actual_local_moge2_geometry_generated_not_truth",
                        "selection": cfg.artifact(selection_file),
                        "source_video": selection["source_video"],
                        "selected_frame": f,
                        "selected_pts": selection["selected_pts"],
                        "model_sha256": weight["sha256"],
                        "device": device,
                        "max_edge": max_edge,
                        "num_tokens": tokens,
                        "backend_code": cfg.artifact(__file__),
                        "outputs": [cfg.artifact(dst)],
                        "llm_mode": "off",
                        "legacy_scene_used": False,
                        "camera_truth_verified": False,
                        "metric_depth_truth_verified": False,
                        "delivery_ready": False,
                    },
                )
                cfg.stage(folder, "geometry", [rec, dst], {"MoGe2": weight["sha256"]})
            print(f"depth view {f}", flush=True)
    finally:
        if capture is not None:
            capture.release()
    if not plan:
        cfg.save(
            output / "depth_views.json",
            {
                "source_sha256": source_sha,
                "frames": frames,
                "llm_mode": "off",
                "model_sha256": weight["sha256"],
                "outputs": {str(i): cfg.digest(output / f"view_{i:07d}.npz") for i in frames},
            },
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--runtime", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--frames")
    p.add_argument("--output", type=Path)
    p.add_argument("--plan")
    a = p.parse_args()
    if not a.plan and (not a.frames or not a.output):
        p.error("additional view inference requires --frames and --output")
    infer(
        a.runtime,
        a.video,
        [] if a.plan else list(map(int, a.frames.split(","))),
        a.output,
        plan=a.plan,
    )
