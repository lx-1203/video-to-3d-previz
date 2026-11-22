"""Image-only checks for source-matched foreground ordering of static seating."""

from pathlib import Path
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import sys
import av
import cv2
import numpy as np

if __package__:
    from .fullclip_guardrail_motion import motion_witnesses
    from .fullclip_guardrail_actor_visibility import torso_colour_witnesses
else:
    from fullclip_guardrail_motion import motion_witnesses
    from fullclip_guardrail_actor_visibility import torso_colour_witnesses


def patch(image, u, v):
    x, y = round(u), round(v)
    if not 2 <= x < image.shape[1] - 2 or not 2 <= y < image.shape[0] - 2:
        return None
    return np.median(image[y - 2 : y + 3, x - 2 : x + 3], axis=(0, 1))


def material_matches_cloth(target, reference, paths):
    """Require material disagreement plus an unbroken route to observed clothing."""
    difference = np.linalg.norm(target - reference)
    if difference < 24:
        return False
    for path in paths:
        if path is None:
            continue
        jump = np.max(np.linalg.norm(np.diff(path, axis=0), axis=1))
        spread = np.max(np.linalg.norm(path - target, axis=1))
        if jump < 15 and spread < 28 and difference > 1.7 * spread + 8:
            return True
    return False


def transfer_coats(source, frames, direct, shots, max_bytes=140_000_000):
    grays = {}
    actors = {}
    motion = {}
    shot = None
    committed = -1

    def flush(last):
        if not grays:
            return
        hi = max(grays)
        end = hi if last else hi - 18
        rays, _ = motion_witnesses(grays, actors, direct)
        for i, row in rays.items():
            if committed < i <= end:
                motion[i] = row
        for i in list(grays):
            if last or i < end - 17:
                del grays[i]
                del actors[i]
        return end

    with av.open(str(source)) as video:
        for i, fr in enumerate(video.decode(video=0)):
            row = frames[i]
            if "pts" in row and fr.pts * fr.time_base != Fraction(row["pts"]) * Fraction(
                row["time_base"]
            ):
                raise ValueError("Surface material source PTS mismatch")
            if row["shot_id"] != shot:
                if grays:
                    flush(True)
                shot = row["shot_id"]
            if shot not in shots:
                continue
            im = fr.to_ndarray(format="bgr24")
            new = torso_colour_witnesses(im, row["actors"])
            if new:
                direct[str(i)] = new
            grays[i] = fr.to_ndarray(format="gray")
            actors[i] = row["actors"]
            # A sliding overlap accommodates the full optical-flow path on both sides.
            limit = max(46, min(100, max_bytes // (2 * grays[i].nbytes)))
            if len(grays) >= limit:
                committed = flush(False)
        flush(True)
    return motion


def measure(request):
    source = Path(request["source"])
    people = request["people"]
    data = request["evidence"]
    pairs = request["pairs"]
    if not pairs:
        return data
    with source.open("rb") as inp:
        if (
            hashlib.file_digest(inp, "sha256").hexdigest() != people["source_sha256"]
            or data["source_sha256"] != people["source_sha256"]
        ):
            raise ValueError("Seating material evidence differs from source")
    direct = data.setdefault("observed_torso_cores", {})
    shots = {people["frames"][q["i"]]["shot_id"] for q in pairs}
    motion = transfer_coats(source, people["frames"], direct, shots)
    for i, row in motion.items():
        for tid, samples in row.items():
            direct.setdefault(str(i), {}).setdefault(tid, []).extend(samples)
    needs = defaultdict(list)
    for n, q in enumerate(pairs):
        needs[q["i"]].append((n, False))
        needs[q["k"]].append((n, True))
    target = {}
    reference = {}
    routes = {}
    with av.open(str(source)) as video:
        for i, fr in enumerate(video.decode(video=0)):
            if i not in needs:
                continue
            lab = cv2.cvtColor(fr.to_ndarray(format="bgr24"), cv2.COLOR_BGR2LAB)
            for n, ref in needs[i]:
                q = pairs[n]
                if ref:
                    reference[n] = patch(lab, *q["ref"])
                    continue
                point = np.array([q["u"], q["v"]])
                target[n] = patch(lab, *point)
                upper = [a for a in direct.get(str(i), {}).get(q["id"], []) if 0.1 < a[2] < 0.65]
                paths = []
                if len(upper) >= 2:
                    for a in upper:
                        pixels = [
                            patch(lab, *(f * np.asarray(a[:2]) + (1 - f) * point))
                            for f in np.linspace(0, 1, 7)
                        ]
                        if all(x is not None for x in pixels):
                            paths.append(np.array(pixels))
                routes[n] = paths
    verified = [
        q
        for n, q in enumerate(pairs)
        if target.get(n) is not None
        and reference.get(n) is not None
        and material_matches_cloth(target[n], reference[n], routes.get(n, []))
    ]
    frames_by_surface = defaultdict(set)
    for q in verified:
        frames_by_surface[q["id"], q["ob"]].add(q["i"])
    added = 0
    for q in verified:
        if sum(abs(i - q["i"]) <= 4 for i in frames_by_surface[q["id"], q["ob"]]) < 2:
            continue
        direct.setdefault(str(q["i"]), {}).setdefault(q["id"], []).append([q["u"], q["v"], q["t"]])
        added += 1
    data["material_coverage"] = {
        "reference_visible_surfaces": len(pairs),
        "material_witnesses": added,
        "source_motion_inferred": sum(len(v) for row in motion.values() for v in row.values()),
    }
    return data


if __name__ == "__main__":
    request, result = map(Path, sys.argv[1:])
    result.write_text(
        json.dumps(measure(json.loads(request.read_bytes())), separators=(",", ":")),
        encoding="utf-8",
    )
