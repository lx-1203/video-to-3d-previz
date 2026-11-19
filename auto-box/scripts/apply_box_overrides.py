# -*- coding: utf-8 -*-
"""Apply box_overrides.json to a frames.jsonl, producing a corrected copy.

This is the write-back half of the review loop: the vision model's decisions
already became concrete boxes in box_overrides.json, and this step puts them
into the detection stream that the pose stage consumes.
"""
import argparse
import json


def load_overrides(path):
    with open(path, encoding="utf-8-sig") as fh:
        raw = json.load(fh)
    out = {}
    for shot, per_frame in raw.items():
        for frame, per_track in per_frame.items():
            out[(shot, int(frame))] = {int(t): b for t, b in per_track.items()}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detections", required=True)
    ap.add_argument("--overrides", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    overrides = load_overrides(args.overrides)
    frames_in = frames_out = overridden = dropped = 0

    with open(args.detections, encoding="utf-8") as src, \
            open(args.output, "w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            fr = json.loads(line)
            frames_in += 1
            key = (fr.get("window") or fr.get("shot_id"), fr["source_zero_based"])
            patch = overrides.get(key)
            applied = []
            if patch:
                kept = []
                for t in fr.get("observed_tracks", []):
                    tid = t.get("window_local_track_id", t.get("track_id"))
                    if tid in patch:
                        new = patch[tid]
                        if new is None:
                            applied.append({"track_id": tid, "action": "dropped"})
                            dropped += 1
                            continue
                        t = dict(t)
                        t["box_xyxy_original"] = list(t["box_xyxy"])
                        t["box_xyxy"] = [float(v) for v in new]
                        t["box_kind"] = "review_override"
                        applied.append({"track_id": tid, "action": "replaced"})
                        overridden += 1
                    kept.append(t)
                fr = dict(fr)
                fr["observed_tracks"] = kept
                fr["overrides_applied"] = applied
            dst.write(json.dumps(fr, ensure_ascii=False) + "\n")
            frames_out += 1

    print(json.dumps({"frames_in": frames_in, "frames_out": frames_out,
                      "boxes_replaced": overridden, "boxes_dropped": dropped,
                      "output": args.output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
