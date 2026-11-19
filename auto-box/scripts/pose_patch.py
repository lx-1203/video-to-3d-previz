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

    cfg = load_config(args.config)
    deltas = json.load(open(args.deltas, encoding="utf-8-sig"))["deltas"]
    changed = {(d["shot_id"], d["frame"]) for d in deltas}
    actors = {a for d in deltas for a in list(d["removed"]) + list(d["added"])}
    print("changed frames:", len(changed), "actors:", sorted(actors))

    mapping = json.load(open(args.mapping, encoding="utf-8-sig"))
    keep_rows = [f for f in mapping["frames"]
                 if (f["shot_id"], f["frame"]) in changed]
    mini_map = {"actor_ids": sorted(actors), "frames": keep_rows}
    os.makedirs(args.work, exist_ok=True)
    map_path = os.path.join(args.work, "mapping_mini.json")
    json.dump(mini_map, open(map_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    det_path = os.path.join(args.work, "frames_mini.jsonl")
    wanted = {f["frame"] for f in keep_rows}
    with open(args.detections, encoding="utf-8") as src, \
            open(det_path, "w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            fr = json.loads(line)
            if (fr.get("window"), fr["source_zero_based"]) not in changed:
                continue
            ids = {m["track_id"] for f in keep_rows
                   if f["frame"] == fr["source_zero_based"]
                   for m in f["mappings"] if m.get("track_id") is not None}
            fr = dict(fr)
            fr["observed_tracks"] = [t for t in fr.get("observed_tracks", [])
                                     if t.get("window_local_track_id") in ids]
            dst.write(json.dumps(fr, ensure_ascii=False) + "\n")
    print("mini frames written:", len(wanted))

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

    import torch
    full = torch.load(args.pose_existing, map_location="cpu", weights_only=False)
    mini = torch.load(os.path.join(plan_work, "pose.pt"), map_location="cpu",
                      weights_only=False)
    mini_by_frame = {fr["frame_id"]: fr for fr in mini["frames"]}
    log = []
    for fr in full["frames"]:
        fid = fr["frame_id"]
        if fid not in wanted:
            continue
        before = [i["track_id"] for i in fr["instances"]]
        fr["instances"] = [i for i in fr["instances"] if i["track_id"] not in actors]
        src = mini_by_frame.get(fid)
        if src:
            fr["instances"].extend(src["instances"])
        log.append({"frame": int(fid), "before": before,
                    "after": [i["track_id"] for i in fr["instances"]]})

    torch.save(full, args.out)
    json.dump({"patched": args.out, "frames": len(log), "log": log},
              open(args.out + ".log.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps({"patched_frames": len(log), "out": args.out},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
