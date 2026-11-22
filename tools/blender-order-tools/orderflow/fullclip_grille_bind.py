"""Register a fixed foreground grille using multiple native camera poses."""

from collections import defaultdict
import json

import numpy as np


def _solution(jacobian, error):
    design = np.asarray(jacobian)
    target = np.asarray(error)
    if np.linalg.matrix_rank(design) != 2:
        return None
    weights = np.ones(len(target))
    for _ in range(4):
        coef = np.linalg.lstsq(design * weights[:, None], target * weights, rcond=None)[0]
        residual = target - design @ coef
        scale = max(0.5, float(np.median(np.abs(residual))))
        weights = np.sqrt(np.minimum(1, 1.5 * scale / np.maximum(np.abs(residual), 1e-6)))
    return coef


def register_fixed_grilles(scene, camera):
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    by_shot = defaultdict(list)
    for obj in scene.objects:
        if obj.get("grille_frame_centers"):
            by_shot[obj["source_frame"]].append(obj)
    result = {}
    saved = scene.frame_current
    try:
        for anchor, objects in by_shot.items():
            scene.frame_set(anchor + 1)
            rotation = camera.matrix_world.to_quaternion()
            across = [rotation @ Vector((1, 0, 0)), rotation @ Vector((0, 1, 0))]
            samples = defaultdict(list)
            for obj in objects:
                center = (
                    sum((obj.matrix_world @ v.co for v in list(obj.data.vertices)[:4]), Vector())
                    / 4
                )
                axis = 0 if obj["grille_axis"] == "vertical" else 1
                for key, coordinate in json.loads(obj["grille_frame_centers"]).items():
                    samples[int(key)].append(
                        (center, axis, obj["grille_image_length"], coordinate)
                    )
            jacobian = []
            deltas = []
            depths = []
            for frame, rows in sorted(samples.items()):
                scene.frame_set(frame + 1)
                for center, axis, length, coordinate in rows:
                    uv = world_to_camera_view(scene, camera, center)
                    if uv.z <= camera.data.clip_start:
                        continue
                    step = max(0.0001, uv.z * 0.001)
                    sign = 1 if axis == 0 else -1
                    deltas.append(coordinate - (uv[axis] if axis == 0 else 1 - uv[axis]) * length)
                    jacobian.append(
                        [
                            sign
                            * length
                            * (
                                world_to_camera_view(scene, camera, center + direction * step)[
                                    axis
                                ]
                                - uv[axis]
                            )
                            / step
                            for direction in across
                        ]
                    )
                    depths.append(uv.z)
            if len(deltas) < 12:
                continue
            coeff = _solution(jacobian, deltas)
            if coeff is None or np.linalg.norm(coeff) > 0.045 * np.median(depths):
                continue
            before = np.median(np.abs(deltas))
            after = np.median(np.abs(deltas - np.asarray(jacobian) @ coeff))
            if after >= before:
                continue
            shift = across[0] * float(coeff[0]) + across[1] * float(coeff[1])
            for obj in objects:
                obj.location += shift
            result[str(anchor)] = dict(
                samples=len(deltas),
                before_median_px=float(before),
                after_median_px=float(after),
                rigid_shift_m=float(shift.length),
            )
    finally:
        scene.frame_set(saved)
    return result
