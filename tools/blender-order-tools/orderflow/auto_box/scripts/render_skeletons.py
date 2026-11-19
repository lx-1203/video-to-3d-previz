# -*- coding: utf-8 -*-
"""Draw 2D skeletons from the pose result, marking interpolated frames."""
import argparse
import hashlib
import json
import os
import subprocess

from PIL import Image, ImageDraw
from font_util import load_font

HERE = os.path.dirname(os.path.abspath(__file__))
_PARENTS_PATH = os.path.join(os.path.dirname(HERE), "references", "joint_parents.json")
try:
    with open(_PARENTS_PATH, encoding="utf-8") as _fh:
        PARENTS = [list(p) for p in json.load(_fh)["parents"]]
except (OSError, KeyError):
    PARENTS = None


def color_for(actor):
    index = int.from_bytes(hashlib.sha256(actor.encode('utf-8')).digest()[:4], 'big')
    return FALLBACK[index % len(FALLBACK)]


FALLBACK = [(255, 210, 90), (120, 220, 255), (255, 140, 200), (160, 255, 160),
            (220, 160, 255), (255, 255, 150), (150, 200, 255), (200, 255, 200)]

def project(joints, K):
    fx, fy, cx, cy = K[0][0], K[1][1], K[0][2], K[1][2]
    out = []
    for X, Y, Z in joints:
        if Z <= 0.05:
            out.append(None)
            continue
        out.append((fx * X / Z + cx, fy * Y / Z + cy))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--filled", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--intrinsics", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", required=True,
                    help="comma separated frame numbers")
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args()

    data = json.load(open(args.filled, encoding="utf-8-sig"))["actors"]
    K = json.load(open(args.intrinsics, encoding="utf-8-sig"))
    os.makedirs(args.out, exist_ok=True)
    font = load_font(20, bold=True)

    for f in [int(x) for x in args.frames.split(",")]:
        png = os.path.join(args.out, "src_%04d.png" % f)
        if not os.path.exists(png):
            subprocess.run([os.environ.get("ORDERFLOW_FFMPEG_EXE", "ffmpeg"), "-y", "-v", "error", "-i", args.source,
                            "-vf", "select='eq(n\\,%d)'" % f, "-fps_mode", "passthrough",
                            "-frames:v", "1", png], check=True)
        img = Image.open(png).convert("RGB")
        d = ImageDraw.Draw(img, "RGBA")
        legend = []
        # draw predicted/hold first so observed skeletons stay readable on top
        priority = {"missing": 0, "held": 1, "filled": 1, "observed": 2}
        items = sorted(data.items(),
                       key=lambda kv: priority.get((kv[1].get(str(f)) or {}).get("source"),
                                                   -1))
        for actor, seq in items:
            rec = seq.get(str(f))
            if not rec:
                legend.append((actor, "无数据", (200, 200, 200)))
                continue
            col = color_for(actor)
            src = rec["source"]
            alpha = 255 if src == "observed" else 110
            if rec.get("k2d") is None:
                legend.append((actor, "missing", (150, 150, 150)))
                continue
            pts = [(float(x), float(y)) for x, y in rec["k2d"]]
            if PARENTS is not None:
                for i, ps in enumerate(PARENTS):
                    if pts[i] is None:
                        continue
                    for p in ps:
                        if p < 0 or p >= len(pts) or pts[p] is None:
                            continue
                        d.line([pts[i], pts[p]], fill=col + (alpha,), width=3)
            for pt in pts:
                if pt is None:
                    continue
                d.ellipse([pt[0] - 2.5, pt[1] - 2.5, pt[0] + 2.5, pt[1] + 2.5],
                          fill=col + (alpha,))
            legend.append((actor, src, col))
        # legend
        d.rectangle([8, 8, 210, 34 + 22 * len(legend)], fill=(0, 0, 0, 190))
        d.text((14, 12), "frame %d" % f, fill=(255, 255, 255), font=font)
        for i, (actor, src, col) in enumerate(legend):
            d.text((14, 36 + 22 * i), "%s: %s" % (actor, src), fill=col,
                   font=font)
        p = os.path.join(args.out, "skel_%04d.png" % f)
        img.save(p)
        print("wrote", p)


if __name__ == "__main__":
    main()
