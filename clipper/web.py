"""The clipper website: save videos on the server once, make clips from them, download the clips.

Videos are downloaded by the server and kept in its library, so the phone's data is only spent on
the finished clips (and, if wanted, a small 360p preview for finding the cut times).
One worker thread does the heavy work in order: downloads, previews and clips.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import media
from .cli import run
from .faces import make_detector
from .times import END, ClipperError, format_time, parse_time, parse_times_text
from .web_page import LOGIN_PAGE, PAGE

QUALITIES = {"1080": 1080, "best": 2160}
COOKIE = "clipper"


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
            self.state[kind][key].update(fields)
            self.save()

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))


class Worker:
    def __init__(self, store: Store, detector="auto", preset: str = "faster"):
        self.store = store
        self.tasks: queue.Queue = queue.Queue()
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
        for jid, j in sorted(store.state["jobs"].items(), key=lambda kv: kv[1]["created"]):
            if j["status"] in ("waiting", "working"):
                store.update("jobs", jid, status="waiting", progress="")
                self.tasks.put(("job", jid))
        threading.Thread(target=self.loop, daemon=True).start()

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
        store.update("videos", vid, status="downloading", message="")
        path, title = media.download(v["url"], store.library, QUALITIES.get(v["quality"], 1080), media.say)
        info = media.probe(path)
        store.update("videos", vid, status="preview", title=title, file=path.name, duration=info.duration,
                     width=info.width, height=info.height, size=path.stat().st_size,
                     progress="Making a small preview")
        preview = store.previews / f"{vid}.mp4"
        if not preview.exists():
            part = preview.with_name(f"{vid}.partial.mp4")
            done = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "scale=-2:360", "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "32", "-c:a", "aac", "-b:a", "48k", "-ac", "1",
                 "-movflags", "+faststart", str(part)], capture_output=True)
            if done.returncode == 0:
                part.replace(preview)
            else:
                part.unlink(missing_ok=True)
        store.update("videos", vid, status="ready", progress="", preview=preview.exists())

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
        if self.detector is None and j["shape"] == "vertical":
            self.detector = make_detector(self.detector_name, media.say)
        manifest = run(str(store.library / v["file"]), j["ends"], j["start"], out_root=store.clips / jid,
                       detector=self.detector or self.detector_name, preset=self.preset, crf=18,
                       reframe=j["shape"] == "vertical", title=v["title"])
        folder = store.clips / jid / media.safe_name(manifest["title"])
        clips = [{"file": p["file"], "start": p["start"], "end": p["end"], "duration": p["duration"],
                  "size": (folder / p["file"]).stat().st_size} for p in manifest["parts"]]
        store.update("jobs", jid, status="done", progress="", clips=clips, folder=folder.name,
                     width=manifest["width"], height=manifest["height"])


# ---------------------------------------------------------------- the website

def make_handler(store: Store, worker: Worker, password: str, secret: bytes):
    token = hmac.new(secret, password.encode(), hashlib.sha256).hexdigest()
    failures: dict[str, list[float]] = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "clipper"

        def log_message(self, fmt, *args):  # quiet: only errors go to the journal
            pass

        # -------- helpers
        def signed_in(self) -> bool:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return COOKIE in cookie and hmac.compare_digest(cookie[COOKIE].value, token)

        def send(self, code: int, body: bytes | str, kind: str = "text/html; charset=utf-8", headers=None):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, data, code: int = 200):
            self.send(code, json.dumps(data, ensure_ascii=False), "application/json")

        def error(self, message: str, code: int = 400):
            self.json({"error": message}, code)

        def body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                raise ClipperError("Too much text.")
            raw = self.rfile.read(length).decode("utf-8", "replace")
            if self.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(raw or "{}")
            return {k: v[0] for k, v in parse_qs(raw).items()}

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

        # -------- routes
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                return self.send(200, "ok", "text/plain")
            if not self.signed_in():
                if url.path.startswith(("/api/", "/media/")):
                    return self.error("Please sign in again.", 401)
                return self.send(200, LOGIN_PAGE)
            if url.path == "/":
                return self.send(200, PAGE)
            if url.path == "/api/state":
                state = store.snapshot()
                state["free_gb"] = round(shutil.disk_usage(store.data).free / 1e9, 1)
                state["queue"] = worker.tasks.qsize()
                return self.json(state)
            parts = url.path.strip("/").split("/")
            try:
                if parts[:2] == ["media", "preview"] and len(parts) == 3:
                    vid = parts[2].removesuffix(".mp4")
                    if vid in store.state["videos"]:
                        return self.send_file(store.previews / f"{vid}.mp4", f"{vid}.mp4", False)
                if parts[:2] == ["media", "video"] and len(parts) == 3:
                    v = store.state["videos"].get(parts[2])
                    if v and v.get("file"):
                        return self.send_file(store.library / v["file"],
                                              f"{media.safe_name(v['title'])}.mp4", True)
                if parts[:2] == ["media", "clip"] and len(parts) == 4:
                    j = store.state["jobs"].get(parts[2])
                    if j and any(c["file"] == parts[3] for c in j.get("clips", [])):
                        return self.send_file(store.clips / parts[2] / j["folder"] / parts[3], parts[3], True)
                if parts[:2] == ["media", "zip"] and len(parts) == 3:
                    j = store.state["jobs"].get(parts[2])
                    if j and j.get("clips"):
                        return self.send_zip(parts[2], j)
            except (BrokenPipeError, ConnectionResetError):
                return
            self.error("Not found.", 404)

        def send_zip(self, jid: str, j: dict):
            folder = store.clips / jid / j["folder"]
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(j['folder'])}.zip")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            class Out:          # zipfile writes a streamable zip to anything with write()
                def __init__(self, w):
                    self.w, self.n = w, 0

                def write(self, b):
                    self.w.write(b)
                    self.n += len(b)
                    return len(b)

                def tell(self):
                    return self.n

                def flush(self):
                    self.w.flush()

            with zipfile.ZipFile(Out(self.wfile), "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for c in j["clips"]:
                    z.write(folder / c["file"], c["file"])

        def do_POST(self):
            url = urlparse(self.path)
            try:
                data = self.body()
            except (ValueError, ClipperError):
                return self.error("That request didn't make sense.")
            if url.path == "/login":
                ip = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For") \
                    or self.client_address[0]
                recent = [t for t in failures.get(ip, []) if time.time() - t < 600]
                if len(recent) >= 10:
                    return self.send(429, LOGIN_PAGE.replace("<!--msg-->", "Too many tries. Wait 10 minutes."))
                if hmac.compare_digest(str(data.get("password", "")), password):
                    failures.pop(ip, None)
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", f"{COOKIE}={token}; Path=/; Max-Age=31536000; HttpOnly; "
                                                   "SameSite=Lax" + ("; Secure" if self.headers.get(
                                                       "X-Forwarded-Proto") == "https" else ""))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                failures[ip] = recent + [time.time()]
                time.sleep(1)
                return self.send(401, LOGIN_PAGE.replace("<!--msg-->", "Wrong password."))
            if not self.signed_in():
                return self.error("Please sign in again.", 401)
            try:
                if url.path == "/api/add":
                    return self.json(self.add(data))
                m = re.fullmatch(r"/api/(videos|jobs)/([\w-]+)/delete", url.path)
                if m:
                    return self.json(self.delete(m.group(1), m.group(2)))
                m = re.fullmatch(r"/api/jobs/([\w-]+)/retry", url.path)
                if m and m.group(1) in store.state["jobs"]:
                    store.update("jobs", m.group(1), status="waiting", message="", progress="")
                    worker.tasks.put(("job", m.group(1)))
                    return self.json({"ok": True})
                m = re.fullmatch(r"/api/videos/([\w-]+)/retry", url.path)
                if m and m.group(1) in store.state["videos"]:
                    store.update("videos", m.group(1), status="waiting", message="", progress="")
                    worker.tasks.put(("video", m.group(1)))
                    return self.json({"ok": True})
            except ClipperError as e:
                return self.error(str(e))
            self.error("Not found.", 404)

        def add(self, data: dict) -> dict:
            """A link and/or a saved video, plus optional end times. No times = just save the video."""
            text = str(data.get("times") or "").strip()
            ends = parse_times_text(text) if text else None
            start = 0.0
            if str(data.get("start") or "").strip():
                start = parse_time(str(data["start"]))
                if start == END:
                    raise ClipperError('The start can\'t be "end".')
            shape = "original" if data.get("shape") == "original" else "vertical"
            vid = str(data.get("video") or "")
            with store.lock:
                videos = store.state["videos"]
                if not vid:
                    link = re.search(r"https?://\S+", str(data.get("url") or ""))
                    if not link:
                        raise ClipperError("Paste a video link (it starts with https://).")
                    url = link.group(0)
                    quality = "best" if data.get("quality") == "best" else "1080"
                    vid = next((k for k, v in videos.items() if v["url"] == url and v["status"] != "failed"),
                               None)
                    if vid is None:
                        vid = uuid.uuid4().hex[:10]
                        videos[vid] = {"url": url, "title": url, "quality": quality, "status": "waiting",
                                       "progress": "", "message": "", "added": time.time()}
                        store.save()
                        worker.tasks.put(("video", vid))
                elif vid not in videos:
                    raise ClipperError("That video isn't in the library any more.")
                v = videos[vid]
                if ends and v.get("duration"):
                    last = [e for e in ends if e != END]
                    if last and max(last) > v["duration"] + 0.05:
                        raise ClipperError(f"{format_time(max(last))} is past the end of the video "
                                           f"({format_time(v['duration'])}). Use \"end\" for the last clip.")
                jid = None
                if ends:
                    jid = uuid.uuid4().hex[:10]
                    store.state["jobs"][jid] = {"video": vid, "ends": ends, "start": start, "times": text,
                                                "shape": shape, "status": "waiting", "progress": "",
                                                "message": "", "clips": [], "created": time.time()}
                    store.save()
                    worker.tasks.put(("job", jid))
            return {"video": vid, "job": jid}

        def delete(self, kind: str, key: str) -> dict:
            with store.lock:
                item = store.state[kind].get(key)
                if not item:
                    return {"ok": True}
                busy = worker.current == ("video" if kind == "videos" else "job", key)
                if busy or (kind == "videos" and any(
                        j["video"] == key and j["status"] in ("waiting", "working")
                        for j in store.state["jobs"].values())):
                    raise ClipperError("It's being worked on. Delete it when it's finished.")
                if kind == "videos":
                    if item.get("file"):
                        (store.library / item["file"]).unlink(missing_ok=True)
                        index_file = store.library / "index.json"
                        if index_file.exists():
                            index = json.loads(index_file.read_text())
                            index.pop(item["url"], None)
                            index_file.write_text(json.dumps(index, indent=2, ensure_ascii=False))
                    (store.previews / f"{key}.mp4").unlink(missing_ok=True)
                else:
                    shutil.rmtree(store.clips / key, ignore_errors=True)
                del store.state[kind][key]
                store.save()
            return {"ok": True}

    return Handler


def serve(data: Path, host: str, port: int, password: str, detector: str = "auto",
          preset: str = "faster") -> ThreadingHTTPServer:
    data.mkdir(parents=True, exist_ok=True)
    secret_file = data / "secret"
    if not secret_file.exists():
        secret_file.write_bytes(secrets.token_bytes(32))
        os.chmod(secret_file, 0o600)
    store = Store(data)
    worker = Worker(store, detector, preset)
    server = ThreadingHTTPServer((host, port), make_handler(store, worker, password, secret_file.read_bytes()))
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
    data = Path(args.data).expanduser()
    password = os.environ.get("CLIPPER_PASSWORD", "")
    if not password:
        file = data / "password"
        if not file.exists():
            data.mkdir(parents=True, exist_ok=True)
            file.write_text(secrets.token_urlsafe(9) + "\n")
            os.chmod(file, 0o600)
        password = file.read_text().strip()
    try:
        media.require_tools()
    except ClipperError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    server = serve(data, args.host, args.port, password, args.detector, args.preset)
    print(f"clipper website on http://{args.host}:{args.port}  (password in {data / 'password'} "
          f"or $CLIPPER_PASSWORD)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
