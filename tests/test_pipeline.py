"""End to end on the synthetic box video: cut, reframe, encode, and check the real output."""
import json
import subprocess

import numpy as np
import pytest

from clipper import media
from clipper.cli import main, run
from clipper.times import ClipperError, END

from conftest import CUT_S, FPS, box_detector

TIMES = [3.5, 7, END]                  # the hard cut at 5.0 s falls inside part 2
EXPECTED = [(0, 84), (84, 168), (168, 240)]
SW, SH = 270, 480                      # read the 1080x1920 output back at quarter size


def frames_of(path):
    return np.stack(list(media.read_frames(path, 0, 10_000, SW, SH)))


def box_centre_x(frame):
    xs = np.nonzero(frame.min(axis=2) > 200)[1]
    return xs.mean() if len(xs) > 50 else None


def stream(path, kind):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", kind, "-count_frames",
                          "-show_entries", "stream=nb_read_frames,duration,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


@pytest.fixture(scope="module")
def made(box_video, tmp_path_factory):
    out = tmp_path_factory.mktemp("clips")
    manifest = run(str(box_video), TIMES, out_root=out, detector=box_detector)
    return out / "Box_Story_Ep1", manifest


def test_files_and_manifest(made):
    folder, manifest = made
    saved = json.loads((folder / "manifest.json").read_text())
    assert saved == manifest
    assert [p["file"] for p in saved["parts"]] == [f"Box_Story_Ep1_part0{n}.mp4" for n in (1, 2, 3)]
    assert [(p["part"], p["start"], p["end"]) for p in saved["parts"]] == [
        (1, "0:00.000", "0:03.500"), (2, "0:03.500", "0:07.000"), (3, "0:07.000", "0:10.000")]
    for p in saved["parts"]:
        assert (folder / p["file"]).is_file()


def test_durations_match_the_times_exactly(made):
    folder, manifest = made
    for (a, b), part in zip(EXPECTED, manifest["parts"]):
        video = stream(folder / part["file"], "v:0")
        audio = stream(folder / part["file"], "a:0")
        assert (video["width"], video["height"]) == (1080, 1920)
        assert int(video["nb_read_frames"]) == b - a                      # exact frame count
        assert float(video["duration"]) == pytest.approx((b - a) / FPS, abs=1e-3)
        assert float(audio["duration"]) == pytest.approx((b - a) / FPS, abs=0.03)  # audio kept, same length
    # back to back: each part starts where the last ended
    parts = manifest["parts"]
    assert all(x["end_seconds"] == y["start_seconds"] for x, y in zip(parts, parts[1:]))
    assert parts[0]["start_seconds"] == 0 and parts[-1]["end_seconds"] == 10


def test_the_box_stays_near_the_centre(made):
    folder, manifest = made
    offsets = []
    for part in manifest["parts"]:
        for frame in frames_of(folder / part["file"]):
            x = box_centre_x(frame)
            assert x is not None, "the box left the frame"
            offsets.append(abs(x - SW / 2) / SW)
    offsets = np.array(offsets)
    print(f"box offset from centre: mean {offsets.mean():.1%}, max {offsets.max():.1%} of the width")
    assert offsets.mean() < 0.03
    assert offsets.max() < 0.10


def test_the_crop_snaps_on_the_cut(made):
    folder, manifest = made
    part = manifest["parts"][1]
    assert part["shots"] == 2
    frames = frames_of(folder / part["file"])
    cut = round((CUT_S - 3.5) * FPS)                                 # frame 36 of part 2
    before, after = frames[cut - 1], frames[cut]
    assert before[..., 2].mean() > before[..., 0].mean()             # still the blue shot
    assert after[..., 0].mean() > after[..., 2].mean()               # already the red shot
    # the box jumped 210 px in the source (more than the crop is wide), yet it's centred on both sides
    for frame in (before, after, frames[cut + 1]):
        assert abs(box_centre_x(frame) - SW / 2) < 0.06 * SW


def test_finished_parts_are_skipped_unless_forced(made, box_video, capsys):
    folder, _ = made
    stamps = {p: p.stat().st_mtime_ns for p in folder.glob("*.mp4")}
    run(str(box_video), TIMES, out_root=folder.parent, detector=box_detector)
    assert "already done, skipping" in capsys.readouterr().out
    assert {p: p.stat().st_mtime_ns for p in folder.glob("*.mp4")} == stamps
    run(str(box_video), [3.5, END], out_root=folder.parent, detector=box_detector)  # part 2 changed
    out = capsys.readouterr().out
    assert "part01.mp4  0:00.000 -> 0:03.500  already done" in out
    assert "part02.mp4  0:03.500 -> 0:10.000  done" in out
    run(str(box_video), TIMES, out_root=folder.parent, detector=box_detector, force=True)
    assert all(p.stat().st_mtime_ns != stamps[p] for p in stamps)


def test_vertical_source_is_fitted(vertical_video, tmp_path):
    manifest = run(str(vertical_video), [END], out_root=tmp_path, detector=box_detector)
    v = stream(tmp_path / "tall" / manifest["parts"][0]["file"], "v:0")
    assert (v["width"], v["height"], int(v["nb_read_frames"])) == (1080, 1920, 48)


def test_plain_english_errors(box_video, tmp_path, capsys, monkeypatch):
    assert main([str(box_video), "-t", "4:12, 99:00", "-o", str(tmp_path)]) == 1
    assert "Cut 1 (4:12.000) is past the end of the video (0:10.000)" in capsys.readouterr().err
    assert main([str(box_video), "-t", "0:0x", "-o", str(tmp_path)]) == 1
    assert '"0:0x" is not a time' in capsys.readouterr().err
    assert main([str(tmp_path / "nope.mp4"), "-t", "5"]) == 1
    assert "Can't find the video file" in capsys.readouterr().err
    monkeypatch.setattr(media.shutil, "which", lambda tool: None)
    assert main([str(box_video), "-t", "5", "-o", str(tmp_path)]) == 1
    assert "ffmpeg is not installed" in capsys.readouterr().err


@pytest.mark.parametrize("shape, size", [("1:1", (1080, 1080)), ("4:5", (1080, 1350))])
def test_square_and_portrait_follow_the_box_too(box_video, tmp_path, shape, size):
    manifest = run(str(box_video), [3.5], out_root=tmp_path, detector=box_detector, shape=shape)
    clip = tmp_path / "Box_Story_Ep1" / manifest["parts"][0]["file"]
    v = stream(clip, "v:0")
    assert (v["width"], v["height"], int(v["nb_read_frames"])) == (*size, 84)
    w, h = size[0] // 4, size[1] // 4
    offsets = []
    for frame in media.read_frames(clip, 0, 10_000, w, h):
        xs = np.nonzero(frame.min(axis=2) > 200)[1]
        assert len(xs) > 20, "the box left the frame"
        offsets.append(abs(xs.mean() - w / 2) / w)
    # the "face" stays near the middle; a wide crop can't follow past the edge of the source
    assert np.mean(offsets) < 0.04 and max(offsets) < 0.15


def test_an_unknown_shape_is_explained(box_video, tmp_path):
    with pytest.raises(ClipperError, match='Unknown shape "2:1"'):
        run(str(box_video), [END], out_root=tmp_path, detector=box_detector, shape="2:1")
