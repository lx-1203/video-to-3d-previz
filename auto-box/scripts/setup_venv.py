# -*- coding: utf-8 -*-
"""Create a self-contained pose interpreter inside the package.

The bundled vendor/venv was built with --system-site-packages on the machine that
produced this package, so it relies on that machine's torch/cv2.  On a different
machine run this script once: it builds vendor/venv from scratch and installs the
pinned GEM dependencies.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, "vendor", "venv")

PACKAGES = [
    "torch==2.13.0",
    "numpy",
    "opencv-python",
    "scipy",
    "Pillow",
    "omegaconf==2.3.1",
    "py-soma-x==0.3.3",
    "transformers==5.3.0",
    "open3d==0.19.0",
    "einops",
    "onnxruntime",
    "av",
]


def run(cmd, env=None):
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, env=env)
    if r.returncode != 0:
        raise SystemExit("command failed: %s" % " ".join(cmd))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-python", default=sys.executable,
                    help="interpreter used to create the venv")
    ap.add_argument("--index", default="https://mirrors.aliyun.com/pypi/simple",
                    help="pip index (PyPI is slow or blocked in some networks)")
    ap.add_argument("--force", action="store_true", help="rebuild over an existing venv")
    args = ap.parse_args()

    if os.path.exists(TARGET) and not args.force:
        print("vendor/venv already exists; pass --force to rebuild")
    else:
        if os.path.exists(TARGET):
            import shutil
            shutil.rmtree(TARGET)
        run([args.base_python, "-m", "venv", TARGET])

    python = os.path.join(TARGET, "Scripts", "python.exe")
    if not os.path.exists(python):
        python = os.path.join(TARGET, "bin", "python")

    env = dict(os.environ)
    env["SETUPTOOLS_USE_DISTUTILS"] = "stdlib"
    run([python, "-m", "pip", "install", "--upgrade", "pip"], env=env)
    for pkg in PACKAGES:
        run([python, "-m", "pip", "install", "--index-url", args.index, pkg], env=env)

    check = subprocess.run(
        [python, "-c", "import torch, soma, omegaconf, cv2;"
                       "print(torch.__version__, soma.__version__)"],
        capture_output=True, text=True)
    print(check.stdout.strip() or check.stderr.strip()[-400:])

    config_path = os.path.join(ROOT, "config", "paths.json")
    cfg = json.load(open(config_path, encoding="utf-8-sig"))
    rel = os.path.relpath(python, ROOT).replace(os.sep, "/")
    cfg["pose_python"] = rel
    json.dump(cfg, open(config_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("config pose_python ->", rel)
    print("done; now run: python scripts/preflight.py")


if __name__ == "__main__":
    main()
