"""Decode a source clock and propose cut boundaries without hand-entered scene ranges."""

from fractions import Fraction
from pathlib import Path
import av
import cv2
import numpy as np


def inspect_source(source):
    changes = []
    times = []
    previous = None
    with av.open(str(Path(source))) as stream:
        video = stream.streams.video[0]
        fps = Fraction(video.average_rate)
        timebase = Fraction(video.time_base)
        for frame in stream.decode(video):
            if frame.pts is None:
                raise ValueError("Source frame has no PTS")
            time = frame.pts * frame.time_base
            if times and abs(time - times[0] - len(times) / fps) > timebase / 2:
                raise ValueError("Variable-rate source requires a timestamp-aware render timeline")
            times.append(time)
            image = cv2.resize(
                frame.to_ndarray(format="bgr24"), (48, 27), interpolation=cv2.INTER_AREA
            )
            thumb = image.astype(float)
            changes.append(0.0 if previous is None else float(np.abs(previous - thumb).mean()))
            previous = thumb
        width, height = video.codec_context.width, video.codec_context.height
        audio = bool(stream.streams.audio)
    if not times:
        raise ValueError("Empty video track")
    cuts = []
    for i in range(2, len(changes) - 1):
        nearby = changes[max(1, i - 9) : i] + changes[i + 1 : i + 10]
        if changes[i] > max(15, 4 * np.median(nearby) + 3) and (not cuts or i - cuts[-1] > 4):
            cuts.append(i)
    return dict(
        width=width,
        height=height,
        fps=str(fps),
        pts=[str(t) for t in times],
        cuts=cuts,
        audio=audio,
        frames=len(times),
    )
