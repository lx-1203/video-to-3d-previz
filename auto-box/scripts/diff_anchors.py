# -*- coding: utf-8 -*-
"""Compare two anchor files and report exactly which frames changed identity.

This is the piece that removes the manual "which frames are affected" step: an
anchor edit turns into a frame-level delta that the pose stage can patch.
"""
import argparse
import json
from collections import defaultdict


def load_map(path):
    d = json.load(open(path, encoding="utf-8-sig"))
    out = defaultdict(dict)          # (shot, frame) -> actor -> track
    for a in d["anchors"]:
        for f in range(a["start_frame"], a["end_frame"] + 1):
            out[(a["shot_id"], f)][a["actor_id"]] = a["track_id"]
    return out


def frames_of(path):
    shots = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        fr = json.loads(line)
        shots.setdefault(fr.get("window") or fr.get("shot_id"), set()).add(
            fr["source_zero_based"])
    return shots


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--detections", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    old, new = load_map(args.old), load_map(args.new)
    shots = frames_of(args.detections)
    deltas = []
    per_shot = defaultdict(lambda: {"changed": [], "actors": set()})

    for shot, frames in shots.items():
        for f in sorted(frames):
            key = (shot, f)
            a, b = old.get(key, {}), new.get(key, {})
            removed = {k: v for k, v in a.items() if b.get(k) != v}
            added = {k: v for k, v in b.items() if a.get(k) != v}
            if not removed and not added:
                continue
            deltas.append({"shot_id": shot, "frame": f,
                           "removed": removed, "added": added})
            per_shot[shot]["changed"].append(f)
            per_shot[shot]["actors"].update(removed, added)

    summary = {s: {"changed_frames": v["changed"],
                   "count": len(v["changed"]),
                   "actors": sorted(v["actors"])}
               for s, v in per_shot.items()}
    json.dump({"deltas": deltas, "summary": summary},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(json.dumps({s: {"frames": v["count"], "actors": v["actors"]}
                      for s, v in summary.items()}, ensure_ascii=False))
    for d in deltas[:10]:
        print("  f%-4d -%s +%s" % (d["frame"], d["removed"], d["added"]))
    print("written", args.out)


if __name__ == "__main__":
    main()
