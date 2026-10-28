"""The website end to end: sign in, save a video once, make clips, download them."""
import io
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile

import pytest

from clipper import media, web

from conftest import box_detector

PASSWORD = "open sesame"


class Browser:
    def __init__(self, base):
        self.base, self.cookie = base, ""

    def call(self, path, body=None, form=False, headers=None):
        data = None
        h = {"Cookie": self.cookie, **(headers or {})}
        if body is not None:
            if form:
                data = "&".join(f"{k}={urllib.request.quote(v)}" for k, v in body.items()).encode()
                h["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=h)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        try:
            r = urllib.request.build_opener(NoRedirect).open(request)
        except urllib.error.HTTPError as e:
            r = e
        cookie = r.headers.get("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";")[0]
        return r.status, dict(r.headers), r.read()

    def json(self, path, body=None):
        status, _, raw = self.call(path, body)
        return status, json.loads(raw)

    def wait(self, kind, key, until=("ready", "done", "failed"), timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            item = self.json("/api/state")[1][kind][key]
            if item["status"] in until:
                return item
            time.sleep(0.3)
        raise AssertionError(f"{kind} {key} stuck at {item}")


@pytest.fixture()
def site(tmp_path, box_video, monkeypatch):
    downloads = []

    def fake_download(url, folder, max_height, say):    # stands in for yt-dlp: "downloads" the box video
        downloads.append((url, max_height))
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "abc123.mp4"
        shutil.copy(box_video, target)
        say("  downloading video 100%")
        return target, "Box Story Ep1"

    monkeypatch.setattr(media, "download", fake_download)
    server = web.serve(tmp_path / "data", "127.0.0.1", 0, PASSWORD, detector=box_detector, preset="ultrafast")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = Browser(f"http://127.0.0.1:{server.server_address[1]}")
    yield browser, downloads, tmp_path / "data"
    server.shutdown()
    media.listeners.clear()


def sign_in(browser):
    status, headers, _ = browser.call("/login", {"password": PASSWORD}, form=True)
    assert status == 303 and "HttpOnly" in headers["Set-Cookie"]


def test_nothing_without_the_password(site):
    browser, _, _ = site
    assert browser.call("/api/state")[0] == 401
    assert browser.call("/media/video/x")[0] == 401
    assert b"Password" in browser.call("/")[2]
    status, _, page = browser.call("/login", {"password": "guess"}, form=True)
    assert status == 401 and b"Wrong password" in page
    assert browser.json("/api/add", {"url": "https://x.y/z"})[0] == 401
    sign_in(browser)
    assert browser.call("/api/state")[0] == 200
    assert b"Save video" in browser.call("/")[2]


def test_mistakes_are_explained_before_anything_runs(site):
    browser, downloads, _ = site
    sign_in(browser)
    assert browser.json("/api/add", {"url": "not a link"}) == (400, {"error": "Paste a video link (it starts with https://)."})
    status, body = browser.json("/api/add", {"url": "https://x.y/v", "times": "4:12\n9:3o"})
    assert status == 400 and body["error"].startswith('line 2: "9:3o" is not a time')
    assert downloads == []


def test_save_once_clip_twice_and_download(site):
    browser, downloads, data = site
    sign_in(browser)
    # save only: no times
    status, added = browser.json("/api/add", {"url": "https://example.com/watch?v=1", "quality": "best"})
    assert status == 200 and added["job"] is None
    video = browser.wait("videos", added["video"])
    assert video["status"] == "ready" and video["title"] == "Box Story Ep1" and video["preview"]
    assert round(video["duration"]) == 10 and downloads == [("https://example.com/watch?v=1", 2160)]

    # the small preview streams in pieces (seeking in the phone's player)
    status, headers, chunk = browser.call(f"/media/preview/{added['video']}.mp4", headers={"Range": "bytes=0-99"})
    assert status == 206 and len(chunk) == 100 and headers["Content-Range"].startswith("bytes 0-99/")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=height", "-of", "csv=p=0",
                            str(data / "previews" / f"{added['video']}.mp4")], capture_output=True, text=True)
    assert "360" in probe.stdout

    # clips from the saved video, and the same link again: no second download
    _, job1 = browser.json("/api/add", {"video": added["video"], "times": "3.5\nend"})
    _, job2 = browser.json("/api/add", {"url": "https://example.com/watch?v=1", "times": "5, end",
                                        "shape": "original"})
    assert job2["video"] == added["video"] and len(downloads) == 1
    one = browser.wait("jobs", job1["job"])
    two = browser.wait("jobs", job2["job"])
    assert one["status"] == two["status"] == "done", (one, two)
    assert [c["file"] for c in one["clips"]] == ["Box_Story_Ep1_part01.mp4", "Box_Story_Ep1_part02.mp4"]
    assert (one["width"], one["height"]) == (1080, 1920) and (two["width"], two["height"]) == (640, 360)

    status, headers, clip = browser.call(f"/media/clip/{job1['job']}/Box_Story_Ep1_part01.mp4")
    assert status == 200 and headers["Content-Disposition"].startswith("attachment")
    assert len(clip) == one["clips"][0]["size"] and clip[4:8] == b"ftyp"

    status, _, raw = browser.call(f"/media/zip/{job1['job']}")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.namelist() == ["Box_Story_Ep1_part01.mp4", "Box_Story_Ep1_part02.mp4"]
        assert len(z.read("Box_Story_Ep1_part02.mp4")) == one["clips"][1]["size"]

    # the full saved video can be downloaded too
    status, headers, full = browser.call(f"/media/video/{added['video']}")
    assert status == 200 and len(full) == video["size"]

    # times past the end are refused straight away once the length is known
    status, body = browser.json("/api/add", {"video": added["video"], "times": "0:20"})
    assert status == 400 and "past the end of the video (0:10.000)" in body["error"]


def test_files_outside_the_site_cannot_be_reached(site):
    browser, _, _ = site
    sign_in(browser)
    for path in ("/media/clip/../../secret", "/media/video/..%2Fsecret", "/media/preview/..%2F..%2Fstate.json",
                 "/media/zip/nope"):
        assert browser.call(path)[0] == 404


def test_deleting_and_carrying_on_after_a_restart(site, tmp_path, box_video):
    browser, downloads, data = site
    sign_in(browser)
    _, added = browser.json("/api/add", {"url": "https://example.com/v2", "times": "end"})
    job = browser.wait("jobs", added["job"])
    assert job["status"] == "done"
    assert browser.json(f"/api/jobs/{added['job']}/delete", {})[0] == 200
    assert not (data / "clips" / added["job"]).exists()
    assert browser.json(f"/api/videos/{added['video']}/delete", {})[0] == 200
    assert not list((data / "library").glob("*.mp4")) and not list((data / "previews").glob("*.mp4"))
    state = browser.json("/api/state")[1]
    assert state["videos"] == {} and state["jobs"] == {}

    # a job that was waiting when the server stopped is picked up again
    saved = json.loads((data / "state.json").read_text())
    saved["videos"]["v1"] = {"url": "https://example.com/v3", "title": "x", "quality": "1080",
                             "status": "downloading", "progress": "", "message": "", "added": 1}
    saved["jobs"]["j1"] = {"video": "v1", "ends": ["end"], "start": 0, "times": "end", "shape": "vertical",
                           "status": "working", "progress": "", "message": "", "clips": [], "created": 1}
    (data / "state.json").write_text(json.dumps(saved))
    server = web.serve(data, "127.0.0.1", 0, PASSWORD, detector=box_detector, preset="ultrafast")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    again = Browser(f"http://127.0.0.1:{server.server_address[1]}")
    again.cookie = browser.cookie                  # still signed in after a restart
    assert again.wait("jobs", "j1")["status"] == "done"
    server.shutdown()
