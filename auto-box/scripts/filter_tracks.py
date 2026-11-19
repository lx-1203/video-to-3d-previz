# -*- coding: utf-8 -*-
"""Keep only selected track ids in a frames.jsonl (drop background extras)."""
import argparse
import json


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detections", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--keep-ids", required=True, help="comma separated track ids")
    args = ap.parse_args()

    keep = {int(x) for x in args.keep_ids.split(",") if x.strip()}
    kept = dropped = 0
    with open(args.detections, encoding="utf-8") as src, \
            open(args.output, "w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            fr = json.loads(line)
            before = len(fr.get("observed_tracks", []))
            fr["observed_tracks"] = [t for t in fr.get("observed_tracks", [])
                                     if t.get("window_local_track_id") in keep]
            kept += len(fr["observed_tracks"])
            dropped += before - len(fr["observed_tracks"])
            dst.write(json.dumps(fr, ensure_ascii=False) + "\n")
    print(json.dumps({"kept_boxes": kept, "dropped_boxes": dropped,
                      "keep_ids": sorted(keep)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
