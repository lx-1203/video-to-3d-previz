#!/usr/bin/env python3
"""Extract an exact decoded edit-out frame and verify its source receipt.

Uses FFmpeg, never a generation service. Frame indices are zero-based.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def ffmpeg_binary(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg")
    if candidate:
        resolved = shutil.which(candidate) or (str(Path(candidate).resolve()) if Path(candidate).is_file() else None)
        if resolved:
            return resolved
        raise ValueError(f"FFmpeg executable not found: {candidate}")
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise ValueError("FFmpeg not found. Supply --ffmpeg with an existing executable path.") from exc


def run_ffmpeg(binary: str, args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run([binary, "-nostdin", "-hide_banner", *args], capture_output=True)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-5000:]
        raise ValueError(f"FFmpeg failed ({result.returncode}): {detail}")
    return result


def extract(video: Path, output: Path, frame_index: int | None, binary: str) -> dict:
    video, output = video.resolve(), output.resolve()
    receipt_path = output.with_suffix(".json")
    if not video.is_file():
        raise ValueError(f"Source video not found: {video}")
    if output.suffix.lower() != ".png":
        raise ValueError("--out must be a PNG path")
    if video in (output, receipt_path):
        raise ValueError("Output must differ from source video")
    if output.exists() or receipt_path.exists():
        raise ValueError("Image or receipt already exists. Choose a new versioned filename.")
    if frame_index is not None and frame_index < 0:
        raise ValueError("Frame index must be zero or greater")
    before_hash = digest(video)
    # Count decoded output frames with timing passthrough: container frame-count
    # metadata, nominal FPS and duration-based seeks are unreliable for VFR media.
    counted = run_ffmpeg(binary, [
        "-v", "error", "-xerror", "-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn",
        "-fps_mode", "passthrough", "-progress", "pipe:1", "-nostats", "-f", "null", "-"
    ])
    counts = re.findall(rb"(?m)^frame=\s*(\d+)\s*$", counted.stdout)
    count = int(counts[-1]) if counts else 0
    if not count:
        raise ValueError("Source has no decoded video frames")
    selected = count - 1 if frame_index is None else frame_index
    if selected >= count:
        raise ValueError(f"Frame {selected} is outside decoded range 0..{count - 1}")
    rendered = run_ffmpeg(binary, [
        "-v", "info", "-xerror", "-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", f"select=eq(n\\,{selected}),showinfo", "-frames:v", "1", "-fps_mode", "passthrough",
        "-c:v", "png", "-f", "image2pipe", "pipe:1"
    ])
    if not rendered.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("FFmpeg returned no PNG for the selected frame")
    if digest(video) != before_hash:
        raise ValueError("Source video changed during extraction. Retry on the selected stable version.")
    times = re.findall(r"pts_time:([-+\d.eE]+)", rendered.stderr.decode("utf-8", errors="replace"))
    receipt = {
        "schema_version": 1,
        "source_video": str(video),
        "source_sha256": before_hash,
        "image": str(output),
        "image_sha256": hashlib.sha256(rendered.stdout).hexdigest(),
        "decoded_frame_count": count,
        "frame_index": selected,
        "frame_index_basis": "zero-based decoded display order, video stream 0",
        "presentation_time_seconds": float(times[0]) if times else None,
        "selection": "last_decoded_frame" if frame_index is None else "explicit_edit_out_frame",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "visual_review": "unreviewed",
        "receipt": str(receipt_path),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive writes protect previously selected assets, even on concurrent runs.
    created = []
    try:
        with output.open("xb") as stream:
            created.append(output)
            stream.write(rendered.stdout)
        with receipt_path.open("x", encoding="utf-8") as stream:
            created.append(receipt_path)
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)  # only files this call exclusively created
            except OSError:
                pass  # preserve the original write failure
        raise
    return receipt


def verify(receipt_path: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise ValueError("Unsupported frame receipt schema")
    errors = []
    for path_key, hash_key in (("source_video", "source_sha256"), ("image", "image_sha256")):
        value = receipt.get(path_key)
        expected = receipt.get(hash_key)
        if not isinstance(value, str) or not value or not isinstance(expected, str):
            errors.append(f"Missing {path_key} or {hash_key}")
            continue
        path = Path(value)
        if not path.is_file():
            errors.append(f"Missing {path_key}: {path}")
        elif digest(path) != expected:
            errors.append(f"Changed {path_key}: {path}")
    return {"valid": not errors, "receipt": str(receipt_path.resolve()), "errors": errors,
            "scope": "source/image file integrity only; visual and narrative continuity require review"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("extract", help="Extract the last decoded frame or a selected edit-out frame")
    command.add_argument("video", type=Path)
    command.add_argument("--out", required=True, type=Path)
    command.add_argument("--frame-index", type=int, help="Zero-based last included frame of the chosen edit")
    command.add_argument("--ffmpeg", help="FFmpeg executable; otherwise use FFMPEG_BINARY, PATH or imageio_ffmpeg")
    command = commands.add_parser("verify", help="Verify source and image hashes; does not certify visual continuity")
    command.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "extract":
            result = extract(args.video, args.out, args.frame_index, ffmpeg_binary(args.ffmpeg))
        else:
            result = verify(args.receipt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("valid", True) else 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
