"""ffmpeg/ffprobe/yt-dlp: probing, downloading, reading frames and writing the clips."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .times import ClipperError

OUT_W, OUT_H = 1080, 1920


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise ClipperError(f"{tool} is not installed. Install it with:  sudo apt install ffmpeg")


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float
    frames: int
    has_audio: bool

    @property
    def vertical(self) -> bool:
        """Already 9:16 (or narrower, within 5%): fit and pad instead of cropping."""
        return self.width / self.height <= OUT_W / OUT_H * 1.05


def probe(path: Path) -> VideoInfo:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
               f"Couldn't read {path.name}. Is it a video file?")
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ClipperError(f"{path.name} has no video in it.")
    rate = video.get("avg_frame_rate") or "0/0"
    if rate in ("0/0", "0/1"):
        rate = video["r_frame_rate"]
    fps = float(Fraction(rate))
    duration = float(video.get("duration") or data["format"]["duration"])
    width, height = int(video["width"]), int(video["height"])
    rotation = _rotation(video)
    if rotation in (90, 270):
        width, height = height, width
    frames = int(video.get("nb_frames") or 0) or _count_frames(path) or round(duration * fps)
    return VideoInfo(width, height, fps, duration, frames,
                     any(s["codec_type"] == "audio" for s in data["streams"]))


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return abs(int(side["rotation"])) % 360
    return abs(int(stream.get("tags", {}).get("rotate", 0))) % 360


def _count_frames(path: Path) -> int:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)], "")
    return int(out.strip() or 0)


def _run(cmd: list[str], error: str) -> str:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        tail = "\n".join(done.stderr.strip().splitlines()[-5:])
        raise ClipperError(f"{error}\n{tail}" if error else tail)
    return done.stdout


# ---------------------------------------------------------------- download

def safe_name(title: str) -> str:
    name = re.sub(r"[^\w\-]+", "_", title, flags=re.UNICODE).strip("_")
    return (name or "video")[:80]


def is_url(source: str) -> bool:
    return bool(re.match(r"https?://", source))


def download(url: str, folder: Path, max_height: int, say: Callable[[str], None]) -> tuple[Path, str]:
    """Download once into folder/<id>.mp4. A second run with the same link reuses the file."""
    try:
        import yt_dlp
    except ImportError:
        raise ClipperError("yt-dlp is not installed. Run:  pip install -r requirements.txt") from None
    folder.mkdir(parents=True, exist_ok=True)
    index_file = folder / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    known = index.get(url)
    if known and (folder / known["file"]).exists():
        say(f"Already downloaded: {known['title']}")
        return folder / known["file"], known["title"]

    fmt = (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/bv*[height<={max_height}]+ba/"
           f"b[height<={max_height}]/b")
    last: list = [None]

    def hook(d: dict) -> None:
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d["status"] == "downloading" and total:
            pct = int(100 * d.get("downloaded_bytes", 0) / total)
            what = "audio" if d.get("info_dict", {}).get("vcodec") == "none" else "video"
            if (what, pct // 10) != last[0]:
                last[0] = (what, pct // 10)
                status(f"  downloading {what} {pct}%")

    options = {
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        # YouTube needs a JavaScript runtime for most formats (1080p included): deno or node
        "js_runtimes": {"deno": {"path": None}, "node": {"path": None}},
    }
    say(f"Downloading {url} (up to {max_height}p) ...")
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    except Exception as e:  # yt-dlp raises many kinds; all mean the same thing to the user
        message = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
        raise ClipperError(f"Couldn't download the video: {message}") from None
    status("")
    if not path.exists():
        found = sorted(folder.glob(f"{info['id']}.*"))
        if not found:
            raise ClipperError("The download finished but the video file is missing.")
        path = found[0]
    index[url] = {"file": path.name, "title": info.get("title") or info["id"]}
    index_file.write_text(json.dumps(index, indent=2, ensure_ascii=False))
    return path, index[url]["title"]


# ---------------------------------------------------------------- frames

def read_frames(path: Path, start: float, frames: int, width: int, height: int) -> Iterator[np.ndarray]:
    """Decode exactly the frames the clip will contain, small, as RGB arrays.

    Uses the same accurate seek as write_clip, so frame k here is frame k in the output.
    """
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.6f}", "-i", str(path), "-frames:v", str(frames),
           "-an", "-sn", "-vf", f"scale={width}:{height}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    size = width * height * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------- output

def write_clip(src: Path, out: Path, start: float, frames: int, info: VideoInfo,
               crop_x: list[int] | None, crop_w: int, workdir: Path,
               progress: Callable[[float], None], preset: str = "faster", crf: int = 20,
               reframe: bool = True) -> None:
    """Cut [start, start+frames) frame-accurately, reframe to 1080x1920, H.264 + faststart.

    crop_x holds the left edge of the crop for every frame (source pixels). It is fed to
    ffmpeg's crop filter through sendcmd, so a cut snaps on its exact frame.
    """
    duration = frames / info.fps
    if not reframe:     # keep the original shape and size
        chain = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    elif info.vertical or crop_x is None:
        chain = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                 f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:black")
    else:
        commands = workdir / f"{out.stem}.cmd"
        lines, last = [], None
        for k, x in enumerate(crop_x):
            if x != last:
                # half a frame early so float rounding can never push it onto the next frame
                at = 0.0 if k == 0 else (k - 0.5) / info.fps
                lines.append(f"{at:.6f} crop@track x {x};")
                last = x
        commands.write_text("\n".join(lines) + "\n")
        chain = (f"sendcmd=f={_filter_path(commands)},"
                 f"crop@track=w={crop_w}:h={info.height}:x={crop_x[0]}:y=0:exact=1,"
                 f"scale={OUT_W}:{OUT_H}:flags=lanczos")
    chain += ",setsar=1,format=yuv420p"

    tmp = out.with_name(out.stem + ".partial.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-ss", f"{start:.6f}", "-i", str(src),
           "-filter_complex", f"[0:v]{chain}[v]", "-map", "[v]", "-frames:v", str(frames),
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-r", f"{info.fps:.6f}"]
    if info.has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.6f}"]
    cmd += ["-movflags", "+faststart", str(tmp)]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if line.startswith("out_time_us="):
            value = line.split("=", 1)[1].strip()
            if value.isdigit():
                progress(min(1.0, int(value) / 1e6 / duration))
    err = proc.stderr.read()
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        tail = "\n".join(err.strip().splitlines()[-5:])
        raise ClipperError(f"ffmpeg failed while making {out.name}:\n{tail}")
    tmp.replace(out)


def _filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


# extra places progress goes (the website shows it); each gets (kind, text) with kind "say" or "status"
listeners: list[Callable[[str, str], None]] = []


def say(text: str) -> None:
    print(text, flush=True)
    for listen in listeners:
        listen("say", text)


def status(text: str) -> None:
    """A line that rewrites itself on a terminal, and prints sparingly in logs."""
    for listen in listeners:
        listen("status", text)
    if sys.stdout.isatty():
        print("\r" + text.ljust(72), end="", flush=True)
