"""Fit a single environment scale per camera shot using observed human dimensions."""

import json
from pathlib import Path
import numpy as np
from .fullclip_scene import camera_depth_hint


def scale_from_people(manifest, people, component_contract, height_m):
    bounds = [c["bounds"] for c in json.loads(Path(component_contract).read_bytes())["components"]]
    native_height = max(b[1][2] for b in bounds) - min(b[0][2] for b in bounds)
    unit_scale = height_m / native_height
    scales = {}
    evidence = {}
    for shot in manifest["shots"]:
        first, last = shot["first"], shot["last"]
        focal_px = shot["intrinsics"][0][0] * people["width"]
        ratios = []
        for frame in people["frames"][first : last + 1]:
            for actor in frame["actors"]:
                anchors = actor.get("anchors", {})
                point = next(
                    (
                        anchors[k]
                        for k in ("head", "shoulder", "hip")
                        if k in anchors
                        and anchors[k].get("score", 0) >= 0.45
                        and anchors[k].get("state") not in ("unknown", "inferred", "missing")
                    ),
                    None,
                )
                if not point or actor.get("pixels_per_meter", 0) <= 0:
                    continue
                depth = camera_depth_hint(
                    manifest,
                    frame["source_frame"],
                    point["u"] / people["width"],
                    point["v"] / people["height"],
                )
                if depth and depth > 0:
                    ratio = unit_scale * focal_px / (actor["pixels_per_meter"] * depth)
                    if np.isfinite(ratio) and ratio > 0:
                        ratios.append(np.log(ratio))
        scale = 1.0
        spread = None
        if len(ratios) >= 3:
            median = float(np.median(ratios))
            spread = float(np.median(np.abs(np.asarray(ratios) - median)))
            if spread < 0.35:
                scale = float(np.exp(median))
        scales[first] = scale
        evidence[first] = dict(
            world_scale=scale, observations=len(ratios), log_median_absolute_deviation=spread
        )
    return scales, evidence
