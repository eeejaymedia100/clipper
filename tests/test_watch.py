"""The phone folder: drop a job file in Clipper/jobs, get the clips back in Clipper/<title>/."""
import os
import time

import pytest

from clipper.times import ClipperError, END
from clipper.watch import read_job, watch_once

from conftest import box_detector


def write_job(folder, name, text, age=10):
    jobs = folder / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job = jobs / name
    job.write_text(text)
    old = time.time() - age
    os.utime(job, (old, old))
    return job


def test_a_job_is_a_link_then_end_times(tmp_path):
    job = write_job(tmp_path, "ep3.txt", "https://youtu.be/abc\n# a note\nstart 0:45\n4:12 meets the villain\n"
                                         "\n9:30\nend\n")
    assert read_job(job) == ("https://youtu.be/abc", 45.0, [252.0, 570.0, END])


def test_times_can_go_on_one_line(tmp_path):
    job = write_job(tmp_path, "a.txt", "Watch this https://www.tiktok.com/@x/video/1 pls\n1:00, 2:30, end")
    assert read_job(job) == ("https://www.tiktok.com/@x/video/1", 0.0, [60.0, 150.0, END])


@pytest.mark.parametrize("text, message", [
    ("4:12\nend", "Put the video link on the first line"),
    ("https://youtu.be/abc", "Add the end time of each clip"),
    ("https://youtu.be/abc\n4:12\n9:3o", 'line 3: "9:3o" is not a time'),
    ("https://youtu.be/abc\nstart end\n4:12", 'line 2: "start" can\'t be "end"'),
])
def test_job_mistakes_are_explained(tmp_path, text, message):
    with pytest.raises(ClipperError, match=message):
        read_job(write_job(tmp_path, "bad.txt", text))


def test_a_job_becomes_clips_in_the_phone_folder(box_video, tmp_path):
    folder = tmp_path / "Clipper"
    job = write_job(folder, "box.txt", f"{box_video}\n3.5\nend\n")
    downloads = tmp_path / "downloads"
    assert watch_once(folder, downloads, detector=box_detector) == 1

    clips = folder / "Box_Story_Ep1"
    assert sorted(p.name for p in clips.glob("*.mp4")) == ["Box_Story_Ep1_part01.mp4", "Box_Story_Ep1_part02.mp4"]
    assert not list(folder.rglob("*.partial.mp4"))
    assert not job.exists()                                        # moved out of the way
    done = (folder / "jobs" / "done" / "box.txt").read_text()
    assert done.startswith(f"{box_video}\n3.5\nend\n") and "Done: 2 clips in Clipper/Box_Story_Ep1" in done
    assert not (folder / "jobs" / "box.status.txt").exists()
    assert "*.partial.mp4" in (folder / ".stignore").read_text()  # half-made clips never use the phone's data
    assert box_video.exists()                                      # a local file is never deleted

    assert watch_once(folder, downloads, detector=box_detector) == 0   # nothing left to do


def test_a_bad_job_says_why_and_waits_to_be_fixed(box_video, tmp_path):
    folder = tmp_path / "Clipper"
    job = write_job(folder, "box.txt", f"{box_video}\n9:00\n")
    seen: dict = {}
    assert watch_once(folder, tmp_path / "dl", detector=box_detector, failed=seen) == 1
    status = (folder / "jobs" / "box.status.txt").read_text()
    assert "Problem: Cut 1 (9:00.000) is past the end of the video (0:10.000)" in status
    assert "Fix box.txt and save it again" in status
    assert job.exists()
    assert watch_once(folder, tmp_path / "dl", detector=box_detector, failed=seen) == 0   # not retried as is

    write_job(folder, "box.txt", f"{box_video}\nend\n")                                # fixed on the phone
    assert watch_once(folder, tmp_path / "dl", detector=box_detector, failed=seen) == 1
    assert (folder / "Box_Story_Ep1" / "Box_Story_Ep1_part01.mp4").exists()
    assert not (folder / "jobs" / "box.status.txt").exists()


def test_a_job_still_arriving_is_left_alone(tmp_path):
    write_job(tmp_path, "new.txt", "https://youtu.be/abc\nend", age=0)
    assert watch_once(tmp_path, tmp_path / "dl", detector=box_detector) == 0
