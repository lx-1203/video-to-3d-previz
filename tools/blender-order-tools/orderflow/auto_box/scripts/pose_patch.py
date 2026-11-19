# -*- coding: utf-8 -*-
"""Patch an existing pose result for only the frames an anchor edit touched."""
import argparse
import json
import os
import subprocess

from paths import load_config
import sys
from pathlib import Path

# official GEM-X wrapper scripts; path comes from config["official_scripts"]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    if r.returncode != 0:
        print(r.stdout[-1500:])
        print(r.stderr[-2500:])
        raise SystemExit("failed: " + " ".join(cmd[:3]))
    return r.stdout


def select_patch(mapping, deltas):
    changed = {(row["shot_id"], row["frame"]): set(row["removed"]) | set(row["added"]) for row in deltas}
    selected = []
    for frame in mapping["frames"]:
        affected = changed.get((frame["shot_id"], frame["frame"]), set())
        kept = [row for row in frame["mappings"]
                if row["actor_id"] in affected and row.get("track_id") is not None]
        if kept:
            selected.append(dict(frame, expected_actor_ids=[row["actor_id"] for row in kept], mappings=kept))
    return changed, {"actor_ids": sorted({actor for frame in selected for actor in frame["expected_actor_ids"]}),
                     "frames": selected}


def merge_patch(baseline, patch, changed, selected):
    affected = {frame: actors for (_, frame), actors in changed.items()}
    if len(affected) != len(changed):
        raise ValueError("patch frames must be unique on the source timeline")
    expected = {frame["frame"]: set(frame["expected_actor_ids"]) for frame in selected["frames"]}
    patch_rows = {int(frame["frame_id"]): frame for frame in patch}
    actual = {frame: {str(row["track_id"]) for row in pose["instances"]} for frame, pose in patch_rows.items()}
    if actual != expected or len(patch_rows) != len(patch) or any(
            len(actual[frame]) != len(row["instances"]) for frame, row in patch_rows.items()):
        raise ValueError("GEM patch output coverage differs from the planned actor/frames")

    frames = {int(frame["frame_id"]): frame for frame in baseline}
    if len(frames) != len(baseline):
        raise ValueError("baseline pose has duplicate source frames")
    log = []
    for frame, actors in sorted(affected.items()):
        old, new = frames.get(frame), patch_rows.get(frame)
        if old is None and new is None:
            continue
        before = old["instances"] if old is not None else []
        instances = [row for row in before if str(row["track_id"]) not in actors]
        if new is not None:
            instances.extend(new["instances"])
        frames[frame] = dict(old if old is not None else new, instances=instances)
        log.append({"frame": frame, "before": [row["track_id"] for row in before],
                    "after": [row["track_id"] for row in instances]})
    return [frames[index] for index in sorted(frames)], log


def infer_patch(cfg, args, mini_map):
    map_path = os.path.join(args.work, "mapping_mini.json")
    json.dump(mini_map, open(map_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    det_path = os.path.join(args.work, "frames_mini.jsonl")
    tracks = {(frame["shot_id"], frame["frame"]): {row["track_id"] for row in frame["mappings"]}
              for frame in mini_map["frames"]}
    with open(args.detections, encoding="utf-8") as src, \
            open(det_path, "w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            fr = json.loads(line)
            ids = tracks.get((fr.get("window"), fr["source_zero_based"]))
            if ids is None:
                continue
            fr = dict(fr)
            fr["observed_tracks"] = [t for t in fr.get("observed_tracks", [])
                                     if t.get("window_local_track_id") in ids]
            dst.write(json.dumps(fr, ensure_ascii=False) + "\n")
    print("mini frames written:", len(tracks))

    cand = os.path.join(args.work, "candidate_mini.json")
    run([cfg["python"], os.path.join(cfg["official_scripts"], "multi_actor.py"),
         "--detections", det_path, "--mapping", map_path, "--output", cand])
    print("candidate ok")

    plan_work = os.path.join(args.work, "pose")
    run([cfg["python"], os.path.join(cfg["official_scripts"], "multi_actor_pose.py"), "plan",
         "--candidate", cand, "--source", cfg["source"],
         "--source-sha256", cfg["source_sha256"],
         "--intrinsics", cfg["intrinsics"], "--python", cfg["pose_python"],
         "--repo", cfg["gem_repo"], "--checkpoint", cfg["checkpoint"],
         "--soma-assets", cfg["soma_assets"], "--device", "cpu",
         "--allow-inferred", "--work", plan_work])
    print("plan ok")
    run([cfg["python"], os.path.join(cfg["official_scripts"], "multi_actor_pose.py"), "execute",
         "--plan", os.path.join(plan_work, "plan.json")])
    print("pose ok")

    return os.path.join(plan_work, "pose.pt")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--detections", required=True)
    ap.add_argument("--mapping", required=True, help="mapping rebuilt from the new anchors")
    ap.add_argument("--deltas", required=True)
    ap.add_argument("--pose-existing", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args()

    with open(args.deltas, encoding="utf-8-sig") as source:
        deltas = json.load(source)["deltas"]
    with open(args.mapping, encoding="utf-8-sig") as source:
        changed, mini_map = select_patch(json.load(source), deltas)
    print("changed frames:", len(changed), "frames requiring inference:", len(mini_map["frames"]))
    os.makedirs(args.work, exist_ok=True)
    result = infer_patch(load_config(args.config), args, mini_map) if mini_map["frames"] else None

    import torch

    full = torch.load(args.pose_existing, map_location="cpu", weights_only=False)
    mini = torch.load(result, map_location="cpu", weights_only=False)["frames"] if result else []
    full["frames"], log = merge_patch(full["frames"], mini, changed, mini_map)
    torch.save(full, args.out)
    json.dump({"patched": args.out, "frames": len(log), "log": log},
              open(args.out + ".log.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps({"patched_frames": len(log), "out": args.out},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
