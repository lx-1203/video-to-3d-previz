"""Background worker for OrderFlow's full-video preview operator."""

import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow import core, fullclip_inputs


def run_process(argv, logfile, env):
    with Path(logfile).open("wb") as output:
        return subprocess.run(
            list(map(str, argv)),
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).returncode


def need(argv, logfile, env):
    code = run_process(argv, logfile, env)
    if code:
        raise RuntimeError(f"{Path(logfile).name}: process exit {code}. See {logfile}")


def config_paths(path):
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    keys = (
        "scene_runtime",
        "pose_python",
        "pose_model",
        "face_model",
        "head_model",
        "track_model",
        "detector_python",
        "detector_model",
        "detector_repo",
        "template",
        "component_contract",
    )
    if cfg.get("scene_runner"):
        keys = (*keys, "scene_runner", "scene_job_root")
    result = {}
    for key in keys:
        value = (path.parent / cfg[key]).resolve()
        if not (value.is_dir() if key in ("scene_job_root", "detector_repo") else value.is_file()):
            raise ValueError(f"Missing {key}: {value}")
        result[key] = str(value)
    result["measurement_inputs"] = fullclip_inputs.read_input_table(path, cfg)
    result["height_m"] = float(cfg.get("height_m", 1.75))
    if not 0.1 < result["height_m"] < 5:
        raise ValueError("Unusable neutral template height")
    return result


def build(job_path):
    from orderflow.fullclip_timeline import inspect_source
    from orderflow import motion

    job = json.loads(job_path.read_bytes())
    root = job_path.parent
    source = Path(job["source"])
    cfg = config_paths(Path(job["profile"]))
    state = {
        "status": "running",
        "stage": "preflight",
        "language_model_mode": "off",
        "source": str(source),
        "source_sha256": core.sha256(source),
        "delivery_ready": False,
    }
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUTF8="1",
        HF_HUB_OFFLINE="1",
        WANDB_MODE="disabled",
        OMP_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="2",
        MKL_NUM_THREADS="2",
    )
    local_temp = root / "tmp"
    local_temp.mkdir(exist_ok=True)
    env.update(TMP=str(local_temp), TEMP=str(local_temp), TMPDIR=str(local_temp))

    def status(stage):
        state["stage"] = stage
        core.atomic_json(root / "status.json", state)

    def execute(suffix, args, log):
        need([*suffix, *args], root / log, env)

    package = Path(__file__).resolve().parent
    status("clock")
    try:
        gate = motion._gate(
            {"media_python": job["media_python"], "motion_update_script": job["update_script"]}
        )
        core.atomic_json(root / "update.json", gate)
        timeline = inspect_source(source)
        core.atomic_json(root / "timeline.json", timeline)
        ends = [0, *timeline["cuts"], timeline["frames"]]
        shots = [
            dict(shot_id=f"shot{i + 1}", source_zero_start=a, source_zero_end=b - 1)
            for i, (a, b) in enumerate(zip(ends, ends[1:]))
        ]
        core.atomic_json(root / "shots.json", shots)
        state["frames"] = timeline["frames"]
        state["cuts"] = timeline["cuts"]
        state["reused_measurement_sha256"] = {
            k: core.sha256(v) for k, v in cfg["measurement_inputs"].items()
        }

        def measure_people():
            prepared = cfg["measurement_inputs"]
            if "detections" in prepared:
                fullclip_inputs.detection_input(
                    prepared["detections"],
                    root / "detections",
                    state["source_sha256"],
                    cfg,
                    package,
                    shots,
                    timeline,
                )
            else:
                execute(
                    [
                        cfg["detector_python"],
                        "-B",
                        package / "auto_box/scripts/multi_actor_detect_t4.py",
                    ],
                    [
                        "--source",
                        source,
                        "--source-sha256",
                        state["source_sha256"],
                        "--repo",
                        cfg["detector_repo"],
                        "--model",
                        cfg["detector_model"],
                        "--model-sha256",
                        core.sha256(cfg["detector_model"]),
                        "--shots",
                        root / "shots.json",
                        "--output-new-dir",
                        root / "detections",
                        "--threads2",
                    ],
                    "detect.log",
                )
            for name, model in [("faces", "face_model"), ("heads", "head_model")]:
                if name in prepared:
                    fullclip_inputs.image_observations(
                        prepared[name],
                        root / (name + ".json"),
                        state["source_sha256"],
                        cfg[model],
                        timeline,
                    )
            if "pose_observations" in prepared:
                # The pose worker verifies the source, range, detector, model and
                # observer-code digests before accepting any raw joint observations.
                shutil.copyfile(prepared["pose_observations"], root / "pose-observations.json")
            execute(
                [cfg["pose_python"], "-B", package / "fullclip_people.py"],
                [
                    "--video",
                    source,
                    "--detections",
                    root / "detections/frames.jsonl",
                    "--model",
                    cfg["pose_model"],
                    "--face-model",
                    cfg["face_model"],
                    "--head-model",
                    cfg["head_model"],
                    "--track-model",
                    cfg["track_model"],
                    "--head-cache",
                    root / "heads.json",
                    "--observation-cache",
                    root / "pose-observations.json",
                    "--face-cache",
                    root / "faces.json",
                    "--component-contract",
                    cfg["component_contract"],
                    "--cuts",
                    ",".join(map(str, ends)),
                    "--output",
                    root / "people.json",
                ],
                "pose.log",
            )

        def measure_sets():
            if "scene_runner" not in cfg:
                from orderflow.fullclip_scene_entry import run_scene_jobs

                spans = [(s["source_zero_start"], s["source_zero_end"]) for s in shots]
                records = run_scene_jobs(
                    source, spans, cfg["scene_runtime"], root / "native-scene-jobs"
                )
                core.atomic_json(root / "scene-jobs.json", records)
                return records
            directory = Path(cfg["scene_job_root"])
            records = []
            for i, shot in enumerate(shots):
                start, end = shot["source_zero_start"], shot["source_zero_end"]
                work = directory / ("orderflow_" + uuid.uuid4().hex)
                args = [
                    job["media_python"],
                    "-B",
                    cfg["scene_runner"],
                    "scene",
                    "--video",
                    source,
                    "--job",
                    work,
                    "--runtime",
                    cfg["scene_runtime"],
                    "--start-frame",
                    start,
                    "--frame-count",
                    end - start + 1,
                ]
                rc = run_process(args, root / f"scene-{i + 1}.log", env)
                records.append(dict(first=start, last=end, job=str(work), exit_code=rc))
                core.atomic_json(root / "scene-jobs.json", records)
            return records

        # The depth and pose runtimes can each consume several GiB on CPU.
        status("local_detection_and_pose")
        measure_people()
        status("local_camera_and_depth")
        if "scene_jobs" in cfg["measurement_inputs"]:
            fullclip_inputs.camera_jobs(
                cfg["measurement_inputs"]["scene_jobs"], root / "scene-jobs.json", shots
            )
        else:
            measure_sets()
        status("scene_geometry")
        from orderflow.fullclip_scene_measure import measure_scene

        geometry = measure_scene(
            source,
            root / "scene-jobs.json",
            root / "set",
            detections=root / "detections/frames.jsonl",
            geometry_runtime=cfg["scene_runtime"],
            heads_json=root / "heads.json",
            faces_json=root / "faces.json",
        )
        from orderflow.fullclip_body_size import measure_body_sizes

        status("measuring_visible_props")
        from orderflow.fullclip_props import measure_props

        measure_props(
            source, root / "people.json", root / "props.json", timeline=root / "timeline.json"
        )
        measure_body_sizes(
            source,
            root / "people.json",
            root / "body-sizes.json",
            heads=root / "heads.json",
            props=root / "props.json",
        )
        from orderflow.fullclip_guardrail import measure_guardrails

        measure_guardrails(source, root / "people.json", root / "guardrails.json")
        params = dict(
            **job, **cfg, scene_manifest=str(geometry), source_sha256=state["source_sha256"]
        )
        core.atomic_json(root / "compose.json", params)
        status("editable_scene")
        blender = [
            job["blender"],
            "-b",
            "-Y",
            "--factory-startup",
            "-t",
            "3",
            "--python-exit-code",
            "7",
        ]
        execute(
            blender,
            ["-P", package / "fullclip_compose.py", "--", root / "compose.json"],
            "compose.log",
        )
        blend = root / "preview.blend"
        if not blend.is_file():
            raise ValueError("Missing editable candidate")
        state["scene_sha256"] = core.sha256(blend)
        status("reopen_pose_audit")
        execute(
            [job["blender"], "-b", "-Y", "-t", "3", blend, "--python-exit-code", "7"],
            ["-P", package / "fullclip_pose_audit.py", "--", root],
            "pose-audit.log",
        )
        status("native_render")
        execute(
            [job["blender"], "-b", "-Y", "-t", "3", blend],
            ["-o", root / "frames/######", "-F", "PNG", "-a"],
            "render.log",
        )
        frames = root / "frames"
        if sorted(p.name for p in frames.glob("*.png")) != [
            f"{n:06}.png" for n in range(1, timeline["frames"] + 1)
        ]:
            raise ValueError("Incomplete native Blender render")
        fps = Fraction(timeline["fps"])
        status("synchronized_preview")
        h = job["height"]
        w = max(2, round(h * timeline["width"] / timeline["height"] / 2) * 2)
        ffargs = [
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source,
            "-framerate",
            str(fps),
            "-start_number",
            1,
            "-i",
            frames / "%06d.png",
            "-filter_complex",
            f"[0:v]scale={w}:{h},setsar=1[top];[1:v]setsar=1[lower];[top][lower]vstack=inputs=2:shortest=1,format=yuv420p[v]",
            "-map",
            "[v]",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            root / "comparison.mp4",
            "-map",
            "1:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            root / "white-model.mp4",
        ]
        execute([job["ffmpeg"]], ffargs, "encode.log")
        from orderflow.fullclip_verify import verify, verify_native_movie

        state["media"] = verify(source, root / "comparison.mp4", frames, timeline)
        state["native_media"] = verify_native_movie(
            source, root / "white-model.mp4", frames, timeline
        )
        if state["source_sha256"] != core.sha256(source) or state["scene_sha256"] != core.sha256(
            blend
        ):
            raise RuntimeError("Inputs changed during execution")
        state.update(
            status="internal_preview",
            preview=str(root / "comparison.mp4"),
            scene=str(blend),
            native_movie=str(root / "white-model.mp4"),
            native_movie_sha256=core.sha256(root / "white-model.mp4"),
            preview_sha256=core.sha256(root / "comparison.mp4"),
        )
        status("completed")
    except Exception as exc:
        state.update(status="failed", error=str(exc))
        core.atomic_json(root / "status.json", state)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    job_path = parser.parse_args().job
    try:
        build(job_path)
    except Exception as exc:
        prior = job_path.parent / "status.json"
        try:
            state = core.read_json(prior)
        except (OSError, ValueError):
            state = {}
        state.update(status="failed", delivery_ready=False, error=str(exc))
        core.atomic_json(prior, state)
        raise
