"""Phone folder mode: a job file dropped in <folder>/jobs becomes clips in <folder>/<title>/.

The folder is shared with the phone by Syncthing, so the phone is the whole interface:
write a .txt with the link and the end times, and the clips appear in the same folder.
"""
from __future__ import annotations

import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from .cli import run
from .faces import Detector
from .media import safe_name, say
from .times import END, ClipperError, parse_list, parse_time

SETTLE_SECONDS = 3          # a file still being written or synced is left until it stops changing
IGNORE = "// Half-made clips stay on the server until they are finished\n*.partial.mp4\n"
_LINK = re.compile(r"https?://\S+")


def read_job(path: Path) -> tuple[str, float, list]:
    """Line 1: the link (or a file on the server). Then optional "start 0:45", then end times."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    source, start, ends = None, 0.0, []
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if source is None:
                link = _LINK.search(line)
                if link:
                    source = link.group(0)
                elif Path(line).expanduser().is_file():
                    source = line
                else:
                    raise ClipperError("Put the video link on the first line, then one end time per line.")
                continue
            if line.lower().startswith("start"):
                value = parse_time(line[5:].strip(" :=\t"))
                if value == END:
                    raise ClipperError('"start" can\'t be "end".')
                start = value
            elif "," in line:
                ends += parse_list(line)
            else:
                ends.append(parse_time(re.split(r"\s+", line, maxsplit=1)[0]))
        except ClipperError as e:
            if source is None:
                raise
            raise ClipperError(f"line {number}: {e}") from None
    if source is None:
        raise ClipperError("Put the video link on the first line, then one end time per line.")
    if not ends:
        raise ClipperError("Add the end time of each clip under the link, one per line, e.g. 4:12 then end.")
    return source, start, ends


def watch_once(folder: Path, downloads: Path, detector: Detector | str = "auto",
               failed: dict | None = None, preset: str = "faster") -> int:
    """Handle every job that is ready. Returns how many were tried."""
    folder, jobs = Path(folder), Path(folder) / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    ignore = folder / ".stignore"
    if not ignore.exists():
        ignore.write_text(IGNORE)
    failed = {} if failed is None else failed
    tried = 0
    for job in sorted(jobs.glob("*.txt")):
        if job.name.endswith(".status.txt"):
            continue
        stamp = job.stat().st_mtime
        if time.time() - stamp < SETTLE_SECONDS or failed.get(job.name) == stamp:
            continue
        tried += 1
        status_file = jobs / f"{job.stem}.status.txt"
        say(f"Job {job.name}")
        try:
            source, start, ends = read_job(job)
            status_file.write_text(f"Working on {job.name} ...\n")
            manifest = run(source, ends, start, out_root=folder, downloads=downloads,
                           detector=detector, preset=preset)
        except ClipperError as e:
            failed[job.name] = stamp
            status_file.write_text(f"Problem: {e}\n\nFix {job.name} and save it again.\n")
            say(f"Job {job.name} failed: {e}")
            continue
        failed.pop(job.name, None)
        status_file.unlink(missing_ok=True)
        folder_name = safe_name(manifest["title"])
        count = len(manifest["parts"])
        done = jobs / "done"
        done.mkdir(exist_ok=True)
        text = job.read_text(encoding="utf-8-sig").rstrip("\n")
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        (done / job.name).write_text(f"{text}\n\n# {when} Done: {count} clips in {folder.name}/{folder_name}\n")
        job.unlink()
        video = Path(manifest["video"])
        if video.resolve().is_relative_to(Path(downloads).resolve()):
            video.unlink(missing_ok=True)       # the clips are made; don't fill the server's disk
    return tried


def watch(folder: Path, downloads: Path, detector: str = "auto", preset: str = "faster",
          every: float = 5) -> None:
    say(f"Watching {Path(folder) / 'jobs'} for jobs. Stop with Ctrl+C.")
    failed: dict = {}
    while True:
        watch_once(folder, downloads, detector, failed, preset)
        time.sleep(every)
