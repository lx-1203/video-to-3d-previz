# -*- coding: utf-8 -*-
"""Fill pose gaps by bracketing every missing run with the nearest real frames.

The decision is made in 2D (the space the review actually looks at), because
camera-space depth jitters between crops and would turn a standing person into a
"large motion" case.  Both the 2D keypoints and the 3D joints are interpolated so
the exported numbers stay consistent with the drawn skeleton.
"""
import argparse
import json

import torch


def gather(obj):
    """actor -> frame -> {'j': (77,3), 'k': (77,2)} in camera / pixel space."""
    data = {}
    for fr in obj["frames"]:
        f = int(fr["frame_id"])
        for inst in fr["instances"]:
            actor = str(inst["track_id"])
            data.setdefault(actor, {})[f] = {
                "j": inst["joints_camera"].float(),
                "k": inst["keypoints_2d"].float(),
            }
    return data


def centroid_travel_2d(a, b):
    """2D centroid shift normalised by 2D body radius."""
    ca, cb = a.mean(dim=0), b.mean(dim=0)
    radius = ((a - ca).pow(2).sum(dim=1).sqrt().mean().item())
    return (cb - ca).norm().item() / max(radius, 1e-6)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pose", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-travel", type=float, default=0.30)
    ap.add_argument("--max-hold", type=int, default=8)
    args = ap.parse_args()

    data = gather(torch.load(args.pose, map_location="cpu", weights_only=False))
    result, summary = {}, {}

    for actor, frames in data.items():
        known = sorted(frames)
        seq, gaps = {}, []
        for f in known:
            seq[f] = {"k2d": frames[f]["k"].tolist(),
                      "joints": frames[f]["j"].tolist(),
                      "source": "observed"}

        for prev, nxt in zip(known, known[1:]):
            if nxt - prev <= 1:
                continue
            gap = list(range(prev + 1, nxt))
            ka, kb = frames[prev]["k"], frames[nxt]["k"]
            ja, jb = frames[prev]["j"], frames[nxt]["j"]
            travel = centroid_travel_2d(ka, kb)

            if travel <= args.max_travel:
                for i, f in enumerate(gap, start=1):
                    t = i / (len(gap) + 1.0)
                    seq[f] = {
                        "k2d": ((1 - t) * ka + t * kb).tolist(),
                        "joints": ((1 - t) * ja + t * jb).tolist(),
                        "source": "filled",
                        "bracket": [prev, nxt],
                        "travel": round(travel, 3),
                    }
                gaps.append({"from": prev, "to": nxt, "frames": len(gap),
                             "action": "interpolated", "travel": round(travel, 3)})
            else:
                hold = min(len(gap), args.max_hold)
                for f in gap[:hold]:
                    seq[f] = {"k2d": ka.tolist(), "joints": ja.tolist(),
                              "source": "held", "bracket": [prev, prev],
                              "travel": round(travel, 3)}
                for f in gap[hold:]:
                    seq[f] = {"k2d": None, "joints": None, "source": "missing"}
                gaps.append({"from": prev, "to": nxt, "frames": len(gap),
                             "action": "held_then_missing", "hold": hold,
                             "travel": round(travel, 3)})

        result[actor] = {str(f): v for f, v in sorted(seq.items())}
        summary[actor] = {
            "observed": sum(1 for v in seq.values() if v["source"] == "observed"),
            "filled": sum(1 for v in seq.values() if v["source"] == "filled"),
            "held": sum(1 for v in seq.values() if v["source"] == "held"),
            "missing": sum(1 for v in seq.values() if v["source"] == "missing"),
            "gaps": gaps,
        }

    json.dump({"actors": result, "summary": summary},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({a: {k: v for k, v in s.items() if k != "gaps"}
                      for a, s in summary.items()}, ensure_ascii=False))
    for a, s in summary.items():
        for g in s["gaps"]:
            print("  %-9s %3d->%-3d %2d帧 %-18s travel=%.2f"
                  % (a, g["from"], g["to"], g["frames"], g["action"], g["travel"]))
    print("written", args.out)


if __name__ == "__main__":
    main()
