"""The website end to end: paste a link, save it once, clip it part by part, download the clips."""
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from clipper import media, web

from conftest import box_detector


class Browser:
    def __init__(self, base):
        self.base = base

    def call(self, path, body=None, headers=None, kind="application/json"):
        data = None if body is None else (json.dumps(body).encode() if kind == "application/json"
                                          else body.encode())
        h = dict(headers or {})
        if body is not None:
            h["Content-Type"] = kind
        try:
            r = urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h))
        except urllib.error.HTTPError as e:
            r = e
        return r.status, dict(r.headers), r.read()

    def json(self, path, body=None):
        status, _, raw = self.call(path, body)
        return status, json.loads(raw)

    def wait(self, kind, key, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            item = self.json("/api/state")[1][kind][key]
            if item["status"] in ("ready", "done", "failed"):
                return item
            time.sleep(0.3)
        raise AssertionError(f"{kind} {key} stuck at {item}")


@pytest.fixture()
def site(tmp_path, box_video, monkeypatch):
    downloads = []

    def fake_download(url, folder, max_height, say, max_bytes=None):   # stands in for yt-dlp
        downloads.append((url, max_height))
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "abc123.mp4"
        shutil.copy(box_video, target)
        say("  downloading video 100%")
        return target, "Box Story Ep1"

    monkeypatch.setattr(media, "download", fake_download)
    server = web.serve(tmp_path / "data", "127.0.0.1", 0, detector=box_detector, preset="ultrafast")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = Browser(f"http://127.0.0.1:{server.server_address[1]}")
    yield browser, downloads, tmp_path / "data"
    server.shutdown()
    media.listeners.clear()


def saved(browser, url="https://example.com/watch?v=1"):
    status, body = browser.json("/api/video", {"url": url})
    assert status == 200
    video = browser.wait("videos", body["video"])
    assert video["status"] == "ready", video
    return body["video"], video


def test_no_password_the_page_just_opens(site):
    browser, _, _ = site
    status, _, page = browser.call("/")
    assert status == 200 and b"Paste a video link" in page and b"password" not in page.lower()


def test_paste_a_link_clip_part_by_part_and_download(site):
    browser, downloads, data = site
    vid, video = saved(browser)
    assert video["title"] == "Box Story Ep1" and round(video["duration"]) == 10 and video["preview"]
    assert downloads == [("https://example.com/watch?v=1", web.MAX_HEIGHT)]

    # part 1: 0:00 to 0:04, part 2: from 4 to the end (end left empty)
    _, one = browser.json("/api/clip", {"video": vid, "start": "0:00", "end": "0:04"})
    _, two = browser.json("/api/clip", {"video": vid, "start": "4", "end": ""})
    one, two = browser.wait("jobs", one["job"]), browser.wait("jobs", two["job"])
    assert one["status"] == two["status"] == "done", (one, two)
    assert (one["file"], one["part"]) == ("Box_Story_Ep1_part01.mp4", 1)
    assert (two["file"], two["start"], round(two["end"])) == ("Box_Story_Ep1_part02.mp4", 4, 10)

    # every clip is 1080x1920 (9:16) and exactly as long as asked
    jid = next(k for k, j in browser.json("/api/state")[1]["jobs"].items() if j["part"] == 1)
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                          "stream=width,height,nb_read_frames", "-of", "json", str(data / "clips" / jid / one["file"])],
                         capture_output=True, text=True).stdout
    stream = json.loads(out)["streams"][0]
    assert (stream["width"], stream["height"], int(stream["nb_read_frames"])) == (1080, 1920, 96)

    # downloads have a size and resume (the phone's download manager carries on in the background)
    status, headers, whole = browser.call(f"/media/clip/{jid}")
    assert status == 200 and headers["Content-Disposition"].startswith("attachment")
    assert int(headers["Content-Length"]) == len(whole) == one["size"] and whole[4:8] == b"ftyp"
    status, headers, rest = browser.call(f"/media/clip/{jid}", headers={"Range": "bytes=1000-"})
    assert status == 206 and rest == whole[1000:]

    # the same link again opens the saved video: no second download
    assert browser.json("/api/video", {"url": "https://example.com/watch?v=1"})[1]["video"] == vid
    assert len(downloads) == 1

    # the small preview streams for finding times
    status, headers, chunk = browser.call(f"/media/preview/{vid}.mp4", headers={"Range": "bytes=0-99"})
    assert status == 206 and len(chunk) == 100


@pytest.mark.parametrize("start, end, message", [
    ("0:05", "0:03", "The end must be after the start."),
    ("0:00", "0:20", "The end is past the end of the video (0:10.000)"),
    ("0:30", "", "The start is past the end of the video"),
    ("0:0x", "", 'Start: "0:0x" is not a time'),
    ("", "9:3o", 'End: "9:3o" is not a time'),
])
def test_time_mistakes_are_explained(site, start, end, message):
    browser, _, _ = site
    vid, _ = saved(browser)
    status, body = browser.json("/api/clip", {"video": vid, "start": start, "end": end})
    assert status == 400 and body["error"].startswith(message)


def test_link_mistakes_are_explained(site):
    browser, downloads, _ = site
    assert browser.json("/api/video", {"url": "not a link"}) == (400, {"error": "Paste a video link (it starts with https://)."})
    assert browser.json("/api/clip", {"video": "nope", "start": "0"})[0] == 400
    assert downloads == []


@pytest.mark.parametrize("link", ["http://127.0.0.1:4880/", "http://169.254.169.254/latest", "http://localhost/x",
                                  "http://10.0.0.5/v.mp4", "http://[::1]/v"])
def test_links_into_the_server_itself_are_refused(site, link):
    browser, downloads, _ = site
    status, body = browser.json("/api/video", {"url": link})
    assert status == 400 and "public" in body["error"] and downloads == []


def test_a_nearly_full_disk_stops_downloads(site, monkeypatch):
    browser, downloads, _ = site
    monkeypatch.setattr(web, "MIN_FREE_BYTES", 10**18)
    _, body = browser.json("/api/video", {"url": "https://example.com/big"})
    video = browser.wait("videos", body["video"])
    assert video["status"] == "failed" and "nearly full" in video["message"] and downloads == []


def test_other_sites_cannot_send_forms(site):
    browser, _, _ = site
    assert browser.call("/api/video", "url=https://example.com/v", kind="application/x-www-form-urlencoded")[0] == 415


def test_files_outside_the_site_cannot_be_reached(site):
    browser, _, _ = site
    for path in ("/media/clip/../../state.json", "/media/preview/..%2F..%2Fstate.json", "/media/clip/nope"):
        assert browser.call(path)[0] == 404


def test_deleting_a_video_removes_its_clips_and_restarts_carry_on(site):
    browser, downloads, data = site
    vid, _ = saved(browser)
    _, job = browser.json("/api/clip", {"video": vid, "start": "", "end": ""})
    assert browser.wait("jobs", job["job"])["status"] == "done"
    assert browser.json(f"/api/jobs/{job['job']}/retry", {})[0] == 400     # only failed things retry
    assert browser.json(f"/api/videos/{vid}/delete", {})[0] == 200
    assert not list((data / "library").glob("*.mp4")) and not list((data / "clips").iterdir())
    assert browser.json("/api/state")[1]["videos"] == {} == browser.json("/api/state")[1]["jobs"]

    # work that was going on when the server stopped is picked up again
    state = json.loads((data / "state.json").read_text())
    state["videos"]["v1"] = {"url": "https://example.com/v3", "title": "x", "status": "downloading",
                             "progress": "", "message": "", "added": 1}
    state["jobs"]["j1"] = {"video": "v1", "start": 0, "end": 5, "part": 1, "status": "working",
                           "progress": "", "message": "", "created": 1}
    (data / "state.json").write_text(json.dumps(state))
    server = web.serve(data, "127.0.0.1", 0, detector=box_detector, preset="ultrafast")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    again = Browser(f"http://127.0.0.1:{server.server_address[1]}")
    assert again.wait("jobs", "j1")["status"] == "done"
    server.shutdown()
