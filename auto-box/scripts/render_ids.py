# -*- coding: utf-8 -*-
"""Render selected frames with every observed track id drawn."""
import argparse
import json
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

COLORS = [(255, 90, 90), (80, 170, 255), (90, 230, 120), (255, 205, 60),
          (230, 110, 255), (60, 230, 220), (255, 140, 200), (180, 180, 255),
          (255, 150, 0), (150, 255, 150)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detections", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", required=True)
    args = ap.parse_args()

    rows = {}
    for line in open(args.detections, encoding="utf-8"):
        fr = json.loads(line)
        rows[fr["source_zero_based"]] = fr
    os.makedirs(args.out, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 22)

    for f in [int(x) for x in args.frames.split(",")]:
        fr = rows.get(f)
        if not fr:
            continue
        png = os.path.join(args.out, "src_%04d.png" % f)
        if not os.path.exists(png):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.source,
                            "-vf", "select='eq(n\\,%d)'" % f, "-vsync", "0",
                            "-frames:v", "1", png], check=True)
        img = Image.open(png).convert("RGB")
        d = ImageDraw.Draw(img)
        for t in fr["observed_tracks"]:
            if not t.get("observed"):
                continue
            tid = t["window_local_track_id"]
            x1, y1, x2, y2 = t["box_xyxy"]
            col = COLORS[tid % len(COLORS)]
            d.rectangle([x1, y1, x2, y2], outline=col, width=3)
            label = "id%d" % tid
            tw = d.textlength(label, font=font)
            d.rectangle([x1, max(0, y1 - 26), x1 + tw + 10, max(0, y1 - 26) + 26], fill=col)
            d.text((x1 + 5, max(0, y1 - 24)), label, fill=(0, 0, 0), font=font)
        p = os.path.join(args.out, "ids_%04d.png" % f)
        img.save(p)
        print("wrote", p)


if __name__ == "__main__":
    main()
