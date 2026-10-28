"""Where to put the 9:16 window on every frame so the main character's face stays centred.

One pass over the clip's frames:
  * every frame: a colour histogram, compared with the last one, finds hard scene cuts;
  * about 4 times a second (and on the first frame of every shot): face detection.
Then for each shot separately:
  * lock onto the biggest face and keep following that same face;
  * gaps where it isn't found hold the last position; a shot with no face is centred;
  * small wobbles are ignored, and the path is smoothed with a symmetric (zero-phase)
    filter, so it never lags behind the face.
Because shots are smoothed separately, the crop jumps on the exact frame of a cut.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import cv2
import numpy as np

from .faces import Box, Detector


@dataclass
class Settings:
    detect_fps: float = 4.0          # face detections per second
    cut_threshold: float = 0.45      # histogram distance (0..1) that counts as a hard cut
    min_shot_s: float = 0.25         # ignore a "cut" this soon after the last one (flashes)
    deadband: float = 0.05           # ignore moves smaller than this share of the crop width
    smooth_s: float = 0.2            # gaussian sigma of the smoothing, in seconds
    relock_s: float = 1.5            # lost the face this long and another is visible: switch


@dataclass
class Track:
    crop_x: list[int]                       # left edge of the crop, per frame, source pixels
    shots: list[tuple[int, int]]            # [first, end) frame ranges
    faces_found: int = 0
    detections: int = 0
    samples: dict[int, list[Box]] = field(default_factory=dict, repr=False)


def _histogram(rgb: np.ndarray) -> np.ndarray:
    small = cv2.resize(rgb, (128, max(2, 128 * rgb.shape[0] // rgb.shape[1])), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 8, 8], [0, 180, 0, 256, 0, 256])
    return cv2.normalize(hist, hist).flatten()


def analyse(frames: Iterable[np.ndarray], count: int, fps: float, source_width: int, crop_width: int,
            detector: Detector, settings: Settings = Settings(),
            progress: Callable[[float], None] = lambda p: None) -> Track:
    """frames are small RGB copies of the source; boxes are scaled back to source pixels."""
    step = max(1, round(fps / settings.detect_fps))
    min_shot = max(1, round(settings.min_shot_s * fps))
    cuts = [0]
    samples: dict[int, list[Box]] = {}
    previous = last_rgb = None
    seen = 0

    def detect(i: int, rgb: np.ndarray) -> None:
        if i not in samples:
            k = source_width / rgb.shape[1]
            samples[i] = [b.scaled(k) for b in detector(rgb)]

    for i, rgb in enumerate(frames):
        seen = i + 1
        hist = _histogram(rgb)
        if previous is not None and i - cuts[-1] >= min_shot:
            if cv2.compareHist(previous, hist, cv2.HISTCMP_BHATTACHARYYA) > settings.cut_threshold:
                cuts.append(i)
                detect(i - 1, last_rgb)      # last frame of the old shot, so its end doesn't drift
        previous, last_rgb = hist, rgb
        if (i - cuts[-1]) % step == 0:
            detect(i, rgb)
        if i % 12 == 0:
            progress(seen / count)
    if last_rgb is not None:
        detect(seen - 1, last_rgb)
    progress(1.0)
    total = max(count, seen)
    shots = list(zip(cuts, cuts[1:] + [total]))
    crop_x = plan_path(shots, samples, total, fps, source_width, crop_width, settings)
    return Track(crop_x, shots, sum(1 for f in samples.values() if f), len(samples), samples)


def follow(samples: list[tuple[int, list[Box]]], fps: float, relock_s: float) -> list[tuple[int, float | None]]:
    """Face centre x per sample for the one character we lock onto (None where it's not seen)."""
    locked: Box | None = None
    last_seen = 0
    out: list[tuple[int, float | None]] = []
    for frame, faces in samples:
        match = None
        if locked is not None and faces:
            reach = max(locked.width * 1.5, 1.0)
            close = [b for b in faces
                     if abs(b.cx - locked.cx) <= reach and abs(b.cy - locked.cy) <= reach
                     and 0.4 <= b.area / max(locked.area, 1.0) <= 2.5]
            if close:
                match = min(close, key=lambda b: (b.cx - locked.cx) ** 2 + (b.cy - locked.cy) ** 2)
            elif frame - last_seen >= relock_s * fps:
                match = max(faces, key=lambda b: b.area)
        elif faces:
            match = max(faces, key=lambda b: b.area)
        if match is not None:
            locked, last_seen = match, frame
        out.append((frame, match.cx if match is not None else None))
    return out


def plan_path(shots: list[tuple[int, int]], samples: dict[int, list[Box]], total: int, fps: float,
              source_width: int, crop_width: int, settings: Settings = Settings()) -> list[int]:
    centre = source_width / 2
    xs = np.full(total, centre, dtype=float)
    for first, end in shots:
        points = follow([(f, samples[f]) for f in sorted(samples) if first <= f < end], fps, settings.relock_s)
        known = [(f, x) for f, x in points if x is not None]
        if not known:
            continue                                   # no face in this shot: stay centred
        frames, values, held = [], [], known[0][1]     # before the first sighting: use it
        for f, x in points:
            if x is None:
                x = held                               # face gone: hold where it was
            if abs(x - held) > settings.deadband * crop_width:
                held = x                               # a real move, not a wobble
            frames.append(f)
            values.append(held)
        path = np.interp(np.arange(first, end), frames, values)
        xs[first:end] = _smooth(path, settings.smooth_s * fps)
    left = np.clip(np.rint(xs - crop_width / 2), 0, source_width - crop_width)
    return [int(x) for x in left]


def _smooth(path: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur along time. Symmetric, so it adds no delay (zero phase)."""
    if sigma < 0.5 or len(path) < 3:
        return path
    radius = int(3 * sigma)
    t = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (t / sigma) ** 2)
    kernel /= kernel.sum()
    if len(path) > radius:
        # mirror the path through its end points, so a pan keeps going instead of slowing at the edges
        head = 2 * path[0] - path[radius:0:-1]
        tail = 2 * path[-1] - path[-2:-radius - 2:-1]
        padded = np.concatenate([head, path, tail])
    else:
        padded = np.pad(path, radius, mode="edge")
    return np.convolve(padded, kernel, mode="valid")
