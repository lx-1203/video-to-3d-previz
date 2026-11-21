"""Measure editable set surfaces, repeated furniture and camera motion from local video."""

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import cv2
import numpy as np

if __package__:
    from . import fullclip_scene_entry_config as scene_config
else:
    import fullclip_scene_entry_config as scene_config

WIDTH = 480


def _hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path):
    return json.loads(Path(path).read_bytes())


def _stage(job, stage, filename):
    for receipt in sorted((job / "stages").glob(stage + "-*.json")):
        data = _read(receipt)
        if data.get("status") != "completed" or data.get("llm_mode") != "off":
            continue
        for item in data["outputs"]:
            path = Path(item["path"])
            if path.name == filename and path.resolve().is_relative_to(job.resolve()):
                if path.stat().st_size != item["bytes"] or _hash(path) != item["sha256"]:
                    raise ValueError(f"changed stage artifact {path}")
                return path
    raise ValueError(f"missing completed {stage} artifact {filename} in {job}")


def _gray(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if np.percentile(gray[::6, ::6], 75) < 52:
        return cv2.createCLAHE(3, (6, 6)).apply(np.uint8(np.sqrt(gray / 255) * 255))
    return gray


def _mask_boxes(image, boxes, original):
    h, w = image.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for box in boxes:
        x0, y0, x1, y1 = np.rint(
            np.array(box) * np.tile([w / original[0], h / original[1]], 2)
        ).astype(int)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        # A full-frame close-up box cannot stand in for a precise person silhouette.
        rect = (max(1, x0), max(1, y0), min(w - 1, x1) - max(1, x0), min(h - 1, y1) - max(1, y0))
        if min(rect[2:]) < 3:
            continue
        seg = np.zeros((h, w), np.uint8)
        try:
            cv2.grabCut(image, seg, rect, None, None, 2, cv2.GC_INIT_WITH_RECT)
        except cv2.error:
            seg[y0:y1, x0:x1] = 1
        keep = ((seg == 1) | (seg == 3)).astype(np.uint8)
        if np.count_nonzero(keep) < 0.04 * (x1 - x0) * (y1 - y0):
            if (x1 - x0) * (y1 - y0) < 0.65 * w * h:
                keep[y0:y1, x0:x1] = 1
        mask |= cv2.dilate(keep, np.ones((3, 3), np.uint8))
    return mask


def _observations(path, source_sha):
    if not path:
        return {}
    path = Path(path)
    if path.is_dir():
        matches = list(path.rglob("frames.jsonl"))
        if len(matches) != 1:
            raise ValueError("ambiguous or absent detection file")
        path = matches[0]
    receipt = _read(path.with_name("receipt.json"))
    if receipt["inputs"]["source"]["sha256"] != source_sha or receipt["outputs"][
        "frames.jsonl"
    ] != _hash(path):
        raise ValueError("person observations do not match source video or receipt")
    rows = [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]
    return {
        row["source_zero_based"]: [
            x["box_xyxy"] for x in row["raw_detections"] if x.get("score", 0) >= 0.25
        ]
        for row in rows
    }


def _frames(video, start, end):
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    frames = {}
    for i in range(start, end + 1):
        ok, frame = cap.read()
        if not ok:
            raise ValueError(f"missing source frame {i}")
        h, w = frame.shape[:2]
        frames[i] = cv2.resize(frame, (WIDTH, round(h * WIDTH / w)), interpolation=cv2.INTER_AREA)
    cap.release()
    return frames, (w, h)


def _camera(frames, views, boxes, original, intrinsic):
    """Track fixed background landmarks against a depth keyframe with LK and PnP."""
    h, w = next(iter(frames.values())).shape[:2]
    k = intrinsic.copy()
    k[0] *= w
    k[1] *= h
    inv = np.linalg.inv(k)
    base = min(views)
    cams = {base: np.eye(4)}
    scales = {base: 1.0}
    diagnostics = {}
    for sign, end in [(1, max(frames)), (-1, min(frames))]:
        anchor = base
        previous = base
        rel = np.eye(4)
        for i in range(base + sign, end + sign, sign):
            if previous == anchor:
                image = frames[anchor]
                mask = _mask_boxes(image, boxes.get(anchor, []), original)
                depth = cv2.resize(views[anchor], (w, h))
                allowed = (np.isfinite(depth) & (depth > 0) & (mask == 0)).astype(np.uint8) * 255
                pts = cv2.goodFeaturesToTrack(_gray(image), 640, 0.003, 4, mask=allowed)
                if pts is None:
                    pts = np.empty((0, 1, 2), np.float32)
                ref = pts.reshape(-1, 2)
                pix = np.rint(ref).astype(int)
                z = depth[pix[:, 1], pix[:, 0]] * scales[anchor]
                xyz = (np.column_stack((ref, np.ones(len(ref)))) @ inv.T * z[:, None]).astype(
                    np.float32
                )
                rel = np.eye(4)
            prev_gray, gray = _gray(frames[previous]), _gray(frames[i])
            if len(pts) >= 6:
                nextpts, stat, _ = cv2.calcOpticalFlowPyrLK(
                    prev_gray, gray, pts, None, winSize=(19, 19), maxLevel=3
                )
                back, stat2, _ = cv2.calcOpticalFlowPyrLK(
                    gray, prev_gray, nextpts, None, winSize=(19, 19), maxLevel=3
                )
                ok = (
                    (stat.ravel() > 0)
                    & (stat2.ravel() > 0)
                    & (np.linalg.norm((back - pts).reshape(-1, 2), axis=1) < 1.3)
                )
                pts, xyz = nextpts[ok], xyz[ok]
            success = False
            if len(pts) >= 8:
                success, r, t, inliers = cv2.solvePnPRansac(
                    xyz,
                    pts,
                    k,
                    None,
                    flags=cv2.SOLVEPNP_EPNP,
                    iterationsCount=75,
                    reprojectionError=2.8,
                    confidence=0.993,
                )
                success = bool(
                    success and inliers is not None and len(inliers) >= max(8, len(pts) * 0.3)
                )
                if success:
                    ii = inliers[:, 0]
                    r, t = cv2.solvePnPRefineLM(xyz[ii], pts[ii], k, None, r, t)
                    rr = cv2.Rodrigues(r)[0]
                    m = np.eye(4)
                    m[:3, :3], m[:3, 3] = rr, t.ravel()
                    # Degenerate low-texture frames can propose a camera behind the viewed surface.
                    delta = m @ np.linalg.inv(rel)
                    angle = np.linalg.norm(cv2.Rodrigues(delta[:3, :3])[0])
                    success = angle < 0.1 and np.linalg.norm(delta[:3, 3]) < 0.12 * np.median(
                        xyz[:, 2]
                    )
                    if success:
                        rel = m
            mode = "PnP" if success else "hold"
            if not success:
                gradient0 = cv2.Laplacian(prev_gray, cv2.CV_32F)
                gradient1 = cv2.Laplacian(gray, cv2.CV_32F)
                (dx, dy), strength = cv2.phaseCorrelate(gradient0, gradient1)
                if strength > 0.13 and max(abs(dx), abs(dy)) < min(h, w) * 0.055:
                    anchor_depth = np.nanmedian(views[anchor]) * scales[anchor]
                    rel[:2, 3] += [dx * anchor_depth / k[0, 0], dy * anchor_depth / k[1, 1]]
                    mode = "background phase translation"
            diagnostics[i] = {"mode": mode, "tracks": len(pts)}
            cams[i] = cams[anchor] @ np.linalg.inv(rel)
            if i in views:
                ds = cv2.resize(views[i], (w, h))
                if len(pts) >= 8:
                    # Match relative depth scale against reprojected tracked 3-D points.
                    px = np.clip(np.rint(pts[:, 0]).astype(int), [0, 0], [w - 1, h - 1])
                    estimate = (xyz @ rel[:3, :3].T + rel[:3, 3])[:, 2]
                    ratio = estimate / ds[px[:, 1], px[:, 0]]
                    vals = ratio[np.isfinite(ratio) & (ratio > 0)]
                    scales[i] = float(np.median(vals)) if len(vals) > 7 else scales[anchor]
                else:
                    scales[i] = scales[anchor]
                anchor = i
            previous = i
    if __package__:
        from .fullclip_camera_motion import stabilize
    else:
        from fullclip_camera_motion import stabilize
    metric_views = {i: view * scales[i] for i, view in views.items()}
    cams, diagnostics = stabilize(
        cams, frames, metric_views, boxes, original, intrinsic, diagnostics
    )
    return cams, scales, diagnostics


def _temporal_clean(frames, key, boxes, original):
    """Register nearby views and mark moving or human foreground as unobserved."""
    image = frames[key]
    h, w = image.shape[:2]
    target = _gray(image)
    ref = cv2.goodFeaturesToTrack(target, 550, 0.003, 4)
    observations = []
    middle = None
    first, last = min(frames), max(frames)
    times = sorted(set([key, *[max(first, min(last, key + d)) for d in (-24, -12, 12, 24)]]))
    for j in times:
        view = frames[j]
        m = _mask_boxes(view, boxes.get(j, []), original)
        if j != key:
            if ref is None or len(ref) < 10:
                continue
            pts, ok, _ = cv2.calcOpticalFlowPyrLK(
                target, _gray(view), ref, None, winSize=(23, 23), maxLevel=4
            )
            good = ok.ravel() > 0
            if good.sum() < 10:
                continue
            mat, inliers = cv2.findHomography(pts[good], ref[good], cv2.RANSAC, 2.3)
            if mat is None or inliers.sum() < 12:
                continue
            view = cv2.warpPerspective(view, mat, (w, h))
            invalid = cv2.warpPerspective(1 - m, mat, (w, h), flags=cv2.INTER_NEAREST) == 0
        else:
            invalid = m > 0
        sample = view.astype(np.float32)
        sample[invalid] = np.nan
        if j == key:
            middle = len(observations)
        observations.append(sample)
    cube = np.stack(observations)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(cube, axis=0)
    primary = observations[middle]
    valid = np.isfinite(primary[:, :, 0])
    has = np.isfinite(median[:, :, 0])
    # View-dependent parallax on repeated furniture is not evidence that the set moves.
    # Static primary pixels keep their real edges. A projected person region only uses
    # a background observation when two independently unmasked temporal samples agree.
    count = np.sum(np.isfinite(cube[:, :, :, 0]), axis=0)
    deviation = np.nan_to_num(np.abs(cube - median[None, :, :, :]), nan=255)
    consistent = (np.max(deviation, axis=3) < 8).sum(axis=0) >= 2
    recovered = (~valid) & has & (count >= 2) & consistent
    clean = np.where(valid[:, :, None], image, median)
    return np.nan_to_num(clean).astype(np.uint8), ~(valid | recovered)


def _extra_frames(start, end, anchor):
    return sorted(set([*range(start, end + 1, 48), start, end]) - {anchor})


def measure_scene(
    video,
    scene_jobs_json,
    output_dir,
    *,
    detections=None,
    geometry_runtime=None,
    heads_json=None,
    faces_json=None,
):
    """Return a source-bound JSON manifest for build_scene, across inclusive shot ranges."""
    import importlib.util

    module_path = Path(__file__).with_name("fullclip_scene_surfaces.py")
    spec = importlib.util.spec_from_file_location("orderflow_scene_surfaces", module_path)
    surfaces = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(surfaces)
    cv2.setNumThreads(2)
    cv2.setRNGSeed(732)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    video = Path(video)
    sha = _hash(video)
    jobs = _read(scene_jobs_json)
    boxes = _observations(detections, sha)
    head_boxes = {}
    for p, field in [(heads_json, "items"), (faces_json, "faces")]:
        if not p:
            continue
        annotations = _read(p)
        if annotations["source_sha256"] != sha:
            raise ValueError("face/head observations source SHA mismatch")
        for row in annotations["frames"]:
            known = boxes.setdefault(row["source_frame"], [])
            for item in row[field]:
                if item.get("score", 0) < 0.5:
                    continue
                box = item["bbox"]
                if field == "faces" or item.get("class_id") == 1:
                    head_boxes.setdefault(row["source_frame"], []).append(box)
                if not any(
                    old[0] <= box[0] and old[1] <= box[1] and old[2] >= box[2] and old[3] >= box[3]
                    for old in known
                ):
                    known.append(box)
    work = []
    wanted = []
    previous = None
    for j in jobs:
        job = Path(j["job"])
        geo = _stage(job, "geometry", "raw_geometry.npz")
        inf = _read(_stage(job, "geometry", "inference_receipt.json"))
        sel = _read(_stage(job, "select", "selection.json"))
        det = _read(_stage(job, "objects", "detections.json"))
        start, end = int(j["first"]), int(j["last"])
        if previous is not None and start != previous + 1:
            raise ValueError("shot intervals overlap or have gaps")
        previous = end
        anchor = sel["selected_frame"]
        if any(x["source_video"]["sha256"] != sha for x in [inf, sel, det]) or not (
            anchor == det["selected_frame"] == inf["selected_frame"]
        ):
            raise ValueError("stage source hash or keyframe mismatch")
        if (
            sel["source_start"] != start
            or sel["frame_count"] != end - start + 1
            or not start <= anchor <= end
        ):
            raise ValueError("selection interval differs from requested shot")
        if anchor not in boxes:
            boxes[anchor] = [x["box_xyxy"] for x in det["detections"] if x["class_id"] == 0]
        work.append((j, anchor, geo, det))
        if geometry_runtime:
            wanted.extend(_extra_frames(start, end, anchor))
    generation = output / ("set_geometry_" + uuid.uuid4().hex)
    generation.mkdir()
    depth_dir = output
    if wanted:
        rt = scene_config.runtime(geometry_runtime)
        expected_model = rt["depth_model"]["sha256"]
        cache_key = hashlib.sha256(
            json.dumps(
                [
                    sha,
                    expected_model,
                    wanted,
                    _hash(Path(__file__).with_name("fullclip_scene_depth.py")),
                ]
            ).encode()
        ).hexdigest()
        named = output / ("views_" + cache_key)

        def usable(folder):
            receipt = folder / "depth_views.json"
            if not receipt.exists():
                return False
            cache = _read(receipt)
            return (
                cache.get("source_sha256") == sha
                and cache.get("model_sha256") == expected_model
                and cache.get("frames") == wanted
                and all(
                    (folder / f"view_{i:07d}.npz").exists()
                    and _hash(folder / f"view_{i:07d}.npz") == cache.get("outputs", {}).get(str(i))
                    for i in wanted
                )
            )

        if usable(named):
            depth_dir = named
        elif not usable(output):
            temp = generation / "depth_views"
            temp.mkdir()
            subprocess.run(
                [
                    rt["geometry_python"],
                    str(Path(__file__).with_name("fullclip_scene_depth.py")),
                    "--runtime",
                    str(geometry_runtime),
                    "--video",
                    str(video),
                    "--frames",
                    ",".join(map(str, wanted)),
                    "--output",
                    str(temp),
                ],
                check=True,
            )
            if not usable(temp):
                raise ValueError("additional view cache failed source/model/hash checks")
            try:
                temp.rename(named)
                depth_dir = named
            except FileExistsError:
                depth_dir = named if usable(named) else temp
    manifest = {
        "schema": "orderflow.editable-set/1",
        "source_sha256": sha,
        "shots": [],
        "llm_mode": "off",
        "delivery_ready": False,
    }
    for j, anchor, geo, det in work:
        start, end = int(j["first"]), int(j["last"])
        frames, original = _frames(video, start, end)
        with np.load(geo) as a:
            intr = a["intrinsics"].astype(float)
            views = {anchor: np.where(a["mask"], a["depth"], np.nan)}
        for i in _extra_frames(start, end, anchor) if geometry_runtime else []:
            with np.load(depth_dir / f"view_{i:07d}.npz") as a:
                views[i] = np.where(a["mask"], a["depth"], np.nan)
        cams, scales, tracking = _camera(frames, views, boxes, original, intr)
        assembled = surfaces.measure_views(
            frames, views, boxes, original, cams, scales, intr, det, anchor, _temporal_clean
        )
        if not any(item["kind"] == "Seat back" for item in assembled["objects"]):
            spec = importlib.util.spec_from_file_location(
                "orderflow_scene_occluder", Path(__file__).with_name("fullclip_scene_occluder.py")
            )
            occ = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(occ)
            moving, proof = occ.moving_frame(
                frames, cams, intr, views, scales, head_boxes, original
            )
            if moving:
                assembled["objects"] = [
                    o for o in assembled["objects"] if o["kind"].startswith("Backing")
                ] + moving
                assembled["tracked_solid_frame"] = proof
            elif not any(o.get("foreground_candidate") for o in assembled["objects"]):
                _, unknown = _temporal_clean(frames, anchor, boxes, original)
                grid = (
                    cv2.resize(views[anchor], (frames[anchor].shape[1], frames[anchor].shape[0]))
                    * scales[anchor]
                )
                rounded = occ.fixtures(
                    frames, grid, cams[anchor], intr, anchor, (~unknown) & np.isfinite(grid)
                )
                # A rounded patch of light supplies appearance, not a supported solid in front
                # of an actor. Single-view line fragments similarly lack a rigid frame extent.
                backing = [o for o in assembled["objects"] if o["kind"].startswith("Backing")]
                if backing:
                    assembled["objects"] = backing
                    for item in backing:
                        item["background_luminance_only"] = True
                assembled["background_light_observations"] = len(rounded)
                assembled["observed_rounded_fixtures"] = 0
                aperture_spec = importlib.util.spec_from_file_location(
                    "orderflow_scene_aperture",
                    Path(__file__).with_name("fullclip_scene_aperture.py"),
                )
                aperture = importlib.util.module_from_spec(aperture_spec)
                aperture_spec.loader.exec_module(aperture)
                edge_prop, proof = aperture.clipped_fixture(frames, views, scales, cams, intr)
                if edge_prop and proof:
                    edge_frames = []
                    for f, x, y in proof["edge_observations"]:
                        for box in boxes.get(f, []):
                            bx0, by0, bx1, by1 = np.array(box) * np.tile(
                                [
                                    frames[f].shape[1] / original[0],
                                    frames[f].shape[0] / original[1],
                                ],
                                2,
                            )
                            if bx0 <= x <= bx1 and by0 <= y <= by1:
                                edge_frames.append(f)
                                break
                    if len(edge_frames) >= 2:
                        edge_prop[0]["occlusion_frames"] = edge_frames
                    proof["source_figure_overlap"] = edge_frames
                assembled["objects"].extend(edge_prop)
                assembled["clipped_fixture"] = proof
        assembled["occlusion_frames"] = surfaces.occlusion_evidence(
            assembled["objects"], frames, cams, intr, head_boxes, original
        )
        meshes = generation / f"shot_{start:07d}_geometry.json"
        meshes.write_text(
            json.dumps(assembled, separators=(",", ":"), allow_nan=False), encoding="utf8"
        )
        record = {
            "first": start,
            "last": end,
            "intrinsics": intr.tolist(),
            "camera_to_world": {str(f): np.round(m, 8).tolist() for f, m in cams.items()},
            "geometry_path": str(meshes.resolve()),
            "geometry_sha256": _hash(meshes),
            "views": sorted(views),
            "view_scales": scales,
            "camera_diagnostics": tracking,
            "source_geometry_sha256": _hash(geo),
            "room_stage_exit": j.get("exit_code"),
            "anchor": anchor,
            "method": "source depth and static feature geometry without mandatory visible floor",
            "metric_scale_verified": False,
        }
        depths = generation / f"shot_{start:07d}_depth.npz"
        order = sorted(views)
        np.savez_compressed(
            depths,
            frames=np.array(order),
            depth=np.stack([cv2.resize(views[i], (96, 54)) * scales[i] for i in order]).astype(
                np.float32
            ),
        )
        record.update(depth_samples=str(depths.resolve()), depth_sha256=_hash(depths))
        manifest["shots"].append(record)
        print(f"editable set {start}-{end}, {len(assembled['objects'])} objects", flush=True)
    dst = output / "fullclip_scene.json"
    temp = generation / "fullclip_scene.json.tmp"
    temp.write_text(json.dumps(manifest, separators=(",", ":"), allow_nan=False), encoding="utf8")
    temp.replace(dst)
    return str(dst.resolve())
