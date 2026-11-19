"""Validate generation-segment durations without rewriting the production plan.

Expected input:
{
  "generation_segments": [
    {"segment_id": "...", "logical_shot_id": "...", "duration_seconds": 6}
  ]
}

Optional plan constraints use:
{
  "duration_constraints": {"min_seconds": 4, "max_seconds": 8}
}

CLI --min/--max select single-shot plan constraints within the 4-8s policy.
Items default to generation_mode="single_shot" for compatibility.
Multi-shot items use generation_mode="multi_shot", distinct shot_ids, and
platform_limits={"verified": true, "max_seconds": 30}. Optional platform
min_seconds and allowed_seconds also apply; there is no global multi-shot cap.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys


def number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def platform_bounds(value):
    """Validate recorded platform capabilities, not their real-world provenance."""
    if not isinstance(value, dict) or value.get('verified') is not True:
        return None
    lower, upper = value.get('min_seconds', 0), value.get('max_seconds')
    if not number(lower) or not number(upper) or lower < 0 or upper <= 0 or lower > upper:
        return None
    allowed = value.get('allowed_seconds')
    if allowed is not None and (not isinstance(allowed, list) or not allowed or any(
            not number(v) or v <= 0 or not lower <= v <= upper for v in allowed)):
        return None
    return lower, upper, allowed


def validate(data: dict, min_seconds: float, max_seconds: float) -> dict:
    min_seconds, max_seconds = max(4, min_seconds), min(8, max_seconds)
    errors = []
    segments = data.get("generation_segments")
    if not isinstance(segments, list) or not segments:
        errors.append(
            {
                "code": "SCHEMA",
                "path": "generation_segments",
                "message": "Expected a nonempty array",
            }
        )
        segments = []
    identifiers = set()
    durations = []
    for index, item in enumerate(segments):
        path = f"generation_segments.{index}"
        if not isinstance(item, dict):
            errors.append(
                {"code": "SCHEMA", "path": path, "message": "Expected an object"}
            )
            continue
        identifier = item.get("segment_id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(
                {
                    "code": "SCHEMA",
                    "path": path + ".segment_id",
                    "message": "Expected a nonempty string",
                }
            )
        elif identifier in identifiers:
            errors.append(
                {
                    "code": "DUPLICATE_ID",
                    "path": path + ".segment_id",
                    "message": identifier,
                }
            )
        else:
            identifiers.add(identifier)
        mode = item.get('generation_mode', 'single_shot')
        if mode not in ('single_shot', 'multi_shot'):
            errors.append({'code': 'GENERATION_MODE', 'path': path + '.generation_mode',
                           'message': 'Expected single_shot or multi_shot'})
            continue
        if mode == 'multi_shot':
            shots = item.get('shot_ids')
            if (not isinstance(shots, list) or len(shots) < 2
                    or any(not isinstance(s, str) or not s.strip() for s in shots)
                    or len(set(shots)) != len(shots)):
                errors.append({'code': 'SHOT_IDS', 'path': path + '.shot_ids',
                               'message': 'Multi-shot packages require at least two distinct shot IDs'})
        duration = item.get("duration_seconds")
        if not number(duration) or duration <= 0:
            errors.append(
                {
                    "code": "SCHEMA",
                    "path": path + ".duration_seconds",
                    "message": "Expected a finite positive number",
                }
            )
            continue
        durations.append(duration)
        if mode == 'single_shot' and (duration < min_seconds or duration > max_seconds):
            errors.append(
                {
                    "code": "DURATION_OUT_OF_RANGE",
                    "path": path + ".duration_seconds",
                    "message": (
                        f"{identifier or index}: {duration:g}s is outside "
                        f"{min_seconds:g}-{max_seconds:g}s"
                    ),
                }
            )
        if mode == 'multi_shot' or 'platform_limits' in item:
            bounds = platform_bounds(item.get('platform_limits'))
            if bounds is None:
                errors.append({'code': 'PLATFORM_LIMITS', 'path': path + '.platform_limits',
                               'message': 'Require verified platform limits with a finite positive max_seconds'})
                continue
            lower, upper, allowed = bounds
            if duration < lower or duration > upper or (allowed is not None and not any(
                    math.isclose(duration, v, rel_tol=0, abs_tol=1e-6) for v in allowed)):
                errors.append({'code': 'PLATFORM_DURATION', 'path': path + '.duration_seconds',
                               'message': f'{identifier or index}: {duration:g}s is not supported by recorded platform limits'})
    return {
        "status": "failed" if errors else "passed",
        "constraints": {
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "applies_to": "single_shot only; multi_shot uses each item's verified platform_limits",
        },
        "segment_count": len(segments),
        "duration_min": min(durations) if durations else None,
        "duration_max": max(durations) if durations else None,
        "errors": errors,
        "note": "Passing timing validation does not prove visual or audio quality.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--min", dest="min_seconds", type=float)
    parser.add_argument("--max", dest="max_seconds", type=float)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        data = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "invalid_input",
                    "errors": [
                        {"code": "INPUT", "path": str(args.plan), "message": str(error)}
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if not isinstance(data, dict) or not isinstance(data.get('duration_constraints', {}), dict):
        print(json.dumps({'status': 'invalid_input', 'errors': [
            {'code': 'SCHEMA', 'message': 'Expected a plan object and duration_constraints object'}]}))
        return 2
    configured = data.get("duration_constraints", {})
    min_seconds = (
        args.min_seconds
        if args.min_seconds is not None
        else configured.get("min_seconds", 4)
    )
    max_seconds = (
        args.max_seconds
        if args.max_seconds is not None
        else configured.get("max_seconds", 8)
    )
    if (
        not number(min_seconds)
        or not number(max_seconds)
        or min_seconds < 0
        or max_seconds < min_seconds
    ):
        print(
            json.dumps(
                {
                    "status": "invalid_input",
                    "errors": [
                        {
                            "code": "CONSTRAINT",
                            "path": "duration_constraints",
                            "message": "Require 0 <= min <= max",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    report = validate(data, float(min_seconds), float(max_seconds))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
