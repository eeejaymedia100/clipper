"""clipper: long anime video + list of end times -> vertical 1080x1920 clips."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import media
from .faces import Detector, make_detector
from .media import say, status
from .times import ClipperError, format_time, parse_list, parse_time, plan_segments, read_cuts_file
from .track import Settings, analyse


def run(source: str, ends: list, start: float = 0.0, out_root: Path = Path("clips"),
        downloads: Path = Path("downloads"), max_height: int = 1080, force: bool = False,
        detector: Detector | str = "auto", settings: Settings = Settings(), preset: str = "faster") -> dict:
    media.require_tools()
    if media.is_url(source):
        video, title = media.download(source, downloads, max_height, say)
    else:
        video = Path(source).expanduser()
        if not video.is_file():
            raise ClipperError(f"Can't find the video file {source}")
        title = video.stem
    info = media.probe(video)
    segments = plan_segments(ends, start, info.duration, info.fps, info.frames)

    name = media.safe_name(title)
    folder = Path(out_root) / name
    folder.mkdir(parents=True, exist_ok=True)
    manifest_file = folder / "manifest.json"
    old = {}
    if manifest_file.exists():
        try:
            old = {p["part"]: p for p in json.loads(manifest_file.read_text())["parts"]}
        except (ValueError, KeyError):
            old = {}
    manifest = {"title": title, "source": source, "video": str(video), "fps": info.fps,
                "width": 1080, "height": 1920, "parts": []}

    crop_w = min(info.width, round(info.height * 9 / 16 / 2) * 2)
    aw = min(640, info.width)
    ah = max(2, round(aw * info.height / info.width / 2) * 2)
    shape = "already vertical, fitting it" if info.vertical else f"cropping {crop_w}x{info.height} around faces"
    say(f"{title}\n  {info.width}x{info.height} {info.fps:.5g} fps, {format_time(info.duration)} long; "
        f"{len(segments)} clip(s); {shape}")

    digits = max(2, len(str(len(segments))))
    work = Path(tempfile.mkdtemp(prefix="clipper-"))
    try:
        for n, (a, b) in enumerate(segments, 1):
            file = f"{name}_part{n:0{digits}d}.mp4"
            entry = {"part": n, "start": format_time(a / info.fps), "end": format_time(b / info.fps),
                     "start_seconds": round(a / info.fps, 6), "end_seconds": round(b / info.fps, 6),
                     "duration": round((b - a) / info.fps, 6), "frames": b - a, "file": file}
            label = f"[{n}/{len(segments)}] {file}  {entry['start']} -> {entry['end']}"
            previous = old.get(n, {})
            same = all(previous.get(k) == entry[k] for k in ("start_seconds", "end_seconds", "file"))
            if (folder / file).exists() and same and not force:
                say(f"{label}  already done, skipping")
                manifest["parts"].append(previous)
                continue
            began = time.time()
            crop_x = None
            if not info.vertical:
                if isinstance(detector, str):
                    detector = make_detector(detector, say)
                track = analyse(media.read_frames(video, a / info.fps, b - a, aw, ah), b - a, info.fps,
                                info.width, crop_w, detector, settings,
                                lambda p: status(f"{label}  finding faces {p:4.0%}"))
                crop_x = track.crop_x
                entry["shots"] = len(track.shots)
                entry["face_seen"] = f"{track.faces_found}/{track.detections} checks"
            media.write_clip(video, folder / file, a / info.fps, b - a, info, crop_x, crop_w, work,
                             lambda p: status(f"{label}  encoding {p:4.0%}"), preset)
            status("")
            extra = f"  {entry['shots']} shot(s), face in {entry['face_seen']}" if crop_x else ""
            say(f"{label}  done in {time.time() - began:.0f}s{extra}")
            manifest["parts"].append(entry)
            manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    say(f"Finished: {len(segments)} clip(s) in {folder}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clipper",
        description="Turn a long video into back-to-back vertical (9:16) clips that follow the main face.",
        epilog="Example:  clipper https://youtu.be/XXXX --cuts cuts.txt --start 0:45")
    parser.add_argument("source", help="YouTube link or a video file on this machine")
    parser.add_argument("-c", "--cuts", metavar="FILE",
                        help="text file with one clip END time per line (default: cuts.txt)")
    parser.add_argument("-t", "--times", metavar="TIMES",
                        help='clip end times instead of a file, e.g. "4:12, 8:30, end"')
    parser.add_argument("-s", "--start", default="0", help="skip everything before this time (an intro)")
    parser.add_argument("--max-height", type=int, default=1080, help="download quality limit (default 1080)")
    parser.add_argument("-o", "--out", default="clips", help="output folder (default clips)")
    parser.add_argument("--downloads", default="downloads", help="where downloads are kept (default downloads)")
    parser.add_argument("-f", "--force", action="store_true", help="remake clips that are already done")
    parser.add_argument("--detector", choices=["auto", "imgutils", "opencv"], default="auto",
                        help="face detector (default auto: imgutils, falling back to OpenCV)")
    parser.add_argument("--preset", default="faster",
                        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"],
                        help="x264 speed/size trade-off (default faster; slower = smaller files)")
    args = parser.parse_args(argv)
    try:
        if args.times and args.cuts:
            raise ClipperError("Use --times or --cuts, not both.")
        if args.times:
            ends = parse_list(args.times)
        else:
            cuts = Path(args.cuts or "cuts.txt")
            if not args.cuts and not cuts.exists():
                raise ClipperError("No cut times. Make a cuts.txt (one end time per line) "
                                   "or pass --times \"4:12, 8:30, end\".")
            ends = read_cuts_file(cuts)
        start = parse_time(args.start)
        if start == "end":
            raise ClipperError("--start can't be \"end\".")
        run(args.source, ends, start, Path(args.out), Path(args.downloads), args.max_height,
            args.force, args.detector, preset=args.preset)
    except ClipperError as e:
        status("")
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to carry on where it left off.", file=sys.stderr)
        return 130
    return 0
