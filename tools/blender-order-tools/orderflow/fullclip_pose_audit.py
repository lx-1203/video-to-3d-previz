"""Inspect the saved, reopened candidate in its own camera at every source frame."""

from pathlib import Path
import sys

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view


def camera_visibility(scene, deps, camera, mesh, samples):
    """Sample the actor inside its source box, accounting for intervening native geometry."""
    from collections import Counter

    matrix = mesh.matrix_world
    inverse = matrix.inverted()
    center = camera.matrix_world.translation
    origin = inverse @ center
    local_basis = inverse.to_3x3()
    camera_inverse = camera.matrix_world.inverted()
    blocked = Counter()
    visible = []
    for label, point in samples:
        direction = (point - center).normalized()
        hit, local, _, _ = mesh.ray_cast(origin, (local_basis @ direction).normalized())
        if not hit:
            continue
        surface = matrix @ local
        z = -(camera_inverse @ surface).z
        if not (camera.data.clip_start <= z <= camera.data.clip_end):
            continue
        # Native generated meshes key render and viewport visibility together.
        hit, _, _, _, obj, _ = scene.ray_cast(deps, center, direction)
        if hit and obj.original == mesh.original:
            visible.append(label)
        elif hit and not obj.hide_render:
            blocked[obj.name] += 1
    tested = len(visible) + sum(blocked.values())
    return dict(visible_samples=visible, blockers=dict(blocked), sampled_surface_points=tested)


def audit(folder):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from orderflow import core

    scene = bpy.context.scene
    people = core.read_json(folder / "people.json")
    binding = core.read_json(folder / "binding.json")
    if core.sha256(bpy.data.filepath) != binding["scene_sha256"]:
        raise ValueError("Audit was not opened on the bound scene")
    rigs = {}
    for name in binding["editable_tracks"].values():
        rig = scene.objects[name]
        rigs[(rig["shot_id"], rig["track_id"])] = rig
    initial_scale = {rig.name: tuple(rig.scale) for rig in rigs.values()}
    keys = {"head": ("DEF-spine.006", 0.5)}
    for side, suffix in [("left", ".L"), ("right", ".R")]:
        for part, bone, f in [
            ("shoulder", "upper_arm", 0),
            ("elbow", "forearm", 0),
            ("wrist", "forearm", 1),
            ("hip", "thigh", 0),
            ("knee", "shin", 0),
            ("ankle", "shin", 1),
        ]:
            keys[f"{side}_{part}"] = (f"DEF-{bone}{suffix}", f)
    rows = []
    error = {}
    max_scale_difference = 0.0
    max_bone_delta = 0.0
    if scene.frame_end - scene.frame_start + 1 != len(people["frames"]):
        raise ValueError("Candidate animation length differs from source")
    for frame in people["frames"]:
        i = frame["source_frame"]
        scene.frame_set(scene.frame_start + i)
        deps = bpy.context.evaluated_depsgraph_get()
        camera = scene.camera.evaluated_get(deps)
        expected = {
            actor["track_id"]: actor for actor in frame["actors"] if actor.get("renderable", True)
        }
        active = set()
        for (shot, track), rig in rigs.items():
            meshes = [child for child in rig.children if child.type == "MESH"]
            if len(meshes) != 1:
                raise ValueError("Profiled actor mesh missing or duplicated")
            if meshes[0].hide_render:
                continue
            if shot != str(frame["shot_id"]) or track not in expected:
                raise ValueError(f"Actor visible outside measured interval at {i}")
            active.add(track)
            state = expected[track]
            evaluated = rig.evaluated_get(deps)
            scales = evaluated.matrix_world.to_scale()
            max_scale_difference = max(
                max_scale_difference,
                *(abs(a - b) for a, b in zip(scales, initial_scale[rig.name])),
            )
            points = {}
            samples = []
            x1, y1, x2, y2 = state["bbox"]
            for label, (name, f) in keys.items():
                bone = evaluated.pose.bones[name]
                length = (bone.tail - bone.head).length
                max_bone_delta = max(max_bone_delta, abs(length - bone.bone.length))
                world = evaluated.matrix_world @ bone.head.lerp(bone.tail, f)
                uv = world_to_camera_view(scene, scene.camera, world)
                x, y = float(uv.x * people["width"]), float((1 - uv.y) * people["height"])
                points[label] = [round(x, 2), round(y, 2), round(uv.z, 5)]
                if (
                    uv.z > 0
                    and 0 <= uv.x <= 1
                    and 0 <= uv.y <= 1
                    and x1 <= x <= x2
                    and y1 <= y <= y2
                ):
                    samples.append((label, world))
                obs = state["joints2d"].get(label) or state.get("anchors", {}).get(label)
                if (
                    obs
                    and obs.get("score", 0) >= 0.55
                    and obs.get("visibility", "observed")
                    in ("observed", "visible", "detected", "model_estimate")
                ):
                    dist = float(np.hypot(x - obs["u"], y - obs["v"]))
                    error.setdefault(label, []).append(dist)
            visibility = camera_visibility(
                scene, deps, camera, meshes[0].evaluated_get(deps), samples
            )
            rows.append(dict(frame=i, track=track, points=points, camera_visibility=visibility))
        if active != set(expected):
            raise ValueError(f"Expected visible track missing at source frame {i}")
    if max_scale_difference > 1e-4 or max_bone_delta > 1e-4:
        raise ValueError("Actor scale or segment length changed across the source timeline")
    core.atomic_json(
        folder / "pose-projection.json",
        dict(
            source_sha256=people["source_sha256"],
            scene_sha256=binding["scene_sha256"],
            frames=len(people["frames"]),
            tracks=len(rigs),
            max_scale_difference=max_scale_difference,
            max_bone_length_difference=max_bone_delta,
            observed_pixel_errors={
                k: dict(
                    n=len(v),
                    median=round(float(np.median(v)), 2),
                    p90=round(float(np.percentile(v, 90)), 2),
                )
                for k, v in error.items()
            },
            rows=rows,
            semantic_accuracy="unreviewed",
            collision_check="not_performed",
        ),
    )


if __name__ == "__main__":
    audit(Path(sys.argv[sys.argv.index("--") + 1]))
