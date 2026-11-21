"""Verify both panels against their actual inputs and compare copied audio packets."""

from fractions import Fraction
from itertools import zip_longest
import hashlib
import av
import numpy as np
import cv2


def audio_identity(path):
    stream_hash = hashlib.sha256()
    stamps = []
    with av.open(str(path)) as c:
        if not c.streams.audio:
            return None, stamps
        for p in c.demux(c.streams.audio[0]):
            if p.size:
                stream_hash.update(bytes(p))
                stamps.append(Fraction(p.pts) * p.time_base)
    return stream_hash.hexdigest(), stamps


def verify(source, comparison, frames, timeline):
    errors = []
    seen = 0

    def small(bgr):
        return cv2.resize(bgr, (160, 90), interpolation=cv2.INTER_AREA).astype(float)

    with av.open(str(source)) as src, av.open(str(comparison)) as out:
        for i, (a, b) in enumerate(zip_longest(src.decode(video=0), out.decode(video=0))):
            if a is None or b is None or i >= timeline["frames"]:
                raise ValueError("Source or preview ended early")
            if a.pts * a.time_base - Fraction(timeline["pts"][0]) != b.pts * b.time_base:
                raise ValueError(f"Timestamp mismatch at {i}")
            pixels = b.to_ndarray(format="bgr24")
            h = pixels.shape[0] // 2
            png = cv2.imdecode(
                np.fromfile(frames / f"{i + 1:06}.png", dtype=np.uint8), cv2.IMREAD_COLOR
            )
            if png is None or png.shape != pixels[h:].shape:
                raise ValueError(f"Native frame mismatch at {i}")
            err = [
                float(np.abs(small(ref) - small(panel)).mean())
                for ref, panel in [(a.to_ndarray(format="bgr24"), pixels[:h]), (png, pixels[h:])]
            ]
            if max(err) > 4.0:
                raise ValueError(f"Comparison panel mismatch at {i}: {err}")
            errors.append(err)
            seen += 1
    if seen != timeline["frames"]:
        raise ValueError("Frame coverage mismatch")
    a, t = audio_identity(source)
    b, u = audio_identity(comparison)
    if a != b or t != u:
        raise ValueError("Audio packet payload or timestamp changed")
    return dict(
        frames=seen,
        max_mean_panel_error=np.max(errors, axis=0).tolist(),
        audio_packets=len(t),
        audio_sha256=a,
    )


def verify_native_movie(source, movie, frames, timeline):
    worst = 0.0
    count = 0
    with av.open(str(movie)) as output:
        for i, frame in enumerate(output.decode(video=0)):
            if i >= timeline["frames"] or frame.pts * frame.time_base != Fraction(
                timeline["pts"][i]
            ) - Fraction(timeline["pts"][0]):
                raise ValueError(f"Native movie timestamp mismatch at {i}")
            raster = frame.to_ndarray(format="bgr24")
            expected = cv2.imdecode(
                np.fromfile(frames / f"{i + 1:06}.png", dtype=np.uint8), cv2.IMREAD_COLOR
            )
            if expected is None or raster.shape != expected.shape:
                raise ValueError(f"Native movie dimensions disagree at {i}")
            delta = cv2.absdiff(
                cv2.resize(raster, (160, 90), interpolation=cv2.INTER_AREA),
                cv2.resize(expected, (160, 90), interpolation=cv2.INTER_AREA),
            )
            worst = max(worst, float(delta.mean()))
            count = i + 1
    if count != timeline["frames"] or worst > 4:
        raise ValueError(f"Native movie coverage or image mismatch: {count} frames, error {worst}")
    audio, stamps = audio_identity(source)
    if (audio, stamps) != audio_identity(movie):
        raise ValueError("Native movie audio differs from the source")
    return dict(frames=count, max_mean_error=worst, audio_packets=len(stamps), audio_sha256=audio)
