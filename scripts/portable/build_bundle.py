"""Build a private, relocatable Windows/NVIDIA kit using only the stdlib.

This module copies selected bytes. It never executes skills, Blender or addons,
installs dependencies, follows links, or copies an existing Python environment.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile


PUBLIC_NAME = "video-to-3d-previz"
PRIVATE_NAME = "client-model-standard"
PUBLIC_REQUIRED_FILES = {
    "SKILL.md", "scripts/check_update.py", "scripts/motion/motion_workbench.py",
    "scripts/motion/setup_backend.py",
}
PRIVATE_EXTENSIONS = {".md", ".json", ".yaml", ".blend", ".png"}
PRIVATE_EXCLUDED_DIRS = {
    "config", "configs", "configuration", "tasks", "task", "jobs", "job", "orders",
    "production", "cache", "caches", "__pycache__", "venv", ".venv", "env", ".git",
    "node_modules", "runtime", "backend", "vendor", "weights", "dist", "tmp", "temp",
    "任务", "缓存", "客户任务",
}
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", "clock$"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in "123456789¹²³"
}

INSTALL_BAT = (
    '@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n'
    'where py >nul 2>nul\r\nif errorlevel 1 goto use_python\r\n'
    'py -3 --version >nul 2>nul\r\nif errorlevel 1 goto use_python\r\n'
    'py -3 "%~dp0install.py" %*\r\ngoto check_result\r\n'
    ':use_python\r\nwhere python >nul 2>nul\r\nif errorlevel 1 goto missing_python\r\n'
    'python "%~dp0install.py" %*\r\ngoto check_result\r\n'
    ':missing_python\r\necho Python 3 is required. Install Python and try again.\r\n'
    'pause\r\nexit /b 1\r\n:check_result\r\nif errorlevel 1 goto failed\r\n'
    'echo Installation finished.\r\npause\r\nexit /b 0\r\n'
    ':failed\r\necho Installation failed. Review the error above.\r\npause\r\nexit /b 1\r\n'
).encode("ascii")

INSTALL_MD = """# Windows 接单工具安装说明

这是制作方内部私有工具包，含获授权的甲方模板。请只复制给获授权的电脑。

1. 将 ZIP 完整解压到普通的新目录；不要在 ZIP 内直接运行。
2. 准备 Python 3.10+、Git、FFmpeg、Blender 和兼容 NVIDIA 驱动。Blender 参考版本为 5.2.2 LTS。
3. 打开终端，先检查安装计划，例如：
   `py -3 install.py --plan-only --target "C:\\OrderWorkbench"`
   技能目录默认读取本机 CODEX_HOME（或当前用户的 .codex）；可用 `--skills-root` 指定本机的完整目录。
4. 核对目标后，去掉 `--plan-only` 安装。也可向 `install.bat` 传入同样参数。
   校验失败或目标已有不同内容时，安装器会拒绝覆盖。相同文件允许重新运行。
5. 安装后的 `installation.json` / `catalog.json` 记录本机位置。插件 ZIP 仅随包保存，
   请按插件说明在 Blender 中安装；本包未自动启用插件或修改 Blender 设置。
6. 动作算法的环境和权重由主 Skill 的 `scripts/motion/setup_backend.py` 在目标电脑另行部署；
   先看该脚本的 `--help`，需要下载或安装时由用户显式执行。本包不包含旧虚拟环境、权重、任务或本机配置。

所有 profile 均为候选，须对输入骨架、坐标、动作和实际模型校准及验收；不代表生产视觉已通过。
原始 Blender 模板只供追溯，读取时禁用自动运行脚本；日常使用清理后的模板库。
使用 video-to-3d-previz 前仍须执行其正常在线更新门禁，不绕过失败、锁或本地修改检查。
本机搬迁模拟只能验证文件与路径，不能替代干净新机的驱动、算法推理或正式影片验收。
""".encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _json_object(data: bytes, label: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def invalid_number(value):
        raise ValueError(f"Non-JSON number in {label}: {value}")

    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique,
                           parse_constant=invalid_number)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid JSON: {label}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {label}")
    return value


def _safe_member(name: str) -> list[str]:
    """Reject Windows aliases as well as ordinary zip-slip paths on any host OS."""
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError(f"Unsafe member path: {name!r}")
    parts = name.split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part[-1] in ". "
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED):
            raise ValueError(f"Unsafe member path: {name!r}")
    return parts


def _claim(index: dict, name: str, is_dir: bool = False) -> None:
    parts = _safe_member(name)
    for number in range(1, len(parts) + 1):
        path = "/".join(parts[:number])
        kind = "directory" if number < len(parts) or is_dir else "file"
        key = path.casefold()
        previous = index.get(key)
        if previous is not None and previous != (path, kind):
            raise ValueError(f"Case collision or file/directory conflict: {name}")
        if previous is not None and number == len(parts) and kind == "file":
            raise ValueError(f"Duplicate member: {name}")
        index[key] = (path, kind)


def _no_links(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError(f"Links and reparse points are not allowed: {current}")
    return path


def _read_file(path: Path) -> bytes:
    path = _no_links(path)
    if not path.is_file():
        raise ValueError(f"Required regular file is missing: {path}")
    return path.read_bytes()


def _installer_bytes() -> bytes:
    return _read_file(Path(__file__).with_name("install_bundle.py"))


def _check_metadata(data: bytes, metadata: dict, label: str) -> None:
    if (not isinstance(metadata, dict) or type(metadata.get("size")) is not int
            or metadata["size"] != len(data) or metadata.get("sha256") != _sha(data)):
        raise ValueError(f"Size/SHA mismatch: {label}")


def _public_payload(root: Path) -> tuple[dict[str, bytes], str]:
    raw = _read_file(root / "release-manifest.json")
    manifest = _json_object(raw, "release-manifest.json")
    files = manifest.get("files")
    version = manifest.get("version")
    if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or manifest.get("skill_name") != PUBLIC_NAME
            or not isinstance(version, str) or not version.strip()
            or not isinstance(files, dict) or not files):
        raise ValueError("Public skill identity/version or release manifest is invalid")
    if not PUBLIC_REQUIRED_FILES.issubset(files):
        raise ValueError(f"Public skill is missing required entries: {sorted(PUBLIC_REQUIRED_FILES - files.keys())}")
    payload = {}
    index = {}
    for name, metadata in files.items():
        _claim(index, name)
        # A declared forbidden entry invalidates the release; silently dropping
        # it would make the packaged skill disagree with its own manifest.
        if _excluded_private(Path(name)):
            raise ValueError(f"Public manifest declares forbidden local content: {name}")
        data = _read_file(root.joinpath(*_safe_member(name)))
        _check_metadata(data, metadata, name)
        payload[name] = data
    _claim(index, "release-manifest.json")
    payload["release-manifest.json"] = raw
    return payload, version


def _excluded_private(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    return (any(part in PRIVATE_EXCLUDED_DIRS or part.startswith(".") for part in parts)
            or any(part.startswith(("config.", "config_", "config-")) for part in parts))


def _private_payload(root: Path) -> tuple[dict[str, bytes], list[dict]]:
    if not root.is_dir():
        raise ValueError(f"Private skill directory is missing: {root}")
    payload = {}
    index = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        # Check links before exclusions, so an excluded directory cannot hide a link.
        for name in dirs + files:
            _no_links(Path(directory) / name)
        dirs[:] = sorted(name for name in dirs
                         if not _excluded_private((Path(directory) / name).relative_to(root)))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root)
            if _excluded_private(relative) or path.suffix.casefold() not in PRIVATE_EXTENSIONS:
                continue
            member = relative.as_posix()
            _claim(index, member)
            payload[member] = _read_file(path)
    contract_name = "references/asset-contract.json"
    required = {"SKILL.md", contract_name, "assets/client_source_original.blend",
                "assets/client_character.blend"}
    if not required.issubset(payload):
        raise ValueError(f"Private skill is missing required files: {sorted(required - payload.keys())}")
    original = payload[contract_name]
    contract = _json_object(original, contract_name)
    for field, asset in [("source_sha256", "assets/client_source_original.blend"),
                         ("asset_sha256", "assets/client_character.blend")]:
        if contract.get(field) != _sha(payload[asset]):
            raise ValueError(f"Private contract asset SHA mismatch: {field}")
    contract["source"] = "../assets/client_source_original.blend"
    payload[contract_name] = _json_bytes(contract)
    normalization = {
        "path": f"payload/skills/{PRIVATE_NAME}/{contract_name}", "field": "source",
        "original_sha256": _sha(original), "packaged_sha256": _sha(payload[contract_name]),
        "replacement": contract["source"],
    }
    return payload, [normalization]


def _check_profile_description(value: str) -> None:
    # Only human limitations may contain word/word prose, never host/file paths.
    if ("\\" in value or "${" in value
            or re.search(r"(?<!\w)[A-Za-z]:|(?<!\w)/\S|\.\./|~(?:/|\w+/)", value)
            or re.search(r"\$[A-Za-z_]\w*|%[A-Za-z_]\w*%|\bfile:", value, re.I)
            or re.search(r"/[^\s/]*\.[A-Za-z0-9]+|[^\s/]*\.[A-Za-z0-9]+/", value)):
        raise ValueError("Profile limitations must not contain file paths or host configuration")


def _check_profile(value: object, *, root: bool = True) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _check_profile(key, root=False)
            if root and key == "limitations":
                if not isinstance(child, list) or any(not isinstance(item, str) for item in child):
                    raise ValueError("Profile limitations must be a list of descriptive strings")
                for item in child:
                    _check_profile_description(item)
            else:
                _check_profile(child, root=False)
    elif isinstance(value, list):
        for child in value:
            _check_profile(child, root=False)
    elif isinstance(value, str):
        # Profiles describe bones and coordinates, not paths or host configuration.
        if ("/" in value or "\\" in value or re.match(r"[A-Za-z]:", value)
                or "${" in value or value.startswith(("~", "file:"))):
            raise ValueError("Profiles must contain no file paths or machine-specific configuration")


def _check_addon(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            index = {}
            seen = set()
            python_entries = []
            for info in archive.infolist():
                # zipfile normalizes backslashes on Windows and truncates NULs;
                # validate the original central-directory name before that loss.
                if info.orig_filename != info.filename:
                    raise ValueError(f"Normalized/unsafe addon entry: {info.orig_filename!r}")
                name = info.filename[:-1] if info.is_dir() else info.filename
                _claim(index, name, info.is_dir())
                if name.casefold() in seen:
                    raise ValueError(f"Duplicate addon entry: {name}")
                seen.add(name.casefold())
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode) or info.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"Addon contains a link: {name}")
                if info.flag_bits & 1:
                    raise ValueError("Encrypted addons are not supported")
                if not info.is_dir() and name.lower().endswith(".py") and info.file_size:
                    python_entries.append(name)
            if not python_entries:
                raise ValueError("Addon ZIP must contain nonempty Python addon code")
            if archive.testzip() is not None:
                raise ValueError("Addon ZIP CRC check failed")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError("Invalid addon ZIP") from error


def _verify_bundle(output: Path, expected: dict[str, bytes]) -> None:
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Bundle ZIP CRC check failed")
        if len(archive.infolist()) != len(expected) or set(archive.namelist()) != set(expected):
            raise ValueError("Bundle ZIP member list differs from the planned payload")
        index = {}
        for name, original in expected.items():
            _claim(index, name)
            data = archive.read(name)
            if len(data) != len(original) or _sha(data) != _sha(original):
                raise ValueError(f"Bundle readback SHA mismatch: {name}")


def build_bundle(skill_root, private_root, profiles, addon_zip, output) -> dict:
    """Validate inputs, create a *new* ZIP, and return its bound manifest/report.

    Validation errors raise ValueError (or the underlying filesystem error).
    Existing output files are never replaced. Inputs are read without mutation.
    """
    skill_root = _no_links(Path(skill_root))
    private_root = _no_links(Path(private_root))
    output = _no_links(Path(output))
    profiles = list(profiles)
    if not profiles:
        raise ValueError("At least one explicitly selected candidate profile is required")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if output.suffix.casefold() != ".zip":
        raise ValueError("Output must be a new .zip path")
    for source in (skill_root, private_root):
        if output.is_relative_to(source):
            raise ValueError("Output cannot be placed within a skill source directory")
    public, version = _public_payload(skill_root)
    private, normalizations = _private_payload(private_root)
    entries = {}
    metadata = {}
    index = {}

    def add(name: str, data: bytes, role: str) -> None:
        _claim(index, name)
        entries[name] = data
        metadata[name] = {"size": len(data), "sha256": _sha(data), "role": role}

    for name, data in sorted(public.items()):
        add(f"payload/skills/{PUBLIC_NAME}/{name}", data, "public_skill")
    for name, data in sorted(private.items()):
        add(f"payload/skills/{PRIVATE_NAME}/{name}", data, "private_skill")
    selected_profiles = []
    for profile in profiles:
        path = Path(profile)
        if path.suffix.casefold() != ".json":
            raise ValueError("Profiles must be JSON files")
        data = _read_file(path)
        _check_profile(_json_object(data, path.name))
        member = f"payload/profiles/{path.name}"
        add(member, data, "candidate_profile")
        selected_profiles.append({"path": member, "status": "candidate_requires_calibration"})
    addon_path = Path(addon_zip)
    if addon_path.suffix.casefold() != ".zip":
        raise ValueError("Addon must be a real .zip file")
    addon_data = _read_file(addon_path)
    _check_addon(addon_data)
    addon_member = f"payload/addons/{addon_path.name}"
    add(addon_member, addon_data, "blender_addon")
    add("install.py", _installer_bytes(), "installer")
    add("install.bat", INSTALL_BAT, "launcher")
    add("INSTALL.md", INSTALL_MD, "instructions")
    manifest = {
        "schema_version": 1, "bundle_type": "order_workbench_private_windows_nvidia",
        "public_skill": {"name": PUBLIC_NAME, "version": version},
        "private_skill": {"name": PRIVATE_NAME}, "profiles": selected_profiles,
        "addons": [{"path": addon_member, "status": "packaged_not_installed"}],
        "normalizations": normalizations, "files": metadata,
    }
    _claim(index, "bundle-manifest.json")
    entries["bundle-manifest.json"] = _json_bytes(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output.open("xb") as stream:
            created = True
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(entries.items()):
                    archive.writestr(name, data)
        _verify_bundle(output, entries)
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise
    final_data = output.read_bytes()
    return {"output": str(output), "size": len(final_data), "sha256": _sha(final_data),
            "file_count": len(entries), "manifest": manifest}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a validated private Windows/NVIDIA order kit")
    parser.add_argument("--skill", required=True)
    parser.add_argument("--private-skill", required=True)
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--addon-zip", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        report = build_bundle(args.skill, args.private_skill, args.profile, args.addon_zip, args.out)
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        print(f"Bundle build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
