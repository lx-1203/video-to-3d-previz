"""Download the pinned public GEM-X/SOMA body backend assets, without login.

Needs huggingface_hub with hf-xet. All weights remain under the chosen task
root; no account token is read. SHA256 is checked against upstream LFS metadata.
The OpenMMLab detector ZIP has no published hash at its documented URL: its
observed hash and ZIP integrity are recorded, not represented as upstream proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

GEM_REVISION = "4fce057963671fd95cd4154297944397e7b5a9a0"
SOMA_REVISION = "d281db2d01553f7230c56e764cec00fe27ef23f7"
SOURCE_COMMIT = "20555befe2fbbe5b2015e1fff7ff214dc18ea9d6"
DETECTOR_URL = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/yolox_x_8xb8-300e_humanart-a39d44ed.zip"


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def git_blob_sha1(path):
    """Git's content identity for a non-LFS blob, as supplied by HF metadata."""
    path = Path(path)
    value = hashlib.sha1(b"blob " + str(path.stat().st_size).encode("ascii") + b"\0")
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def download_range(url, partial, first, last, total_size):
    """One resumable exact range; reject servers that return a whole file."""
    expected_size = last - first + 1
    while True:
        for attempt in range(5):
            try:
                done = partial.stat().st_size if partial.exists() else 0
                if done == expected_size:
                    return
                if done > expected_size:
                    raise RuntimeError(f"Oversize range file: {partial}")
                requested_first = first + done
                # The local proxy stalls on very large Range bodies; use the
                # exact 4MiB request size verified by the recorded probe.
                requested_last = min(requested_first + 4 * 1024 * 1024 - 1, last)
                request = Request(url, headers={"Range": f"bytes={requested_first}-{requested_last}"})
                with urlopen(request, timeout=120) as source:
                    expected_header = f"bytes {requested_first}-{requested_last}/{total_size}"
                    if source.status != 206 or source.headers.get("Content-Range") != expected_header:
                        raise RuntimeError(f"Server did not honor exact Range {expected_header}")
                    with partial.open("ab") as output:
                        while block := source.read(1024 * 1024):
                            output.write(block)
                    if partial.stat().st_size != requested_last - first + 1:
                        raise OSError(f"Incomplete range: {partial}")
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(3 * (attempt + 1))


def download_segmented(url, target, size, segments):
    partials = [target.with_name(target.name + f".range{index:02d}") for index in range(segments)]
    boundaries = [(size * i // segments, size * (i + 1) // segments - 1) for i in range(segments)]
    with ThreadPoolExecutor(segments) as executor:
        futures = [executor.submit(download_range, url, part, first, last, size)
                   for part, (first, last) in zip(partials, boundaries)]
        for future in futures:
            future.result()
    merged = target.with_name(target.name + ".assembled.partial")
    with merged.open("wb") as output:
        for part in partials:
            with part.open("rb") as source:
                shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
    if merged.stat().st_size != size:
        raise RuntimeError(f"Assembled file has wrong size: {target.name}")
    merged.replace(target)
    return partials


def download_model(job):
    from huggingface_hub import hf_hub_download

    repo, revision, folder, info, transport, source_host, segments = job
    name, size = info.rfilename, info.size
    expected = None if info.lfs is None else info.lfs.sha256
    expected_blob = info.blob_id if info.lfs is None else None
    if not expected and not expected_blob:
        raise RuntimeError(f"Official metadata has no content hash: {repo}/{name}")
    target = folder / name
    source_url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
    download_url = f"https://{source_host}/{repo}/resolve/{revision}/{name}"
    range_partials = []
    target.parent.mkdir(parents=True, exist_ok=True)
    valid = target.is_file() and target.stat().st_size == size
    if valid and expected:
        valid = sha256(target) == expected
    elif valid:
        valid = git_blob_sha1(target) == expected_blob
    reused_existing = valid
    if not valid:
        if transport == "urllib":
            if segments > 1 and size > 100 * 1024 * 1024:
                range_partials = download_segmented(download_url, target, size, segments)
            else:
                download_single(download_url, target, size)
        else:
            hf_hub_download(repo, name, revision=revision, local_dir=folder, token=False,
                            force_download=target.exists())
    actual = sha256(target)
    actual_blob = git_blob_sha1(target) if expected_blob is not None else None
    if (target.stat().st_size != size or (expected is not None and actual != expected)
            or (expected_blob is not None and actual_blob != expected_blob)):
        # These are only the deterministic range files returned by our own
        # segmented downloader. A complete but corrupt chunk must not poison
        # every future retry just because its byte count is correct.
        for part in range_partials:
            part.unlink(missing_ok=True)
        raise RuntimeError(f"Integrity check failed: {repo}/{name}; "
                           f"size={target.stat().st_size}/{size}; sha256={actual}; "
                           f"expected_sha256={expected}; git_blob_sha1={actual_blob}; "
                           f"expected_git_blob_sha1={expected_blob}")
    for part in range_partials:
        part.unlink()
    print(f"Verified {name}: {size} bytes", flush=True)
    return {"repo": repo, "revision": revision, "file": name, "path": str(target),
            "size": size, "sha256": actual, "expected_sha256": expected,
            "git_blob_sha1": actual_blob, "expected_git_blob_sha1": expected_blob,
            "source_url": source_url, "requested_download_url": download_url,
            "download_url": None if reused_existing else download_url,
            "retrieval": "existing_file_verified" if reused_existing else "downloaded",
            "hash_source": "upstream_lfs" if expected else "upstream_git_blob",
            "status": "verified"}


def download_single(url, target, size):
    partial = target.with_name(target.name + ".partial")
    progress = target.with_name(target.name + ".progress.json")
    for attempt in range(5):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with urlopen(Request(url, headers=headers), timeout=120) as source:
                resume = offset > 0 and source.status == 206
                if resume and not source.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise RuntimeError("Server returned the wrong resume range")
                done, last_update = (offset if resume else 0), 0.0
                with partial.open("ab" if resume else "wb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                        done += len(block)
                        if time.monotonic() - last_update > 3:
                            progress.write_text(json.dumps({"bytes": done, "expected_size": size,
                                                           "updated_at": time.time()}), encoding="utf-8")
                            last_update = time.monotonic()
            if partial.stat().st_size != size:
                raise OSError(f"Incomplete download {target.name}: {partial.stat().st_size}/{size}")
            partial.replace(target)
            break
        except OSError:
            if attempt == 4:
                raise
            time.sleep(3 * (attempt + 1))


def download_detector(root):
    folder = root / "vendor/GEM-X/weights"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "yolox_humanart.zip"
    target = folder / "yolox_humanart.onnx"
    if not archive.exists():
        partial = archive.with_suffix(".zip.partial")
        with urlopen(DETECTOR_URL, timeout=120) as source, partial.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        partial.replace(archive)
    with ZipFile(archive) as source:
        bad = source.testzip()
        if bad is not None:
            raise RuntimeError(f"Detector ZIP CRC failed: {bad}")
        member = next(n for n in source.namelist() if n.endswith("/end2end.onnx"))
        expected_member = source.getinfo(member)
        actual_crc = 0
        if target.exists():
            with target.open("rb") as existing:
                for block in iter(lambda: existing.read(8 * 1024 * 1024), b""):
                    actual_crc = zlib.crc32(block, actual_crc)
        if (not target.exists() or target.stat().st_size != expected_member.file_size
                or actual_crc != expected_member.CRC):
            with source.open(member) as data, target.open("wb") as output:
                shutil.copyfileobj(data, output, length=1024 * 1024)
    print(f"Verified detector ZIP CRC; extracted {target.name}", flush=True)
    return {"file": target.name, "path": str(target), "size": target.stat().st_size,
            "sha256": sha256(target), "archive_sha256": sha256(archive), "source_url": DETECTOR_URL,
            "hash_source": "observed_hash_no_published_upstream_sha256", "status": "verified_zip_crc"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2, 3, 4))
    parser.add_argument("--transport", choices=("auto", "http", "urllib"), default="urllib")
    parser.add_argument("--source-host", choices=("huggingface.co", "hf-mirror.com"), default="huggingface.co")
    parser.add_argument("--segments", type=int, choices=(1, 4), default=1)
    args = parser.parse_args()
    if args.source_host != "huggingface.co" and args.transport != "urllib":
        parser.error("A mirror requires --transport urllib; API metadata always comes from the official Hub")
    root = args.root.resolve()
    evidence = root / "evidence/backend"
    evidence.mkdir(parents=True, exist_ok=True)
    status = {"status": "running", "source_commit": SOURCE_COMMIT, "files": [], "failed": [],
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    status_path = evidence / "asset_download_status.json"

    def persist(active):
        status["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        status["active"] = list(active)
        temporary = status_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
        temporary.replace(status_path)

    # Replace a previous attempt's verified state before even importing the Hub
    # client: dependency and metadata failures are part of this attempt too.
    persist([])
    stage = "dependency"
    try:
        os.environ.setdefault("HF_HOME", str(root / "runtime/hf-cache"))
        os.environ.setdefault("HF_XET_CACHE", str(root / "runtime/hf-cache/xet"))
        os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        if args.transport == "http":
            os.environ["HF_HUB_DISABLE_XET"] = "1"
        from huggingface_hub import HfApi

        api = HfApi(token=False)
        stage = "metadata"
        groups = [
            ("nvidia/SOMA-X", SOMA_REVISION, root / "vendor/GEM-X/assets/soma", [
                "SOMA_neutral.npz", "SOMA_template_rig.usda", "SOMA_procedural_transforms.json", "correctives_model.pt", "LICENSE"]),
            ("nvidia/GEM-X", GEM_REVISION, root / "vendor/GEM-X/weights", [
                "body_config.json", "body_video_native77.yaml", "motion_native77.yaml", "motion_stats_raw.npz",
                "motion_native77_e774_s292175.ckpt", "body_s240000.ckpt", "vitpose.pth", "LICENSE", "README.md"]),
        ]
        jobs = []
        for repo, revision, folder, names in groups:
            for attempt in range(5):
                try:
                    metadata = api.model_info(repo, revision=revision, files_metadata=True)
                    if metadata.sha != revision or metadata.gated:
                        raise RuntimeError(f"Expected public pinned model revision: {repo}")
                    break
                except Exception as error:
                    if attempt == 4:
                        raise
                    time.sleep(3 * (attempt + 1))
            siblings = {info.rfilename: info for info in metadata.siblings}
            jobs.extend((repo, revision, folder, siblings[name], args.transport, args.source_host, args.segments)
                        for name in names)
        stage = "download"
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(download_detector, root): "yolox_humanart.onnx"}
            futures.update({executor.submit(download_model, job): job[3].rfilename for job in jobs})
            while futures:
                persist(futures.values())
                finished, _ = wait(futures, timeout=3, return_when=FIRST_COMPLETED)
                for future in finished:
                    name = futures.pop(future)
                    try:
                        status["files"].append(future.result())
                    except Exception as error:
                        status["failed"].append({"file": name, "error": repr(error)})
                        print(f"FAILED {name}: {error!r}", flush=True)
    except Exception as error:
        status["status"] = "failed"
        status["failed"].append({"stage": stage, "error": repr(error)})
        persist([])
        print(f"FAILED {stage}: {error!r}", flush=True)
        raise SystemExit(1) from error
    status["status"] = "failed" if status["failed"] else "verified"
    persist([])
    if status["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
