"""The clipper website: paste a link, the server downloads and keeps the video, then make one
9:16 clip at a time from a start and an end time until the whole video is clipped.

Everything heavy runs on the server in one worker thread, so closing the page never stops it.
Clip downloads are plain files with a size and Range support, so the phone's download manager
keeps going in the background and resumes.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

from . import media
from .cli import run
from .faces import make_detector
from .times import END, ClipperError, format_time, parse_time
from .web_page import PAGE

MAX_HEIGHT = 1440               # sharper 9:16 crops than 1080p, still quick to download and decode
MIN_FREE_BYTES = 5 * 10**9      # never let downloads fill the disk the other sites live on


class Store:
    """Everything the site knows, kept in data/state.json so a restart picks up where it was."""

    def __init__(self, data: Path):
        self.data = data
        self.library = data / "library"
        self.previews = data / "previews"
        self.clips = data / "clips"
        for d in (self.library, self.previews, self.clips):
            d.mkdir(parents=True, exist_ok=True)
        self.file = data / "state.json"
        self.lock = threading.RLock()
        self.state = json.loads(self.file.read_text()) if self.file.exists() else {"videos": {}, "jobs": {}}

    def save(self) -> None:
        with self.lock:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False))
            tmp.replace(self.file)

    def update(self, kind: str, key: str, **fields) -> None:
        with self.lock:
            if key in self.state[kind]:
                self.state[kind][key].update(fields)
                self.save()

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))


class Worker:
    def __init__(self, store: Store, detector="auto", preset: str = "faster"):
        self.store = store
        self.tasks: queue.Queue = queue.Queue()
        self.previews: queue.Queue = queue.Queue()   # separate, so a preview never holds up a clip
        self.detector_name = detector if isinstance(detector, str) else "auto"
        self.detector = None if isinstance(detector, str) else detector
        self.preset = preset
        self.current: tuple[str, str] | None = None
        media.listeners.append(self.heard)
        # anything left half done when the server stopped goes back in the queue
        for vid, v in store.state["videos"].items():
            if v["status"] in ("waiting", "downloading", "preview"):
                store.update("videos", vid, status="waiting", progress="")
                self.tasks.put(("video", vid))
            elif v["status"] == "ready" and not v.get("preview"):
                self.previews.put(vid)
        for jid, j in sorted(store.state["jobs"].items(), key=lambda kv: kv[1]["created"]):
            if j["status"] in ("waiting", "working"):
                store.update("jobs", jid, status="waiting", progress="")
                self.tasks.put(("job", jid))
        threading.Thread(target=self.loop, daemon=True).start()
        threading.Thread(target=self.preview_loop, daemon=True).start()

    def heard(self, kind: str, text: str) -> None:
        if self.current and text.strip():      # kept in memory only; the next real update saves it
            what, key = self.current
            with self.store.lock:
                item = self.store.state["videos" if what == "video" else "jobs"].get(key)
                if item is not None:
                    item["progress"] = text.strip()

    def loop(self) -> None:
        while True:
            what, key = self.tasks.get()
            self.current = (what, key)
            try:
                if what == "video":
                    self.download(key)
                else:
                    self.clip(key)
            except Exception as e:  # keep the worker alive whatever happens; show the reason
                kind = "videos" if what == "video" else "jobs"
                message = str(e) if isinstance(e, ClipperError) else f"Something went wrong: {e}"
                self.store.update(kind, key, status="failed", message=message, progress="")
            finally:
                self.current = None

    def download(self, vid: str) -> None:
        store = self.store
        v = store.state["videos"].get(vid)
        if not v:
            return
        free = shutil.disk_usage(store.data).free
        if free < MIN_FREE_BYTES + 10**9:
            raise ClipperError(f"The server is nearly full ({free / 1e9:.1f} GB free). "
                               "Delete a video you've finished with first.")
        store.update("videos", vid, status="downloading", message="")
        path, title = media.download(v["url"], store.library, MAX_HEIGHT, media.say,
                                     max_bytes=free - MIN_FREE_BYTES)
        info = media.probe(path)
        # ready to clip straight away; the preview for finding times is made alongside
        store.update("videos", vid, status="ready", title=title, file=path.name, duration=info.duration,
                     width=info.width, height=info.height, size=path.stat().st_size, progress="", preview=False)
        self.previews.put(vid)

    def preview_loop(self) -> None:
        while True:
            vid = self.previews.get()
            v = self.store.state["videos"].get(vid)
            if not v or not v.get("file"):
                continue
            preview = self.store.previews / f"{vid}.mp4"
            if not preview.exists():
                part = preview.with_name(f"{vid}.partial.mp4")
                done = subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-threads", "2", "-i", str(self.store.library / v["file"]),
                     "-vf", "scale=-2:360,fps=15", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34",
                     "-g", "30", "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-movflags", "+faststart", str(part)],
                    capture_output=True)
                if done.returncode == 0:
                    part.replace(preview)
                else:
                    part.unlink(missing_ok=True)
            self.store.update("videos", vid, preview=preview.exists())

    def clip(self, jid: str) -> None:
        store = self.store
        j = store.state["jobs"].get(jid)
        if not j:
            return
        v = store.state["videos"].get(j["video"])
        if not v:
            raise ClipperError("That video was deleted.")
        if v["status"] != "ready":
            raise ClipperError(f"The video isn't saved yet: {v.get('message') or v['status']}")
        store.update("jobs", jid, status="working", message="")
        if self.detector is None:
            self.detector = make_detector(self.detector_name, media.say)
        work = store.clips / jid
        manifest = run(str(store.library / v["file"]), [j["end"]], j["start"], out_root=work,
                       detector=self.detector, preset=self.preset, crf=18, title=v["title"])
        made = work / media.safe_name(v["title"]) / manifest["parts"][0]["file"]
        name = f"{media.safe_name(v['title'])}_part{j['part']:02d}.mp4"
        made.replace(work / name)
        shutil.rmtree(made.parent, ignore_errors=True)
        store.update("jobs", jid, status="done", progress="", file=name, size=(work / name).stat().st_size,
                     end=manifest["parts"][0]["end_seconds"])


def check_public(url: str) -> None:
    """Refuse links that point back into this server or its private network."""
    host = urlparse(url).hostname or ""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return      # unknown name: the download itself will explain
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise ClipperError("That link points inside the server. Use a public video link.")


def read_time(text, what: str):
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return parse_time(text)
    except ClipperError as e:
        raise ClipperError(f"{what}: {e}") from None


# ---------------------------------------------------------------- the website

def make_handler(store: Store, worker: Worker):

    class Handler(BaseHTTPRequestHandler):
        server_version = "clipper"

        def log_message(self, fmt, *args):  # quiet: only errors go to the journal
            pass

        def send(self, code: int, body: bytes | str, kind: str = "text/html; charset=utf-8"):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, data, code: int = 200):
            self.send(code, json.dumps(data, ensure_ascii=False), "application/json")

        def error(self, message: str, code: int = 400):
            self.json({"error": message}, code)

        def send_file(self, path: Path, name: str, attachment: bool):
            if not path.is_file():
                return self.error("That file is gone.", 404)
            size = path.stat().st_size
            start, end = 0, size - 1
            ranged = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if ranged and (ranged.group(1) or ranged.group(2)):
                if ranged.group(1):
                    start = int(ranged.group(1))
                    end = min(int(ranged.group(2)), size - 1) if ranged.group(2) else size - 1
                else:
                    start = max(0, size - int(ranged.group(2)))
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if ranged:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            disposition = "attachment" if attachment else "inline"
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(name)}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with path.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                return self.send(200, "ok", "text/plain")
            if path == "/":
                return self.send(200, PAGE)
            if path == "/api/state":
                state = store.snapshot()
                state["free_gb"] = round(shutil.disk_usage(store.data).free / 1e9, 1)
                return self.json(state)
            parts = path.strip("/").split("/")
            try:
                if parts[:2] == ["media", "preview"] and len(parts) == 3:
                    vid = parts[2].removesuffix(".mp4")
                    if vid in store.state["videos"]:
                        return self.send_file(store.previews / f"{vid}.mp4", f"{vid}.mp4", False)
                if parts[:2] == ["media", "clip"] and len(parts) == 3:
                    j = store.state["jobs"].get(parts[2])
                    if j and j.get("file"):
                        return self.send_file(store.clips / parts[2] / j["file"], j["file"], True)
            except (BrokenPipeError, ConnectionResetError):
                return
            self.error("Not found.", 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.error("Send JSON.", 415)     # a form on another site can't send JSON
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 100_000:
                    raise ValueError
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                return self.error("That request didn't make sense.")
            try:
                if path == "/api/video":
                    return self.json(self.add_video(data))
                if path == "/api/clip":
                    return self.json(self.add_clip(data))
                m = re.fullmatch(r"/api/(videos|jobs)/([\w-]+)/delete", path)
                if m:
                    return self.json(self.delete(m.group(1), m.group(2)))
                m = re.fullmatch(r"/api/(videos|jobs)/([\w-]+)/retry", path)
                if m and m.group(2) in store.state[m.group(1)]:
                    if store.state[m.group(1)][m.group(2)]["status"] != "failed":
                        raise ClipperError("Only things that failed can be tried again.")
                    store.update(m.group(1), m.group(2), status="waiting", message="", progress="")
                    worker.tasks.put(("video" if m.group(1) == "videos" else "job", m.group(2)))
                    return self.json({"ok": True})
            except ClipperError as e:
                return self.error(str(e))
            self.error("Not found.", 404)

        def add_video(self, data: dict) -> dict:
            link = re.search(r"https?://\S+", str(data.get("url") or ""))
            if not link:
                raise ClipperError("Paste a video link (it starts with https://).")
            url = link.group(0)
            check_public(url)
            with store.lock:
                videos = store.state["videos"]
                vid = next((k for k, v in videos.items() if v["url"] == url and v["status"] != "failed"), None)
                if vid is None:          # the same link again opens the saved copy: no second download
                    vid = uuid.uuid4().hex[:10]
                    videos[vid] = {"url": url, "title": url, "status": "waiting", "progress": "",
                                   "message": "", "added": time.time()}
                    store.save()
                    worker.tasks.put(("video", vid))
            return {"video": vid}

        def add_clip(self, data: dict) -> dict:
            vid = str(data.get("video") or "")
            with store.lock:
                v = store.state["videos"].get(vid)
                if not v:
                    raise ClipperError("That video isn't saved any more. Paste the link again.")
                if v["status"] != "ready":
                    raise ClipperError("Wait for the video to finish downloading.")
                start = read_time(data.get("start"), "Start")
                start = 0.0 if start is None else start
                end = read_time(data.get("end"), "End")
                end = END if end is None else end
                if start == END:
                    raise ClipperError('The start can\'t be "end".')
                if start >= v["duration"]:
                    raise ClipperError(f"The start is past the end of the video ({format_time(v['duration'])}).")
                if end != END:
                    if end <= start:
                        raise ClipperError("The end must be after the start.")
                    if end > v["duration"] + 0.05:
                        raise ClipperError(f"The end is past the end of the video ({format_time(v['duration'])}). "
                                           "Leave it empty to clip to the end.")
                part = 1 + sum(1 for j in store.state["jobs"].values() if j["video"] == vid)
                jid = uuid.uuid4().hex[:10]
                store.state["jobs"][jid] = {"video": vid, "start": start,
                                            "end": v["duration"] if end == END else end, "part": part,
                                            "status": "waiting", "progress": "", "message": "",
                                            "created": time.time()}
                store.save()
                worker.tasks.put(("job", jid))
            return {"job": jid}

        def delete(self, kind: str, key: str) -> dict:
            with store.lock:
                item = store.state[kind].get(key)
                if not item:
                    return {"ok": True}
                if worker.current == ("video" if kind == "videos" else "job", key):
                    raise ClipperError("It's being worked on. Delete it when it's finished.")
                if kind == "videos":
                    if any(j["video"] == key and j["status"] in ("waiting", "working")
                           for j in store.state["jobs"].values()):
                        raise ClipperError("A clip of this video is being made. Delete it when that's done.")
                    if item.get("file"):
                        (store.library / item["file"]).unlink(missing_ok=True)
                        index_file = store.library / "index.json"
                        if index_file.exists():
                            index = json.loads(index_file.read_text())
                            index.pop(item["url"], None)
                            index_file.write_text(json.dumps(index, indent=2, ensure_ascii=False))
                    (store.previews / f"{key}.mp4").unlink(missing_ok=True)
                    for jid in [j for j, job in store.state["jobs"].items() if job["video"] == key]:
                        shutil.rmtree(store.clips / jid, ignore_errors=True)
                        del store.state["jobs"][jid]
                else:
                    shutil.rmtree(store.clips / key, ignore_errors=True)
                del store.state[kind][key]
                store.save()
            return {"ok": True}

    return Handler


def serve(data: Path, host: str, port: int, detector="auto", preset: str = "faster") -> ThreadingHTTPServer:
    data.mkdir(parents=True, exist_ok=True)
    store = Store(data)
    worker = Worker(store, detector, preset)
    server = ThreadingHTTPServer((host, port), make_handler(store, worker))
    server.daemon_threads = True
    return server


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="clipper web", description="Run the clipper website.")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=4880)
    parser.add_argument("--data", default="~/clipper-data", help="where videos and clips are kept")
    parser.add_argument("--detector", choices=["auto", "imgutils", "opencv"], default="auto")
    parser.add_argument("--preset", default="faster",
                        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"])
    args = parser.parse_args(argv)
    try:
        media.require_tools()
    except ClipperError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    server = serve(Path(args.data).expanduser(), args.host, args.port, args.detector, args.preset)
    print(f"clipper website on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
