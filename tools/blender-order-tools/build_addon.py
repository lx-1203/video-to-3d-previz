"""Build an explicit, asset-free Blender add-on ZIP and an external receipt."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

FILES = (
    "orderflow/__init__.py", "orderflow/core.py", "orderflow/worker.py",
    "orderflow/media.py", "orderflow/ui.py", "orderflow/motion.py",
    "orderflow/palette.py", "orderflow/acceptance.py", "orderflow/camera.py", "README.md", "examples/palette.json",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    # An allowlist prevents accidental inclusion of customer assets and caches.
    payload = {}
    for name in FILES:
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Missing or linked release file: " + name)
        target = name if name.startswith("orderflow/") else "orderflow/" + name
        data = path.read_bytes()
        if path.suffix == ".py":
            ast.parse(data, filename=name)
        payload[target] = data
    repo = root.parents[1]
    solver = repo / "camera-solve/addon/jsonio.py"
    if not solver.is_file() or solver.is_symlink() or not solver.resolve().is_relative_to(repo):
        raise ValueError("Missing or linked CameraSolve planner")
    solver_data = solver.read_bytes()
    ast.parse(solver_data, filename=str(solver))
    payload["orderflow/camera_solve_jsonio.py"] = solver_data
    init = ast.parse(payload["orderflow/__init__.py"])
    info = next(ast.literal_eval(node.value) for node in init.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets))
    version = ".".join(map(str, info["version"]))
    if output.exists() or output.with_suffix(".receipt.json").exists():
        raise ValueError("Release output already exists; use a new destination")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                           capture_output=True, text=True, check=True).stdout.strip()
    output.parent.mkdir(parents=True, exist_ok=True)
    members = {name: digest(data) for name, data in payload.items()}
    try:
        with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
            for name, data in payload.items():
                entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, data)
        with zipfile.ZipFile(output) as archive:
            if archive.testzip() or archive.namelist() != list(payload):
                raise ValueError("Release ZIP member/CRC mismatch")
            if any(digest(archive.read(name)) != sha for name, sha in members.items()):
                raise ValueError("Release ZIP SHA mismatch")
    except Exception:
        # Delete only the new artifact this invocation owns.
        output.unlink(missing_ok=True)
        raise
    receipt = {"version": version, "git_revision": revision,
               "source_dirty": bool(dirty), "zip_path": str(output),
               "zip_sha256": digest(output.read_bytes()), "bytes": output.stat().st_size,
               "members": members, "crc_and_member_sha_verified": True}
    output.with_suffix(".receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).parent, args.output), ensure_ascii=False))
