"""Collect comparable rendered surfaces, then validate their real source appearance."""

from pathlib import Path
import json
import subprocess
import tempfile
import numpy as np


def collect_pairs(scene, camera, people, rigs):
    import bpy
    from mathutils import Vector
    from bpy_extras.object_utils import world_to_camera_view

    if __package__:
        from .fullclip_guardrail_actor_visibility import SEATING
    else:
        from fullclip_guardrail_actor_visibility import SEATING
    frames = people["frames"]
    w, h = people["width"], people["height"]
    pairs = []
    candidate_shots = {
        frames[k]["shot_id"]
        for ob in scene.objects
        if ob.get("reconstruction_kind") in SEATING
        and isinstance(k := ob.get("source_frame"), int)
        and 0 <= k < len(frames)
    }
    for i, fr in enumerate(frames):
        if fr["shot_id"] not in candidate_shots or not fr["actors"]:
            continue
        scene.frame_set(i + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        cam = camera.evaluated_get(deps)
        mw = cam.matrix_world.normalized()
        inverse = cam.calc_matrix_camera(deps, x=w, y=h).inverted()
        for actor in fr["actors"]:
            tid = str(actor["track_id"])
            rig = rigs.get((str(fr["shot_id"]), tid))
            if rig is None:
                continue
            sh = actor["anchors"].get("shoulder", {})
            hp = actor["anchors"].get("hip", {})
            sides = [actor["joints2d"].get(side + "_shoulder", {}) for side in ("left", "right")]
            if (
                min(
                    sh.get("score", 0),
                    hp.get("score", 0),
                    *[v.get("score", 0) for v in sides],
                )
                < 0.48
            ):
                continue
            sw = abs(sides[0]["u"] - sides[1]["u"])
            if sw < 12:
                continue
            a = np.array([sh["u"], sh["v"]])
            b = np.array([hp["u"], hp["v"]])
            parts = [
                ob.evaluated_get(deps)
                for ob in rig.children
                if ob.type == "MESH" and not ob.hide_render
            ]
            for t in (0.23, 0.38, 0.55, 0.72, 0.90):
                for dx in (-0.22, -0.11, 0, 0.11, 0.22):
                    u, v = (1 - t) * a + t * b + [dx * sw, 0]
                    if not 4 < u < w - 5 or not 4 < v < h - 5:
                        continue
                    ray = (
                        mw.to_quaternion()
                        @ (inverse @ Vector((2 * u / w - 1, 1 - 2 * v / h, 0, 1)))
                        .to_3d()
                        .normalized()
                    )
                    hit, co, _, _, ob, _ = scene.ray_cast(deps, mw.translation, ray)
                    if not hit or ob.get("reconstruction_kind") not in SEATING:
                        continue
                    k = ob.get("source_frame")
                    if (
                        not isinstance(k, int)
                        or not 0 <= k < len(frames)
                        or frames[k]["shot_id"] != fr["shot_id"]
                    ):
                        continue
                    depth = (co - mw.translation).length
                    for part in parts:
                        inv = part.matrix_world.inverted()
                        ok, pos, _, _ = part.ray_cast(
                            inv @ mw.translation, (inv.to_3x3() @ ray).normalized()
                        )
                        if (
                            ok
                            and (part.matrix_world @ pos - mw.translation).length > depth + 0.015
                        ):
                            pairs.append(
                                dict(
                                    i=i,
                                    id=tid,
                                    u=float(u),
                                    v=float(v),
                                    t=t,
                                    k=k,
                                    xyz=list(co),
                                    ob=ob.name,
                                )
                            )
                            break
    valid = []
    for k in sorted({pair["k"] for pair in pairs}):
        scene.frame_set(k + 1)
        deps = bpy.context.evaluated_depsgraph_get()
        center = camera.evaluated_get(deps).matrix_world.translation
        for pair in [pair for pair in pairs if pair["k"] == k]:
            pos = Vector(pair.pop("xyz"))
            vc = world_to_camera_view(scene, camera, pos)
            u, v = vc.x * w, (1 - vc.y) * h
            if not (4 < u < w - 5 and 4 < v < h - 5 and vc.z > 0):
                continue
            if any(
                a["bbox"][0] - 3 < u < a["bbox"][2] + 3 and a["bbox"][1] - 3 < v < a["bbox"][3] + 3
                for a in frames[k]["actors"]
            ):
                continue
            hit, co, _, _, ob, _ = scene.ray_cast(deps, center, (pos - center).normalized())
            if hit and ob.name == pair["ob"] and (co - pos).length <= 0.025:
                pair["ref"] = [float(u), float(v)]
                valid.append(pair)
    return valid


def augment(scene, camera, people, rigs, evidence, source_config):
    pairs = collect_pairs(scene, camera, people, rigs)
    if not pairs:
        return None
    source = source_config["source"]
    runtime = source_config["media_python"]
    with tempfile.TemporaryDirectory(prefix="orderflow-surface-") as work:
        work = Path(work)
        request = work / "request.json"
        result = work / "response.json"
        observed = (
            evidence if isinstance(evidence, dict) else json.loads(Path(evidence).read_bytes())
        )
        request.write_text(
            json.dumps(
                dict(source=source, people=people, pairs=pairs, evidence=observed),
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                runtime,
                str(Path(__file__).with_name("fullclip_guardrail_material.py")),
                str(request),
                str(result),
            ],
            check=True,
        )
        return json.loads(result.read_bytes())
