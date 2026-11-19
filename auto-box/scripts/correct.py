# -*- coding: utf-8 -*-
"""Correction pass: anchor edit -> affected frames -> patch pose -> refill -> render.

Run this after someone (a human or the vision agent) decides that an identity
anchor is wrong.  Only the frames whose actor->track mapping changed are
re-inferred; everything else is reused from the existing pose result.
"""
import argparse
import json
import os
import subprocess

from paths import load_config

HERE = os.path.dirname(os.path.abspath(__file__))
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
    ap.add_argument("--detections", required=True)
    ap.add_argument("--anchors-old", required=True)
    ap.add_argument("--anchors-new", required=True,
                    help="produced by anchor_edit.py")
    ap.add_argument("--pose-existing", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--frames", default="", help="comma separated render frames")
    args = ap.parse_args()

    cfg = load_config(args.config)
    os.makedirs(args.work, exist_ok=True)

    deltas = os.path.join(args.work, "deltas.json")
    run([cfg["python"], os.path.join(HERE, "diff_anchors.py"),
         "--old", args.anchors_old, "--new", args.anchors_new,
         "--detections", args.detections, "--out", deltas])
    n = len(json.load(open(deltas, encoding="utf-8"))["deltas"])
    print("changed frames:", n)
    if n == 0:
        print("nothing to patch")
        return

    prop = os.path.join(args.work, "proposal_new.json")
    run([cfg["python"], os.path.join(cfg["official_scripts"], "actor_identity_proposal.py"),
         "--detections", args.detections, "--anchors", args.anchors_new,
         "--output", prop])
    mapping = os.path.join(args.work, "mapping_new.json")
    run([cfg["python"], os.path.join(HERE, "build_candidate.py"), prop, mapping])

    patched = os.path.join(args.work, "pose_patched.pt")
    run([cfg["python"], os.path.join(HERE, "pose_patch.py"),
         "--config", args.config, "--detections", args.detections,
         "--mapping", mapping, "--deltas", deltas,
         "--pose-existing", args.pose_existing, "--out", patched,
         "--work", os.path.join(args.work, "patch")])

    filled = os.path.join(args.work, "filled.json")
    run([cfg["python"], os.path.join(HERE, "fill_pose_gaps.py"),
         "--pose", patched, "--out", filled,
         "--max-travel", "0.30", "--max-hold", "12"])
    print("fill ok")

    if args.frames:
        run([cfg["python"], os.path.join(HERE, "render_skeletons.py"),
             "--filled", filled, "--source", cfg["source"],
             "--intrinsics", cfg["intrinsics"],
             "--out", os.path.join(args.work, "skel"),
             "--frames", args.frames])
        print("render ok")

    print(json.dumps({"patched": patched, "filled": filled}, ensure_ascii=False))


if __name__ == "__main__":
    main()
