import pytest

from clipper.times import END, ClipperError, format_time, parse_list, parse_time, plan_segments, read_cuts_file


@pytest.mark.parametrize("text,seconds", [
    ("90", 90), ("4:12", 252), ("1:02:30", 3750), ("0:05.5", 5.5), (" 12.25 ", 12.25), ("75:00", 4500),
])
def test_times_people_type(text, seconds):
    assert parse_time(text) == pytest.approx(seconds)


def test_end_is_a_time():
    assert parse_time("END") == END


@pytest.mark.parametrize("bad", ["4:7x", "4:60", "1:75:00", "", "abc", "-5", "4::12", "1:2:3:4"])
def test_bad_times_are_explained(bad):
    with pytest.raises(ClipperError, match="time"):
        parse_time(bad)


def test_format_round_trips():
    for s in (0, 5.5, 252, 3750.25):
        assert parse_time(format_time(s)) == pytest.approx(s)


def test_list_from_flag():
    assert parse_list("4:12, 8:30 end") == [252, 510, END]


def test_cuts_file_ignores_notes_comments_and_blanks(tmp_path):
    f = tmp_path / "cuts.txt"
    f.write_text("# episode 1\n4:12 hero meets the villain\n\n8:30, fight starts\nend  credits\n")
    assert read_cuts_file(f) == [252, 510, END]


def test_cuts_file_error_names_the_line(tmp_path):
    f = tmp_path / "cuts.txt"
    f.write_text("4:12\n8:3o oops\n")
    with pytest.raises(ClipperError, match="line 2"):
        read_cuts_file(f)


def test_segments_are_back_to_back_on_the_frame_grid():
    segs = plan_segments([3.5, 7.01, END], 0, 10, 24, 240)
    assert segs == [(0, 84), (84, 168), (168, 240)]


def test_start_skips_the_intro():
    assert plan_segments([5, END], 1.5, 10, 24, 240) == [(36, 120), (120, 240)]


def test_time_past_the_end_is_refused():
    with pytest.raises(ClipperError, match=r"Cut 2 \(0:12.000\) is past the end of the video \(0:10.000\)"):
        plan_segments([5, 12], 0, 10, 24, 240)


def test_times_must_go_up():
    with pytest.raises(ClipperError, match="must come after"):
        plan_segments([5, 4], 0, 10, 24, 240)
    with pytest.raises(ClipperError, match="must come after"):
        plan_segments([1], 2, 10, 24, 240)


def test_end_only_last():
    with pytest.raises(ClipperError, match="only be the last"):
        plan_segments([END, 5], 0, 10, 24, 240)
