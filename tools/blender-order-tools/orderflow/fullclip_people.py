"""Construct metric, shot-local pose proposals for OrderFlow's editable scene pass.

CLI uses the external local pose Python. No model or client asset is bundled.
"""

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import tempfile

if __package__:
    # This backend is normally spawned as a script. Importing run from the add-on
    # also works in a Python environment which has its optional pose dependencies.
    from . import fullclip_people_faces as faces_backend
else:
    import fullclip_people_faces as faces_backend


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf8", dir=out.parent, delete=False) as f:
        scratch = Path(f.name)
        try:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except BaseException:
            f.close()
            scratch.unlink(missing_ok=True)
            raise
    try:
        os.replace(scratch, out)
    finally:
        scratch.unlink(missing_ok=True)


def run(
    video,
    detections,
    model,
    cuts,
    output,
    component_contract=None,
    face_model=None,
    head_model=None,
    track_model=None,
    face_cache=None,
    head_cache=None,
    observation_cache=None,
):
    if __package__:
        from . import fullclip_people_heads as heads_backend, fullclip_people_observe as observe
        from .fullclip_people_gaps import fill as fill_person_gaps
        from .fullclip_people_fragments import fill_stationary_fragments
        from .fullclip_people_partial import extend_partial_bodies
        from .fullclip_people_partial_pose import apply_partial_posture
        from .fullclip_people_support import analyze as analyze_support
        from .fullclip_people_posture import apply as posture_priors
        from .fullclip_people_temporal import stabilize as stabilize_motion
        from .fullclip_people_appearance import label as label_appearance
        from .fullclip_people_regiontrack import fill as fill_region
        from .fullclip_people_solve import read_profile, solve_track, stabilize_close_roll
        from .fullclip_people_tracks import assign, fill_short_gaps, merge_heads
    else:
        import fullclip_people_heads as heads_backend
        import fullclip_people_observe as observe
        from fullclip_people_gaps import fill as fill_person_gaps
        from fullclip_people_fragments import fill_stationary_fragments
        from fullclip_people_partial import extend_partial_bodies
        from fullclip_people_partial_pose import apply_partial_posture
        from fullclip_people_support import analyze as analyze_support
        from fullclip_people_posture import apply as posture_priors
        from fullclip_people_temporal import stabilize as stabilize_motion
        from fullclip_people_appearance import label as label_appearance
        from fullclip_people_regiontrack import fill as fill_region
        from fullclip_people_solve import read_profile, solve_track, stabilize_close_roll
        from fullclip_people_tracks import assign, fill_short_gaps, merge_heads
    sha = faces_backend.digest
    if isinstance(cuts, str):
        cuts = [int(k) for k in cuts.split(",")]
    data = [
        json.loads(line) for line in Path(detections).read_text(encoding="utf-8-sig").splitlines()
    ]
    if (
        len(cuts) < 2
        or cuts[0] != 0
        or cuts[-1] != len(data)
        or any(a >= b for a, b in zip(cuts, cuts[1:]))
    ):
        raise ValueError("Cuts must partition every detector frame, with an exclusive end")
    source_sha, det_sha, pose_sha = sha(video), sha(detections), sha(model)
    receipt = read(Path(detections).with_name("receipt.json"))
    if (
        not receipt.get("requested_ranges_complete")
        or receipt["inputs"]["source"]["sha256"] != source_sha
        or receipt["outputs"]["frames.jsonl"] != det_sha
    ):
        raise ValueError("OrderFlow detector receipt disagrees with current source/observations")
    face_sha = sha(face_model) if face_model else None
    head_sha = sha(head_model) if head_model else None
    expected = {
        "schema": "orderflow.observations.v1",
        "source_sha256": source_sha,
        "detection_sha256": det_sha,
        "pose_model_sha256": pose_sha,
        "face_model_sha256": face_sha,
        "observer_sha256": sha(observe.__file__),
    }
    Path(output).resolve().parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="fullclip-people-", dir=Path(output).resolve().parent
    ) as temp:
        face_data = None
        if face_model:
            face_path = Path(face_cache) if face_cache else Path(temp) / "faces.json"
            if face_path.is_file():
                face_data = read(face_path)
            else:
                face_data = faces_backend.scan(video, face_model, face_path)
            if (
                face_data.get("source_sha256") != source_sha
                or face_data.get("model_sha256") != face_sha
            ):
                raise ValueError("Face cache belongs to a different source/model")
        head_data = None
        if head_model:
            head_path = Path(head_cache) if head_cache else Path(temp) / "heads.json"
            if head_path.is_file():
                head_data = read(head_path)
            else:
                head_data = heads_backend.scan(
                    video, head_model, detections, face_path if face_model else None, head_path
                )
            if (
                head_data.get("source_sha256") != source_sha
                or head_data.get("model_sha256") != head_sha
            ):
                raise ValueError("Head cache belongs to a different source/model")
        if observation_cache and Path(observation_cache).is_file():
            obs = read(observation_cache)
            if any(obs.get(k) != v for k, v in expected.items()):
                raise ValueError("Observation cache provenance differs from the current inputs")
            width, height, frames = (obs[k] for k in ("width", "height", "frames"))
        else:
            width, height, frames = observe.measure_frames(
                video, model, data, face_data["frames"] if face_model else None, cuts
            )
            if observation_cache:
                write(observation_cache, dict(expected, width=width, height=height, frames=frames))
    if len(frames) != len(data):
        raise ValueError("Observation cache length mismatch")
    for shot, (start, stop) in enumerate(zip(cuts, cuts[1:]), 1):
        for i in range(start, stop):
            if (frames[i]["source_frame"], frames[i]["pts"], frames[i]["time_base"]) != (
                i,
                data[i]["pts"],
                data[i]["time_base"],
            ):
                raise ValueError("Observation frame map differs from the detector")
            frames[i]["shot_id"] = f"S{shot:02d}"
    merge_heads(frames, head_data["frames"] if head_data else None, width, height)
    assign(frames)
    fill_stationary_fragments(video, frames, width)
    fill_person_gaps(video, frames, width)
    fill_region(video, frames, track_model, width, height)
    fill_short_gaps(video, frames, width, height)
    partial_tracks = extend_partial_bodies(video, frames, width, height, model, head_model)
    groups = defaultdict(list)
    for row in frames:
        for actor in row["actors"]:
            groups[actor["track_id"]].append((row["source_frame"], actor))
    analyze_support(video, frames, width, height)
    posture_priors(video, frames, width)
    stabilize_close_roll(frames, height)
    profile = read_profile(component_contract)
    stabilize_motion(video, frames, width, height, profile)
    for track, entries in groups.items():
        solve_track(entries, profile)
        apply_partial_posture(entries, profile)
        for _, actor in entries:
            actor.pop("interpolation_endpoints", None)
            actor["identity_state"] = "shot_local_unverified"
            actor["renderable"] = len(entries) >= 3 or len(data) < 3
    # Machine consumers iterate frames. Keep rejected ephemeral proposals in
    # the evidence list, with no duplicate or one-frame rig keyed into the scene.
    for row in frames:
        row["rejected_actors"] = [a for a in row["actors"] if not a["renderable"]]
        row["actors"] = [a for a in row["actors"] if a["renderable"]]
    label_appearance(video, frames)
    coverage = {}
    for shot, (start, end) in enumerate(zip(cuts, cuts[1:]), 1):
        part = frames[start:end]
        actors = [a for r in part for a in r["actors"]]
        coverage[f"S{shot:02d}"] = {
            "frames": len(part),
            "frames_with_actor": sum(any(a["renderable"] for a in r["actors"]) for r in part),
            "bbox_sources": dict(Counter(a["bbox_kind"] for a in actors)),
            "unknown_frames": [
                r["source_frame"] for r in part if not any(a["renderable"] for a in r["actors"])
            ],
        }
    out = {
        "schema": "orderflow.fullclip_people.v1",
        "source_sha256": source_sha,
        "width": width,
        "height": height,
        "cuts": cuts,
        "source_frame_count": len(frames),
        "frames": frames,
        "coverage": coverage,
        "partial_body_extensions": partial_tracks,
        "pose_model_sha256": pose_sha,
        "face_model_sha256": face_sha,
        "head_model_sha256": head_sha,
        "track_model_sha256": sha(track_model) if track_model else None,
        "detector_data_sha256": det_sha,
        "component_contract_sha256": sha(component_contract) if component_contract else None,
        "rig_lengths": profile,
        "coordinate_system": "root at hip midpoint; x screen right, y away from camera, z screen up",
        "scale_units": "pixels_per_meter is source pixels per original, unscaled template meter",
        "llm_mode": "off",
        "ground_truth_visibility": False,
        "depth_measured": False,
        "score_is_calibrated_probability": False,
        "quality_state": "internal_candidate",
        "backend_sha256": {
            p.name: sha(p) for p in Path(__file__).parent.glob("fullclip_people*.py")
        },
    }
    write(output, out)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("video", "detections", "model", "cuts", "output"):
        p.add_argument("--" + flag, required=True)
    for flag in (
        "component-contract",
        "face-model",
        "head-model",
        "track-model",
        "face-cache",
        "head-cache",
        "observation-cache",
    ):
        p.add_argument("--" + flag)
    result = run(**vars(p.parse_args()))
    print(json.dumps(result["coverage"]), flush=True)
