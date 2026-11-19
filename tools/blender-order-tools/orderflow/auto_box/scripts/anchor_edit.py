# -*- coding: utf-8 -*-
"""Edit identity anchors with range replacement instead of hand-written JSON.

Every --set replaces the actor's claim on [start,end]; any overlapping claim of
the same actor is trimmed or dropped.  A claim by another actor on the same
track is a hard conflict and is refused unless --force is given.
"""
import argparse
import json


def load(path):
    return json.load(open(path, encoding="utf-8-sig"))


def overlaps(a, s, e):
    return a["start_frame"] <= e and a["end_frame"] >= s


def clip(anchor, s, e):
    """Return the pieces of anchor left after removing [s,e]."""
    out = []
    if anchor["start_frame"] < s:
        piece = dict(anchor)
        piece["end_frame"] = min(anchor["end_frame"], s - 1)
        out.append(piece)
    if anchor["end_frame"] > e:
        piece = dict(anchor)
        piece["start_frame"] = max(anchor["start_frame"], e + 1)
        out.append(piece)
    return [p for p in out if p["start_frame"] <= p["end_frame"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--anchors", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--set", nargs=5, action="append", default=[],
                    metavar=("SHOT", "ACTOR", "TRACK", "START", "END"))
    ap.add_argument("--clear", nargs=5, action="append", default=[],
                    metavar=("SHOT", "ACTOR", "TRACK", "START", "END"))
    ap.add_argument("--evidence", default="manual correction")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    data = load(args.anchors)
    anchors = list(data.get("anchors", []))
    log = []

    for shot, actor, track, s, e in args.clear:
        s, e, track = int(s), int(e), int(track)
        kept, removed = [], 0
        for a in anchors:
            if a["shot_id"] == shot and a["actor_id"] == actor and a["track_id"] == track \
                    and overlaps(a, s, e):
                log.append({"op": "clear", "shot": shot, "actor": actor,
                            "track": track, "range": [s, e],
                            "removed": [a["start_frame"], a["end_frame"]]})
                kept.extend(clip(a, s, e))
                removed += 1
            else:
                kept.append(a)
        anchors = kept
        if not removed:
            print("warning: --clear matched nothing for %s %s id%d" % (shot, actor, track))

    for shot, actor, track, s, e in args.set:
        s, e, track = int(s), int(e), int(track)
        for a in anchors:
            if a["shot_id"] == shot and a["track_id"] == track and a["actor_id"] != actor \
                    and overlaps(a, s, e):
                msg = ("track id%d already belongs to %s on %d..%d"
                       % (track, a["actor_id"], a["start_frame"], a["end_frame"]))
                if not args.force:
                    raise SystemExit("conflict: " + msg)
                print("warning (forced):", msg)
        kept = []
        for a in anchors:
            if a["shot_id"] == shot and a["actor_id"] == actor and overlaps(a, s, e):
                kept.extend(clip(a, s, e))
            else:
                kept.append(a)
        anchors = kept
        anchors.append({
            "shot_id": shot, "track_id": track, "actor_id": actor,
            "start_frame": s, "end_frame": e,
            "confirmation": "confirmed", "evidence": args.evidence,
        })
        log.append({"op": "set", "shot": shot, "actor": actor, "track": track,
                    "range": [s, e], "evidence": args.evidence})

    anchors.sort(key=lambda a: (a["shot_id"], a["actor_id"], a["start_frame"]))
    actors = sorted({a["actor_id"] for a in anchors})
    data["actor_ids"] = actors
    data["anchors"] = anchors
    json.dump(data, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(json.dumps({"anchors": len(anchors), "actors": actors,
                      "changes": len(log)}, ensure_ascii=False))
    for a in anchors:
        print("  %-9s id%-2d %3d..%-3d" % (a["actor_id"], a["track_id"],
                                           a["start_frame"], a["end_frame"]))


if __name__ == "__main__":
    main()
