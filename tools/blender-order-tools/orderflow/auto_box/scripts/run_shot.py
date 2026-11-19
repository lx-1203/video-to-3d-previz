# -*- coding: utf-8 -*-
"""First pass for one shot: anchors -> candidate -> pose -> fill -> render.

Detection and the identity review are separate steps on purpose: boxes come from
the detector, identity comes from a human or a vision agent looking at frames.
"""
import argparse
import json
import os
import subprocess

from paths import load_config

# official GEM-X wrapper scripts; path comes from config["official_scripts"]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        print(r.stdout[-1500:])
        print(r.stderr[-2500:])
        raise SystemExit("failed: " + " ".join(str(c) for c in cmd[:3]))
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--shot", required=True, help="shot_id")
    ap.add_argument("--detections", required=True)
    ap.add_argument("--anchors", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--frames", default="", help="comma separated render frames")
    args = ap.parse_args()

    cfg = load_config(args.config)
    os.makedirs(args.work, exist_ok=True)

    prop = os.path.join(args.work, "proposal.json")
    run([cfg["python"], os.path.join(cfg["official_scripts"], "actor_identity_proposal.py"),
         "--detections", args.detections, "--anchors", args.anchors,
         "--output", prop])
    print("proposal ok")

    mapping = os.path.join(args.work, "mapping.json")
    run([cfg["python"], os.path.join(os.path.dirname(__file__), "build_candidate.py"),
         prop, mapping, '--shot', args.shot])
    print("mapping ok")

    cand = os.path.join(args.work, "candidate.json")
    run([cfg["python"], os.path.join(cfg["official_scripts"], "multi_actor.py"),
         "--detections", args.detections, "--mapping", mapping,
         "--output", cand])
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

    filled = os.path.join(args.work, "filled.json")
    run([cfg["python"], os.path.join(os.path.dirname(__file__), "fill_pose_gaps.py"),
         "--pose", os.path.join(plan_work, "pose.pt"), "--out", filled,
         "--max-travel", "0.30", "--max-hold", "12"])
    print("fill ok")

    if args.frames:
        run([cfg["python"], os.path.join(os.path.dirname(__file__), "render_skeletons.py"),
             "--filled", filled, "--source", cfg["source"],
             "--intrinsics", cfg["intrinsics"],
             "--out", os.path.join(args.work, "skel"),
             "--frames", args.frames])
        print("render ok")

    print(json.dumps({"pose": os.path.join(plan_work, "pose.pt"),
                      "filled": filled}, ensure_ascii=False))


if __name__ == "__main__":
    main()
