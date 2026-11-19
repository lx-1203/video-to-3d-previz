# -*- coding: utf-8 -*-
"""Load config/paths.json and resolve relative paths against the package root.

Paths inside the package are written relative ("vendor/GEM-X", "assets/source.mp4")
so the folder can be moved or renamed without editing anything.  Empty
"python"/"pose_python" mean "use the interpreter currently running".
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "paths.json")

_SHA_KEYS = {"yolox_sha256", "source_sha256"}


def load_config(path=None):
    config_path = path or CONFIG
    with open(config_path, encoding="utf-8-sig") as fh:
        raw = json.load(fh)

    cfg = {k: v for k, v in raw.items() if not k.startswith("_")}
    for key, value in list(cfg.items()):
        if not isinstance(value, str) or not value or key in _SHA_KEYS:
            continue
        if not os.path.isabs(value):
            cfg[key] = os.path.normpath(os.path.join(ROOT, value))
    for key in ("python", "pose_python"):
        if not cfg.get(key):
            cfg[key] = sys.executable
    cfg["_root"] = ROOT
    cfg["_config"] = config_path
    return cfg


def package_root():
    return ROOT
