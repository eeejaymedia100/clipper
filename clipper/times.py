"""Reading the clip end times people type: "4:12", "1:02:30", "90", "end"."""
from __future__ import annotations

import re
from pathlib import Path

END = "end"
_NUMBER = r"\d+(?:\.\d+)?"


class ClipperError(Exception):
    """A problem we can explain in plain English. Printed without a traceback."""


def parse_time(text: str) -> float | str:
    """Seconds as a float, or END. Raises ClipperError with an example of what works."""
    t = text.strip().lower()
    if t == END:
        return END
    if re.fullmatch(_NUMBER, t):
        return float(t)
    parts = t.split(":")
    if 2 <= len(parts) <= 3 and all(re.fullmatch(r"\d+", p) for p in parts[:-1]) \
            and re.fullmatch(_NUMBER, parts[-1]):
        *bigger, seconds = parts
        values = [int(p) for p in bigger] + [float(seconds)]
        # the hours (or minutes, in m:ss) can be any size, the rest must be under 60
        if any(v >= 60 for v in values[1:]):
            raise ClipperError(f'"{text.strip()}" is not a real time: minutes and seconds must be under 60.')
        total = 0.0
        for v in values:
            total = total * 60 + v
        return total
    raise ClipperError(f'"{text.strip()}" is not a time. Write it like 4:12, 1:02:30, 90 or end.')


def format_time(seconds: float) -> str:
    """1:02:30.500 / 4:12.000 — always readable back by parse_time."""
    ms = round(seconds * 1000)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s = rest / 1000
    return f"{h}:{m:02d}:{s:06.3f}" if h else f"{m}:{s:06.3f}"


def parse_list(text: str) -> list[float | str]:
    """--times "4:12, 8:30 end" — commas and/or spaces between times."""
    tokens = [t for t in re.split(r"[,\s]+", text) if t]
    if not tokens:
        raise ClipperError("No cut times given. Example: --times \"4:12, 8:30, end\"")
    return [parse_time(t) for t in tokens]


def read_cuts_file(path: str | Path) -> list[float | str]:
    """One time per line. Anything after the time is a note. Blank lines and # lines are skipped."""
    path = Path(path)
    if not path.is_file():
        raise ClipperError(f"Can't find the cuts file {path}. Make a text file with one end time per line.")
    times: list[float | str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        first = re.split(r"[\s,]+", line, maxsplit=1)[0]
        try:
            times.append(parse_time(first))
        except ClipperError as e:
            raise ClipperError(f"{path.name} line {number}: {e}") from None
    if not times:
        raise ClipperError(f"{path.name} has no times in it. Put one end time per line, e.g. 4:12")
    return times


def plan_segments(ends: list[float | str], start: float, duration: float, fps: float,
                  total_frames: int) -> list[tuple[int, int]]:
    """Turn end times into back-to-back [first_frame, end_frame) ranges.

    Every boundary is snapped to the frame grid once, and each clip starts on the exact
    frame the previous one ended, so there can be no gap or overlap.
    """
    frame = 1 / fps
    if start < 0:
        raise ClipperError("--start can't be negative.")
    if start >= duration:
        raise ClipperError(f"--start {format_time(start)} is past the end of the video ({format_time(duration)}).")
    bounds = [round(start * fps)]
    for i, end in enumerate(ends, 1):
        label = f"Cut {i}"
        if end == END:
            if i != len(ends):
                raise ClipperError(f'{label} is "end" but more cuts come after it. "end" can only be the last one.')
            bounds.append(total_frames)
            continue
        if end > duration + frame:
            raise ClipperError(f"{label} ({format_time(end)}) is past the end of the video "
                               f"({format_time(duration)}). Use \"end\" for the last clip.")
        f = min(round(end * fps), total_frames)
        if f <= bounds[-1]:
            before = format_time(bounds[-1] / fps)
            raise ClipperError(f"{label} ({format_time(end)}) must come after the previous cut ({before}). "
                               "Times go in order, each one later than the last.")
        bounds.append(f)
    return list(zip(bounds, bounds[1:]))
