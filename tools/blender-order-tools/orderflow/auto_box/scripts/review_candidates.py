# -*- coding: utf-8 -*-
"""Find frames that need a vision review, and render them for the model.

Pure geometry: no model, no GPU, seconds for a 700-frame shot.  The four rules
below turn a whole shot into a short, evidence-backed review list.
"""
import argparse
import json
import os
import subprocess
from collections import defaultdict

from PIL import Image, ImageDraw
from font_util import load_font

PALETTE = [(255, 70, 70), (70, 170, 255), (90, 230, 120), (255, 200, 60),
           (230, 110, 255), (60, 230, 220), (255, 140, 200), (180, 180, 255)]


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b):
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return iw * ih


def center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


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


def observed(fr):
    out = []
    for t in fr.get("observed_tracks") or fr.get("detections") or []:
        if t.get("observed") is False:
            continue
        tid = t.get("window_local_track_id", t.get("track_id"))
        box = t.get("box_xyxy") or t.get("box")
        if tid is None or not box or len(box) != 4:
            continue
        out.append((int(tid), [float(v) for v in box]))
    return out


def scan(rows, contain=0.90, h_ratio=1.40, aspect=3.2, min_h=250.0,
         min_w=55.0, jump=0.8):
    cands = []
    by_track = defaultdict(dict)
    for (shot, frame), fr in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        boxes = observed(fr)
        for tid, b in boxes:
            by_track[(shot, tid)][frame] = b

        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ta, a = boxes[i]
                tb, b = boxes[j]
                ia = inter(a, b)
                if ia <= 0:
                    continue
                small = min(area(a), area(b))
                if small <= 0:
                    continue
                c = ia / small
                ha, hb = a[3] - a[1], b[3] - b[1]
                r = max(ha, hb) / max(1.0, min(ha, hb))
                if c >= contain and r >= h_ratio:
                    tall = (ta, a) if ha > hb else (tb, b)
                    cands.append({"type": "containment_duplicate", "shot_id": shot,
                                  "frame": frame, "track_id": tall[0],
                                  "other_track_id": tb if tall[0] == ta else ta,
                                  "values": {"containment": round(c, 3),
                                             "height_ratio": round(r, 2)}})

        for tid, b in boxes:
            w, h = b[2] - b[0], b[3] - b[1]
            if w >= min_w and h >= min_h and h > aspect * w:
                cands.append({"type": "extreme_aspect", "shot_id": shot,
                              "frame": frame, "track_id": tid,
                              "values": {"w": round(w), "h": round(h),
                                         "aspect": round(h / w, 2)}})

    for (shot, tid), frames in by_track.items():
        keys = sorted(frames)
        for prev, cur in zip(keys, keys[1:]):
            if cur - prev != 1:
                continue
            a, b = frames[prev], frames[cur]
            h = max(1.0, b[3] - b[1])
            ax, ay = center(a)
            bx, by = center(b)
            d = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            if d > jump * h:
                cands.append({"type": "id_switch", "shot_id": shot, "frame": cur,
                              "track_id": tid,
                              "values": {"jump_px": round(d), "box_h": round(h),
                                         "jump_ratio": round(d / h, 2)}})

    dedup = {}
    for c in cands:
        dedup[(c["shot_id"], c["frame"], c["track_id"], c["type"])] = c
    return sorted(dedup.values(),
                  key=lambda c: (c["shot_id"], c["frame"], c["track_id"]))


def render(rows, cands, video, out_dir, limit, scale=3.0):
    os.makedirs(out_dir, exist_ok=True)
    font = load_font(24, bold=True)
    small = load_font(18)
    made = {}
    for c in cands:
        key = (c["shot_id"], c["frame"])
        if key in made:
            continue
        if len(made) >= limit:
            break
        fr = rows[key]
        boxes = observed(fr)
        target = next((b for tid, b in boxes if tid == c["track_id"]), None)
        if target is None:
            continue
        top = fr.get("pts") is not None
        png = os.path.join(out_dir, "src_%s_%04d.png" % (c["shot_id"], c["frame"]))
        if not os.path.exists(png):
            subprocess.run([os.environ.get("ORDERFLOW_FFMPEG_EXE", "ffmpeg"), "-y", "-v", "error", "-i", video,
                            "-vf", "select='eq(n\\,%d)'" % c["frame"],
                            "-fps_mode", "passthrough", "-frames:v", "1", png], check=True)
        img = Image.open(png).convert("RGB")
        d = ImageDraw.Draw(img)
        for tid, b in boxes:
            col = (255, 255, 0) if tid == c["track_id"] else PALETTE[tid % 8]
            d.rectangle(b, outline=col, width=6 if tid == c["track_id"] else 3)
            label = "id%d" % tid
            tw = d.textlength(label, font=font)
            d.rectangle([b[0], b[1] - 28, b[0] + tw + 10, b[1]], fill=col)
            d.text((b[0] + 5, b[1] - 26), label, fill=(0, 0, 0), font=font)

        x1, y1, x2, y2 = target
        mx, my = (x2 - x1) * 0.55, (y2 - y1) * 0.10
        crop = img.crop((max(0, int(x1 - mx)), max(0, int(y1 - my)),
                         min(img.width, int(x2 + mx)), min(img.height, int(y2 + my))))
        if crop.width > 4 and crop.height > 4:
            f = min(600 / crop.width, 640 / crop.height, scale)
            crop = crop.resize((max(1, int(crop.width * f)),
                                max(1, int(crop.height * f))), Image.LANCZOS)
        canvas = Image.new("RGB", (1280, 720), (16, 16, 16))
        canvas.paste(img.resize((640, 360), Image.LANCZOS), (0, 60))
        canvas.paste(crop, (640 + (640 - crop.width) // 2,
                            60 + (650 - crop.height) // 2))
        bar = ImageDraw.Draw(canvas)
        bar.rectangle([0, 0, 1280, 46], fill=(0, 0, 0))
        bar.text((10, 10),
                 "%s frame %d  target id%d  reason=%s"
                 % (c["shot_id"], c["frame"], c["track_id"], c["type"]),
                 fill=(255, 255, 0), font=font)
        bar.text((10, 380), "all boxes: " + ", ".join("id%d" % t for t, _ in boxes),
                 fill=(220, 220, 220), font=small)
        out = os.path.join(out_dir, "review_%s_%04d.jpg" % (c["shot_id"], c["frame"]))
        canvas.save(out, quality=88)
        made[key] = out
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detections", required=True, help="frames.jsonl from the detector")
    ap.add_argument("--source", required=True, help="source video")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--limit", type=int, default=40, help="max rendered frames")
    args = ap.parse_args()

    rows = load_frames(args.detections)
    cands = scan(rows)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "candidates.json"), "w", encoding="utf-8") as fh:
        json.dump({"candidates": cands}, fh, ensure_ascii=False, indent=2)
    shots = sorted({(c["shot_id"], c["frame"]) for c in cands})
    print(json.dumps({"candidates": len(cands), "frames": len(shots),
                      "rendered": min(len(shots), args.limit),
                      "out": args.out}, ensure_ascii=False))
    made = render(rows, cands, args.source, args.out, args.limit)
    print(json.dumps({"rendered_files": len(made)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
