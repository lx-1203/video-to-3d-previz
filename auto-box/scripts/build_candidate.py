# -*- coding: utf-8 -*-
"""Build a pose candidate: only actors that actually have a box on each frame.

The candidate is what the pose planner accepts, so an actor with no box must be
left out of that frame's `expected_actor_ids` instead of being recorded as a
missing detection.
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("proposal")
    ap.add_argument("out")
    ap.add_argument("--shot", default="", help="default: every shot in the file")
    args = ap.parse_args()

    prop = json.load(open(args.proposal, encoding="utf-8-sig"))
    frames, present = [], {}
    for fr in prop["mapping"]["frames"]:
        if args.shot and fr["shot_id"] != args.shot:
            continue
        keep = [m for m in fr["mappings"] if m.get("track_id") is not None]
        frames.append({"shot_id": fr["shot_id"], "frame": fr["frame"],
                       "expected_actor_ids": [m["actor_id"] for m in keep],
                       "mappings": keep})
        for m in keep:
            present[m["actor_id"]] = present.get(m["actor_id"], 0) + 1

    json.dump({"actor_ids": prop["mapping"]["actor_ids"], "frames": frames},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("frames:", len(frames), "boxes per actor:", present)
    print("written", args.out)


if __name__ == "__main__":
    main()
