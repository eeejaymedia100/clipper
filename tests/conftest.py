"""A synthetic "anime" for the tests: a white box (the face) moving over a coloured
background, with one hard cut at exactly 5.0 s where the background and the box jump."""
from __future__ import annotations

import math
import subprocess

import numpy as np
import pytest

from clipper.faces import Box

W, H, FPS, SECONDS, CUT_S, BOX = 640, 360, 24, 10, 5.0, 60


def box_x(t: float) -> float:
    """Centre of the box at time t (source pixels)."""
    if t < CUT_S:
        return 320 + 180 * math.sin(2 * math.pi * t / 5)   # swings across, up to ~230 px/s
    return 110 + 30 * (t - CUT_S)                           # new shot: far left, drifting right


def make_frame(i: int, rng: np.random.Generator) -> np.ndarray:
    t = i / FPS
    colour = (20, 30, 90) if t < CUT_S else (100, 25, 20)
    frame = np.empty((H, W, 3), np.uint8)
    frame[:] = colour
    frame += rng.integers(0, 25, (H, W, 1), dtype=np.uint8)    # some grain, like real footage
    cx = round(box_x(t))
    frame[H // 2 - BOX // 2:H // 2 + BOX // 2, cx - BOX // 2:cx + BOX // 2] = 255
    return frame


def write_video(path, frames, size, seconds) -> None:
    w, h = size
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(FPS), "-i", "-", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-g", "48", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)], stdin=subprocess.PIPE)
    for frame in frames:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    assert proc.wait() == 0


@pytest.fixture(scope="session")
def box_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "Box Story Ep1.mp4"
    rng = np.random.default_rng(1)
    write_video(path, (make_frame(i, rng) for i in range(FPS * SECONDS)), (W, H), SECONDS)
    return path


@pytest.fixture(scope="session")
def vertical_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("src") / "tall.mp4"
    frames = (np.full((640, 360, 3), 80, np.uint8) for _ in range(FPS * 2))
    write_video(path, frames, (360, 640), 2)
    return path


def box_detector(rgb: np.ndarray) -> list[Box]:
    """Stands in for the anime face detector: finds the white box."""
    ys, xs = np.nonzero(rgb.min(axis=2) > 230)
    if len(xs) < 20:
        return []
    return [Box(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, 0.9)]
