# -*- coding: utf-8 -*-
"""Turn a vision model's decisions into deterministic box overrides + anchors.

The model judges; this script computes.  Every applied decision keeps its
evidence string so the audit trail survives.
"""
import argparse
import json
import os
from collections import defaultdict

ACTIONS = {"keep", "drop", "shrink_bottom", "shrink_top", "grow_bottom",
           "same_as", "mark_missing", "assign_actor"}
BOX_ACTIONS = {"shrink_bottom", "shrink_top", "grow_bottom", "drop"}


def load_frames(path):
    rows = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            fr = json.loads(line)
            key = (fr.get("window") or fr.get("shot_id"), fr["source_zero_based"])
            rows[key] = fr
    return rows


def boxes_of(fr):
    out = {}
    for t in fr.get("observed_tracks") or fr.get("detections") or []:
        if t.get("observed") is False:
            continue
        tid = t.get("window_local_track_id", t.get("track_id"))
        box = t.get("box_xyxy") or t.get("box")
        if tid is None or not box or len(box) != 4:
            continue
        out[int(tid)] = [float(v) for v in box]
    return out


def validate(d, frames):
    errs = []
    action = d.get("action")
    if action not in ACTIONS:
        errs.append("unknown action %r" % action)
        return errs
    if not isinstance(d.get("evidence"), str) or len(d["evidence"].strip()) < 4:
        errs.append("missing evidence")
    if not isinstance(d.get("confidence"), (int, float)):
        errs.append("missing confidence")
    key = (d.get("shot_id"), d.get("frame"))
    if key not in frames:
        errs.append("frame not present in detections")
    if action in BOX_ACTIONS:
        if not isinstance(d.get("track_id"), int):
            errs.append("track_id required")
        elif key in frames and d["track_id"] not in boxes_of(frames[key]):
            errs.append("track not observed on that frame")
    if action in {"shrink_bottom", "shrink_top", "grow_bottom"}:
        amt = d.get("amount")
        if not isinstance(amt, (int, float)) or not (0 < amt <= 0.95):
            errs.append("amount must be in (0, 0.95]")
    if action == "same_as" and not isinstance(d.get("same_as_track"), int):
        errs.append("same_as_track required")
    if action in {"mark_missing", "assign_actor"} and not d.get("actor_id"):
        errs.append("actor_id required")
    if action == "assign_actor" and not isinstance(d.get("track_id"), int):
        errs.append("track_id required")
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--detections", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    frames = load_frames(args.detections)
    with open(args.decisions, encoding="utf-8-sig") as fh:
        payload = json.load(fh)
    decisions = payload.get("decisions", payload if isinstance(payload, list) else [])

    applied, rejected = [], []
    overrides = defaultdict(dict)          # shot -> frame -> track -> box|None
    parent = {}                            # union-find for same_as

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for d in decisions:
        errs = validate(d, frames)
        if errs:
            rejected.append({**d, "errors": errs})
            continue
        shot, frame = d["shot_id"], d["frame"]
        action = d["action"]
        if action == "drop":
            overrides[shot].setdefault(frame, {})[d["track_id"]] = None
        elif action in {"shrink_bottom", "grow_bottom", "shrink_top"}:
            b = list(boxes_of(frames[(shot, frame)])[d["track_id"]])
            h = b[3] - b[1]
            if action == "shrink_bottom":
                b[3] = b[1] + h * (1.0 - d["amount"])
            elif action == "grow_bottom":
                b[3] = b[3] + h * d["amount"]
            else:
                b[1] = b[1] + h * d["amount"]
            overrides[shot].setdefault(frame, {})[d["track_id"]] = b
        elif action == "same_as":
            union(int(d["track_id"]), int(d["same_as_track"]))
        applied.append(d)

    # collapse same_as into canonical ids
    merge = {}
    for node in list(parent):
        merge[node] = find(node)

    # per-actor frame ranges from assign_actor decisions
    actor_frames = defaultdict(lambda: defaultdict(set))
    actor_evidence = defaultdict(dict)
    for d in decisions:
        if d.get("action") == "assign_actor" and d not in rejected:
            canon = merge.get(int(d["track_id"]), int(d["track_id"]))
            actor_frames[d["shot_id"]][d["actor_id"]].add((d["frame"], canon))
            actor_evidence[(d["shot_id"], d["actor_id"], d["frame"])] = \
                d.get("evidence", "")

    anchors = []
    for shot, per_actor in actor_frames.items():
        for actor, pairs in per_actor.items():
            pairs = sorted(pairs)
            start, end, tid = pairs[0][0], pairs[0][0], pairs[0][1]
            for f, t in pairs[1:]:
                if t == tid and f == end + 1:
                    end = f
                    continue
                anchors.append({"shot_id": shot, "track_id": tid, "actor_id": actor,
                                "start_frame": start, "end_frame": end,
                                "confirmation": "hypothesis",
                                "evidence": actor_evidence.get((shot, actor, start), "")})
                start, end, tid = f, f, t
            anchors.append({"shot_id": shot, "track_id": tid, "actor_id": actor,
                            "start_frame": start, "end_frame": end,
                            "confirmation": "hypothesis",
                            "evidence": actor_evidence.get((shot, actor, start), "")})

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "box_overrides.json"), "w", encoding="utf-8") as fh:
        json.dump({k: {str(f): {str(t): b for t, b in v.items()} for f, v in fv.items()}
                   for k, fv in overrides.items()}, fh, ensure_ascii=False, indent=2)
    if anchors:
        with open(os.path.join(args.out_dir, "anchors.json"), "w", encoding="utf-8") as fh:
            json.dump({"actor_ids": sorted({a["actor_id"] for a in anchors}),
                       "anchors": anchors}, fh, ensure_ascii=False, indent=2)
    report = {
        "applied": len(applied),
        "rejected": len(rejected),
        "rejected_detail": rejected,
        "merged_tracks": merge,
        "anchors_written": len(anchors),
    }
    with open(os.path.join(args.out_dir, "apply_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "rejected_detail"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
