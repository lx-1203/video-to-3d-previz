"""Measure narrow continuous horizontal members at observed lower-body crossings.

Two long flanks, a narrow paired edge and foreground evidence inside a pose-backed
body core are needed. Shot-local matching suppresses isolated lines and folds.
"""

import hashlib
import json
from fractions import Fraction
from pathlib import Path


def actors_at_rail(actors):
    import numpy as np

    cores = []
    for actor in actors:
        j = actor["joints2d"]

        def pair(name):
            obs = [j.get(side + "_" + name, {}) for side in ("left", "right")]
            if not all(
                p.get("visibility") in ("visible", "model_estimate", "observed")
                and p.get("score", 0) >= 0.35
                for p in obs
            ):
                return None
            return np.asarray([[p["u"], p["v"]] for p in obs])

        sh, hip = pair("shoulder"), pair("hip")
        if sh is None or hip is None:
            continue
        up = sh.mean(axis=0)
        base = hip.mean(axis=0)
        height = base[1] - up[1]
        if height < 13 or abs(up[0] - base[0]) > height * 0.65:
            continue
        span = max(abs(hip[0, 0] - hip[1, 0]), 0.53 * abs(sh[0, 0] - sh[1, 0]))
        if span < 7:
            continue
        cores.append(
            (
                str(actor["track_id"]),
                float(base[0]),
                float(0.35 * span),
                float(up[1] + 0.60 * height),
                float(base[1] + 0.46 * height),
            )
        )
    return cores


def measure_image(image, actors, detector):
    import cv2
    import numpy as np

    cores = actors_at_rail(actors)
    if not cores:
        return []
    h, w = image.shape[:2]
    scale = min(1.0, 900.0 / w)
    thumb = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    lines = detector.detect(cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY))[0]
    if lines is None:
        return []
    edges = []
    for x, y, xx, yy in lines[:, 0] / scale:
        if x > xx:
            x, y, xx, yy = xx, yy, x, y
        if xx - x < 0.028 * w or abs(yy - y) > 0.55 * (xx - x):
            continue
        m = float((yy - y) / (xx - x))
        intercept = float(y - m * x)
        edges.append((float(x), float(xx), m, intercept))
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    xs = np.arange(1, w - 1, max(1, round(w / 430)), dtype=int)
    accepted = []
    for x, xx, m, b in sorted(edges, key=lambda r: r[0] - r[1]):
        if xx - x < 0.09 * w or not any(
            top < m * cx + b < bottom for _, cx, r, top, bottom in cores
        ):
            continue
        if any(
            abs(m - v["slope"]) * 0.36 * w + abs((m - v["slope"]) * (0.5 * w) + b - v["intercept"])
            < 2.6
            for v in accepted
        ):
            continue
        group = [
            g
            for g in edges
            if abs(g[2] - m) < 0.040
            and abs((g[2] - m) * (max(g[0], min(g[1], 0.5 * (x + xx)))) + g[3] - b)
            < max(3.0, h * 0.007)
        ]
        lo = min(g[0] for g in group)
        hi = max(g[1] for g in group)
        if hi - lo < 0.28 * w:
            continue
        span = (xs >= lo) & (xs <= hi)
        fit = np.zeros_like(xs, dtype=bool)
        for a, z, _, _ in group:
            fit |= (xs >= a) & (xs <= z)
        if fit[span].mean() < 0.44:
            continue
        y = m * xs + b

        def sample(off):
            return lab[np.rint(y + off).astype(int).clip(0, h - 1), xs]

        best = None
        for thickness in np.unique(
            np.rint(np.linspace(max(2, h * 0.003), max(4, h * 0.015), 5))
        ).astype(int):
            for side in (-1, 1):
                center = side * thickness / 2
                # Reduce the influence of isolated highlights and compression noise.
                mid = (sample(center - 0.25 * thickness) + sample(center + 0.25 * thickness)) * 0.5
                upper = sample(center - thickness * 0.5 - 1.8)
                lower = sample(center + thickness * 0.5 + 1.8)
                top_delta = np.linalg.norm(mid - upper, axis=1)
                low_delta = np.linalg.norm(mid - lower, axis=1)
                sharp = np.minimum(top_delta, low_delta)
                close = np.linalg.norm(upper - lower, axis=1)
                # A lone edge widened by blur has a center between its two flanks.
                raised = (top_delta + low_delta - close) > 4
                supported = (sharp > 5.5) & raised & span
                anchors = []
                for tid, cx, r, top, bottom in cores:
                    cy = m * cx + b + center
                    if not top < cy < bottom or not lo + 0.55 * r < cx < hi - 0.55 * r:
                        continue
                    core = np.abs(xs - cx) < r
                    wings = ((xs > cx - 3.4 * r) & (xs < cx - 1.25 * r)) | (
                        (xs > cx + 1.25 * r) & (xs < cx + 3.4 * r)
                    )
                    left = (xs < cx - r) & (xs > lo)
                    right = (xs > cx + r) & (xs < hi)
                    if min(np.sum(left), np.sum(right)) < 4 or not core.any() or not wings.any():
                        continue
                    support = float(supported[core].mean())
                    flanks = min(supported[left].mean(), supported[right].mean())
                    if support < 0.52 or flanks < 0.28 or supported[wings].mean() < 0.31:
                        continue
                    # Require the same material on at least one side of the person's
                    # outline. A short unrelated crease cannot account for both flanks.
                    body_color = np.median(mid[core & supported], axis=0)
                    ring = wings & supported
                    if (
                        not ring.any()
                        or np.min(np.linalg.norm(mid[ring] - body_color, axis=1)) > 24
                    ):
                        continue
                    anchors.append(
                        dict(
                            track_id=tid,
                            u=round(cx, 2),
                            v=round(cy, 2),
                            core_halfwidth=round(r, 2),
                            core_support=round(support, 3),
                            flank_support=round(float(flanks), 3),
                        )
                    )
                if not anchors:
                    continue
                score = (
                    sum(q["core_support"] for q in anchors)
                    + 0.7 * float(supported[span].mean())
                    + 0.35 * float(fit[span].mean())
                )
                if best is None or score > best[0]:
                    best = (
                        score,
                        dict(
                            slope=m,
                            intercept=float(b + center),
                            x_min=lo,
                            x_max=hi,
                            thickness=float(thickness),
                            crossings=anchors,
                            score=round(score, 4),
                        ),
                    )
        if best:
            accepted.append(best[1])
    unique = []
    for record in sorted(accepted, key=lambda r: -r["score"]):
        if not any(
            abs((record["slope"] - p["slope"]) * w * 0.5 + record["intercept"] - p["intercept"])
            < max(5.0, h * 0.012)
            and abs(record["slope"] - p["slope"]) < 0.045
            for p in unique
        ):
            unique.append(record)
    return unique


def associate(frames, w, h, bounds, fps):
    tracks = []
    for frame, shot, rows in frames:
        used = set()
        for sample in rows:
            choices = []
            for i, track in enumerate(tracks):
                prev = track[-1]
                gap = frame - prev["frame"]
                if i in used or prev["shot"] != shot or not 0 < gap <= 6:
                    continue
                dx = sample["slope"] - prev["slope"]
                dist = abs(dx * 0.5 * w + sample["intercept"] - prev["intercept"])
                if abs(dx) < 0.023 + 0.002 * gap and dist < max(4.0, h * 0.009 * gap):
                    choices.append((dist + abs(dx) * w * 0.20, i))
            sample = dict(sample, frame=frame, shot=shot)
            if choices:
                _, i = min(choices)
                tracks[i].append(sample)
                used.add(i)
            else:
                used.add(len(tracks))
                tracks.append([sample])
    accepted = []
    for tr in tracks:
        lo, hi = bounds[tr[0]["shot"]]
        # Require about one second of repeatedly observed crossings, with a shorter
        # evidence window for short shots. This rejects transient mouldings and folds.
        required = max(4, min(int(fps * 0.5), int((hi - lo + 1) * 0.20)))
        if len(tr) >= required and tr[-1]["frame"] - tr[0]["frame"] >= min(6, hi - lo):
            accepted.append(tr)
    return accepted


def measure_guardrails(source, people, output):
    import av
    import cv2

    if __package__:
        from .fullclip_guardrail_panel import observe_profile
        from .fullclip_guardrail_actor_visibility import torso_colour_witnesses
    else:
        from fullclip_guardrail_panel import observe_profile
        from fullclip_guardrail_actor_visibility import torso_colour_witnesses

    data = people if isinstance(people, dict) else json.loads(Path(people).read_bytes())
    with Path(source).open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if data.get("source_sha256", digest) != digest:
        raise ValueError("Guardrail observations and video differ")
    cv2.setNumThreads(1)
    detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    measured = []
    torso_evidence = {}
    bounds = {}
    with av.open(str(source)) as video:
        stream = video.streams.video[0]
        stream.thread_count = 2
        if (stream.width, stream.height) != (data["width"], data["height"]):
            raise ValueError("Guardrail source size mismatch")
        count = 0
        fps = float(stream.average_rate or 24)
        for count, frame in enumerate(video.decode(stream), 1):
            i = count - 1
            row = data["frames"][i]
            if row.get("source_frame", i) != i or (
                "pts" in row
                and frame.pts * frame.time_base
                != Fraction(row["pts"]) * Fraction(row["time_base"])
            ):
                raise ValueError("Guardrail source frame map mismatch")
            shot = str(row["shot_id"])
            bounds.setdefault(shot, [i, i])[1] = i
            if not row["actors"]:
                continue
            image = frame.to_ndarray(format="bgr24")
            swatches = torso_colour_witnesses(image, row["actors"])
            if swatches:
                torso_evidence[str(i)] = swatches
            if (i - bounds[shot][0]) % 2:
                continue
            rails = measure_image(image, row["actors"], detector)
            for rail in rails:
                profile = observe_profile(image, rail, row["actors"], detector)
                if profile:
                    rail["panel_profile"] = profile
            measured.append((i, shot, rails))
    if count != len(data["frames"]):
        raise ValueError("Guardrail source length mismatch")
    result = dict(
        schema="orderflow.observed_guardrails.v1",
        width=data["width"],
        height=data["height"],
        source_sha256=digest,
        shot_bounds=bounds,
        frames=count,
        tracks=associate(measured, data["width"], data["height"], bounds, fps),
        observed_torso_cores=torso_evidence,
    )
    dst = Path(output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    from tempfile import NamedTemporaryFile
    import os

    with NamedTemporaryFile(mode="w", encoding="utf8", dir=dst.parent, delete=False) as f:
        tmp = f.name
        try:
            json.dump(result, f, allow_nan=False, separators=(",", ":"))
        except BaseException:
            f.close()
            Path(tmp).unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, dst)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return result


def bind_guardrails(scene, camera, evidence, binding_info):
    if __package__:
        from .fullclip_guardrail_bind import bind
    else:
        from fullclip_guardrail_bind import bind
    data = evidence if isinstance(evidence, dict) else json.loads(Path(evidence).read_bytes())
    return bind(scene, camera, data, binding_info)


def preflight_gauge(scene, camera, evidence, binding_info):
    """Infer one camera/set scale per shot from evaluated overlapping actor surfaces."""
    if __package__:
        from .fullclip_guardrail_bind import actor_meshes, sample_scene
        from .fullclip_guardrail_gauge import gauge_for_samples
    else:
        from fullclip_guardrail_bind import actor_meshes, sample_scene
        from fullclip_guardrail_gauge import gauge_for_samples
    data = evidence if isinstance(evidence, dict) else json.loads(Path(evidence).read_bytes())
    if camera.get("source_sha256", data["source_sha256"]) != data["source_sha256"]:
        raise ValueError("Camera and foreground measurements have different source videos")
    actors = actor_meshes(binding_info)
    result = {}
    initial = scene.frame_current
    try:
        for rows in data["tracks"]:
            if len(rows) < 15:
                continue
            samples = sample_scene(scene, camera, rows, data["width"], data["height"], actors)
            evidence = gauge_for_samples(samples, data["width"], data["height"])
            if evidence:
                first = data["shot_bounds"][str(rows[0]["shot"])][0]
                if (
                    first not in result
                    or result[first]["crossing_views"] < evidence["crossing_views"]
                ):
                    result[first] = evidence
    finally:
        scene.frame_set(initial)
    return result
