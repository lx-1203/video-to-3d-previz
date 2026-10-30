"""Export current GEM-X Body Motion parameters as centimetre, Y-up BVH.

Uses the installed official GEM-X/SOMA 0.3.3 geometry adapter; no estimator,
body-model asset, or third-party exporter implementation is bundled here.
Each joint has six channels so public SOMA joint positions and orientations
survive conversion, including non-root translations. The output remains a
source motion skeleton and must be retargeted to the client's rig.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import warnings
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_motion_bvh(path, names, parents, world_transforms, rest_transforms, fps):
    """Write a public-joint motion and independently reconstruct its BVH FK.

    Matrices are column-vector world transforms in metres. The file uses
    intrinsic ZXY rotation channels, centimetres and no virtual SOMA Root.
    """
    import numpy as np
    from scipy.spatial.transform import Rotation

    path = Path(path)
    names = tuple(str(name) for name in names)
    parents = np.asarray(parents, dtype=np.int64)
    world = np.asarray(world_transforms, dtype=np.float64)
    rest = np.asarray(rest_transforms, dtype=np.float64)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be finite and positive")
    if world.ndim != 4 or world.shape[1:] != (len(names), 4, 4) or world.shape[0] < 1:
        raise ValueError("world_transforms must have shape (frames,joints,4,4)")
    if rest.shape != (len(names), 4, 4) or len(parents) != len(names):
        raise ValueError("Rest skeleton and parent count differ from the motion")
    if not np.isfinite(world).all() or not np.isfinite(rest).all():
        raise ValueError("Motion contains non-finite transforms")
    if parents[0] != -1 or any(p < 0 or p >= i for i, p in enumerate(parents[1:], start=1)):
        raise ValueError("Expected one topologically ordered tree rooted at Hips")
    if len(set(names)) != len(names) or any(not n or any(c.isspace() or c in "{}" for c in n) for n in names):
        raise ValueError("BVH joint names must be unique tokens")

    positions = world[..., :3, 3]
    rotations = world[..., :3, :3]
    # Runtime matrices should contain rigid orientation, never bone-scale matrices.
    identity = np.eye(3)
    rigid_error = np.max(np.abs(np.swapaxes(rotations, -1, -2) @ rotations - identity))
    if rigid_error > 2e-3 or np.min(np.linalg.det(rotations)) < 0.99:
        raise ValueError(f"SOMA returned non-rigid orientation matrices ({rigid_error:g})")
    local_r = np.empty_like(rotations)
    local_t = np.empty_like(positions)
    offsets = np.zeros((len(names), 3), dtype=np.float64)
    for j, p in enumerate(parents):
        if p < 0:
            local_r[:, j], local_t[:, j] = rotations[:, j], positions[:, j]
        else:
            inv_parent = np.swapaxes(rotations[:, p], -1, -2)
            local_r[:, j] = inv_parent @ rotations[:, j]
            local_t[:, j] = np.einsum("tij,tj->ti", inv_parent, positions[:, j] - positions[:, p])
            offsets[j] = rest[p, :3, :3].T @ (rest[j, :3, 3] - rest[p, :3, 3])
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Gimbal lock detected.*", category=UserWarning)
        euler = Rotation.from_matrix(local_r.reshape(-1, 3, 3)).as_euler("ZXY").reshape(*local_r.shape[:2], 3)
    euler = np.unwrap(euler, axis=0)
    children = [[] for _ in names]
    for j, p in enumerate(parents):
        if p >= 0:
            children[p].append(j)
    order = []
    lines = ["HIERARCHY"]

    def hierarchy(j, depth):
        indent = "  " * depth
        lines.extend([f"{indent}{'ROOT' if parents[j] < 0 else 'JOINT'} {names[j]}", f"{indent}{{"])
        lines.append(f"{indent}  OFFSET " + " ".join(f"{v:.9f}" for v in offsets[j] * 100))
        lines.append(f"{indent}  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation")
        order.append(j)
        for child in children[j]:
            hierarchy(child, depth + 1)
        if not children[j]:
            lines.extend([f"{indent}  End Site", f"{indent}  {{", f"{indent}    OFFSET 0 0.1 0", f"{indent}  }}"])
        lines.append(f"{indent}}}")

    hierarchy(0, 0)
    # Blender's BVH importer subtracts each rest OFFSET from a present
    # X/Y/Zposition channel before applying the pose-bone location. Therefore
    # position channels carry full parent-local positions (not offset deltas).
    # This explicit six-channel convention also retains small non-root motion.
    translations_cm = local_t * 100
    channels = np.concatenate((translations_cm, np.rad2deg(euler)), axis=-1)[:, order]
    lines.extend(["MOTION", f"Frames: {len(world)}", f"Frame Time: {1 / fps:.12f}"])
    lines.extend(" ".join(f"{v:.9f}" for v in row.ravel()) for row in channels)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")

    # Reconstruct after the actual on-disk decimal precision, separately from
    # extraction, so a unit/offset/channel-order mistake is observable.
    disk_lines = path.read_text(encoding="ascii").splitlines()
    disk_channels = np.array([[float(v) for v in line.split()] for line in disk_lines[-len(world):]])
    disk_channels = disk_channels.reshape(len(world), len(names), 6)
    by_joint = np.empty_like(disk_channels)
    by_joint[:, order] = disk_channels
    rebuilt = np.tile(np.eye(4), (len(world), len(names), 1, 1))
    disk_rotations = Rotation.from_euler("ZXY", np.deg2rad(by_joint[..., 3:].reshape(-1, 3))).as_matrix()
    disk_rotations = disk_rotations.reshape(len(world), len(names), 3, 3)
    for j, p in enumerate(parents):
        local = np.tile(np.eye(4), (len(world), 1, 1))
        local[:, :3, :3] = disk_rotations[:, j]
        local[:, :3, 3] = by_joint[:, j, :3] / 100
        rebuilt[:, j] = local if p < 0 else rebuilt[:, p] @ local
    error_m = float(np.max(np.linalg.norm(rebuilt[..., :3, 3] - positions, axis=-1)))
    # Float32 FK may accumulate a little orthogonality error; Euler conversion
    # projects it onto SO(3), so compare matrix entries with a matching tolerance.
    rotation_error = float(np.max(np.abs(rebuilt[..., :3, :3] - rotations)))
    if error_m > 1e-4 or rotation_error > 2e-4:
        raise RuntimeError(f"BVH reconstruction mismatch: {error_m:g}m, rotation {rotation_error:g}")
    return {
        "format": "motion_workbench_soma_bvh_v1", "unit": "centimeters", "unit_scale_m": 0.01,
        "up_axis": "Y", "forward_axis": "Z", "handedness": "right",
        "frames": len(world), "fps": fps, "root": names[0], "joint_count": len(names),
        "joint_names": list(names), "parents": parents.tolist(), "channel_order": "XYZposition_ZXYrotation",
        "position_channel_convention": "absolute_parent_local_blender_bvh",
        "source_rest_world_rotations": {name: rest[j, :3, :3].tolist() for j, name in enumerate(names)},
        "source_rest_world_positions": {name: (rest[j, :3, 3] * 100).tolist() for j, name in enumerate(names)},
        "source_rest_world_positions_unit": "centimeters",
        "hierarchy_channel_joint_order": [names[j] for j in order],
        "max_bvh_fk_position_error_m": error_m, "max_bvh_fk_rotation_matrix_error": rotation_error,
        "bvh_sha256": sha256(path),
    }


def export(input_path, assets, output_path, fps):
    import numpy as np
    import soma
    import torch
    from gem.common.soma import SomaFixedGs1BodyAdapter, SomaRuntimeConfig
    from gem.inference.timing import prediction_fps

    if soma.__version__ != "0.3.3":
        raise ValueError(f"Expected py-soma-x==0.3.3, got {soma.__version__}")
    input_path, output_path = Path(input_path).resolve(), Path(output_path).resolve()
    # Real video caches also contain numeric NumPy tracking metadata. Permit
    # only the reconstruction symbols and dtypes emitted by this GEM release;
    # keep weights_only and reject object arrays or arbitrary pickle globals.
    numpy_core = np._core if hasattr(np, "_core") else np.core
    numeric_globals = [bytes, numpy_core.multiarray._reconstruct, np.ndarray, np.dtype]
    numeric_globals.extend(type(np.dtype(kind)) for kind in (np.float32, np.int32, np.int64, np.bool_))
    # safe_globals removes its own additions on exit. Do not remove symbols
    # which a caller had already registered before this scoped load.
    existing = torch.serialization.get_safe_globals()
    additions = [symbol for symbol in numeric_globals if symbol not in existing]
    with torch.serialization.safe_globals(additions):
        result = torch.load(input_path, map_location="cpu", weights_only=True)
    if not isinstance(result, dict) or "pred_soma_params_global" not in result:
        raise ValueError("Expected current GEM-X Motion output with pred_soma_params_global")
    actual_fps = prediction_fps(result)
    if not math.isclose(float(actual_fps), fps, abs_tol=1e-4, rel_tol=0):
        raise ValueError(f"Source is {actual_fps}Hz; --fps {fps} would retime rather than resample")
    params = result["pred_soma_params_global"]
    params = {key: torch.as_tensor(params[key], dtype=torch.float32) for key in
              ("global_orient", "body_pose", "identity_coeffs", "scale_params", "transl")}
    if params["body_pose"].shape[0] != 1:
        raise ValueError("Export exactly one person/sequence per invocation")
    lengths = torch.as_tensor(result["length"]).reshape(-1)
    if lengths.numel() != 1 or int(lengths[0]) != params["body_pose"].shape[1]:
        raise ValueError("Export one unpadded sequence; result.length must match the pose")
    body = SomaFixedGs1BodyAdapter(SomaRuntimeConfig(
        data_root=str(Path(assets).resolve()), device="cpu", lod="xlo", mode="dense",
        apply_correctives=False, enable_procedural_transforms=True, procedural_reference="runtime",
    ))
    with torch.inference_mode():
        posed = body(**params, return_joints_only=True)
        # Retain the same predicted identity/scales, using the exact official
        # adapter reference convention and sequence shape averaging.
        neutral = {**params, "global_orient": torch.zeros_like(params["global_orient"]),
                   "body_pose": torch.zeros_like(params["body_pose"]), "transl": torch.zeros_like(params["transl"])}
        rest = body(**neutral, return_joints_only=True)
    runtime = body.runtime
    names = tuple(runtime.public_joint_names)
    parents = np.asarray(runtime.output_joint_parent_ids.cpu(), dtype=np.int64)
    if names[0] != "Root" or names[1] != "Hips" or len(names) != 78:
        raise ValueError(f"Unexpected SOMA public rig: {names[:2]}, {len(names)} joints")
    world = posed["transforms"][0, :, 1:].cpu().numpy()
    rest_world = rest["transforms"][0, 0, 1:].cpu().numpy()
    if world.shape[1] != 77:
        raise ValueError("SOMA must return 78 public transforms including virtual Root")
    info = write_motion_bvh(output_path, names[1:], parents[1:] - 1, world, rest_world, fps)
    info.update(input=str(input_path), input_sha256=sha256(input_path), soma_version=soma.__version__,
                gem_source_commit="20555befe2fbbe5b2015e1fff7ff214dc18ea9d6",
                coordinate_source=result.get("coordinates", {}).get("global"),
                postprocessed=bool(result.get("postprocessing")), script_sha256=sha256(Path(__file__)))
    sidecar = output_path.with_suffix(output_path.suffix + ".json")
    sidecar.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "sidecar": str(sidecar), "frames": info["frames"],
                      "max_bvh_fk_position_error_m": info["max_bvh_fk_position_error_m"]}, ensure_ascii=False))
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fps", type=float, default=30)
    args = parser.parse_args()
    export(args.input, args.assets, args.output, args.fps)


if __name__ == "__main__":
    main()
