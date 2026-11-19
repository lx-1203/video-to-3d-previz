# -*- coding: utf-8 -*-
"""Check that this machine can run the pipeline before any long job starts."""
import argparse
import hashlib
import importlib
import json
import os
import shutil
import subprocess

from paths import load_config
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REQUIRED_KEYS = ["python", "pose_python", "official_scripts", "gem_repo",
                 "source", "checkpoint", "soma_assets", "yolox", "intrinsics"]
PACKAGES = ["cv2", "numpy", "scipy", "PIL", "torch", "av", "onnxruntime"]


def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(8 * 1024 * 1024)
            if not block:
                break
            h.update(block)
            if limit and fh.tell() > limit:
                break
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "paths.json"))
    ap.add_argument("--deep", action="store_true",
                    help="also hash the source video and the detector weights")
    args = ap.parse_args()

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    check("launcher python >= 3.9", sys.version_info >= (3, 9), sys.version.split()[0])

    check("ffmpeg on PATH", shutil.which("ffmpeg"), shutil.which("ffmpeg") or "not found")

    if not os.path.exists(args.config):
        print("MISSING config: %s\nCopy config/paths.json and edit it first." % args.config)
        raise SystemExit(2)

    cfg = load_config(args.config)

    # package checks must run on the interpreter the pipeline actually uses
    runner = cfg.get("python") or sys.executable
    if os.path.exists(runner):
        probe = subprocess.run(
            [runner, "-c",
             "import importlib,sys\n"
             "for m in %r:\n"
             "    try:\n"
             "        mod=importlib.import_module(m); print('OK',m,getattr(mod,'__version__','?'))\n"
             "    except Exception as e: print('MISS',m,type(e).__name__)\n"
             % PACKAGES],
            capture_output=True, text=True)
        for line in probe.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                if parts[0] == "OK":
                    check("package %s" % parts[1], True,
                          parts[2] if len(parts) > 2 else "")
                else:
                    check("package %s" % parts[1], False, "missing in %s" % runner)
        if not probe.stdout.strip() and probe.stderr.strip():
            check("package probe", False, probe.stderr.strip().splitlines()[-1])
    else:
        check("pipeline interpreter", False, "missing: %s" % runner)

    for key in REQUIRED_KEYS:
        check("config key %s" % key, key in cfg and str(cfg[key]).strip() != "",
              cfg.get(key, ""))

    for key in ("python", "pose_python", "official_scripts", "gem_repo",
                "source", "checkpoint", "soma_assets", "yolox", "intrinsics"):
        if key in cfg:
            check("exists %s" % key, os.path.exists(cfg[key]), cfg[key])

    if "official_scripts" in cfg:
        for name in ("multi_actor.py", "multi_actor_pose.py", "actor_identity_proposal.py"):
            path = os.path.join(cfg["official_scripts"], name)
            check("official %s" % name, os.path.exists(path), path)

    pose_python = cfg.get("pose_python")
    if pose_python and os.path.exists(pose_python):
        probe = subprocess.run(
            [pose_python, "-c", "import torch, soma, omegaconf, cv2; print('ok')"],
            capture_output=True, text=True)
        detail = (probe.stdout or probe.stderr).strip().splitlines()
        detail = detail[-1] if detail else ""
        check("pose interpreter has torch/soma/omegaconf/cv2",
              probe.returncode == 0, detail)
        inside = os.path.abspath(pose_python).startswith(ROOT)
        check("pose interpreter is inside the package", inside,
              "vendored" if inside else
              "external: %s (works here, but copy it in for portability)" % pose_python)
    elif pose_python:
        check("pose interpreter", False,
              "missing: run scripts/setup_venv.py --force")

    check("references/joint_parents.json",
          os.path.exists(os.path.join(ROOT, "references", "joint_parents.json")))

    runs = os.path.join(ROOT, "runs")
    try:
        os.makedirs(runs, exist_ok=True)
        probe = os.path.join(runs, ".write_probe")
        open(probe, "w").close()
        os.remove(probe)
        check("runs/ writable", True, runs)
    except OSError as exc:
        check("runs/ writable", False, str(exc))

    if args.deep:
        if "yolox" in cfg and "yolox_sha256" in cfg and os.path.exists(cfg["yolox"]):
            got = sha256(cfg["yolox"])
            check("yolox sha256", got == cfg["yolox_sha256"], got[:16] + "...")
        if "source" in cfg and "source_sha256" in cfg and os.path.exists(cfg["source"]):
            check("source sha256", sha256(cfg["source"]) == cfg["source_sha256"], "checked")

    width = max(len(n) for n, _, _ in checks)
    bad = 0
    for name, ok, detail in checks:
        mark = "OK  " if ok else "MISS"
        if not ok:
            bad += 1
        print("%s %-*s %s" % (mark, width, name, detail))

    print("\n%d checks, %d failed" % (len(checks), bad))
    if bad:
        print("Fix the MISS lines above before running any pipeline step.")
        raise SystemExit(1)
    print("Environment looks ready.")


if __name__ == "__main__":
    main()
