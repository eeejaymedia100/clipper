import numpy as np

from clipper.faces import Box
from clipper.track import Settings, follow, plan_path

FPS, SRC_W, CROP_W = 24, 1920, 608


def face(cx, size=100, cy=300):
    return Box(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2)


def centres(path):
    return np.array(path) + CROP_W / 2


def test_small_wobbles_do_not_move_the_crop():
    rng = np.random.default_rng(0)
    samples = {f: [face(900 + rng.uniform(-12, 12))] for f in range(0, 240, 6)}
    path = plan_path([(0, 240)], samples, 240, FPS, SRC_W, CROP_W)
    assert max(path) - min(path) == 0


def test_no_face_in_a_shot_means_centred():
    path = plan_path([(0, 48)], {0: [], 6: []}, 48, FPS, SRC_W, CROP_W)
    assert set(path) == {(SRC_W - CROP_W) // 2}


def test_lost_face_holds_its_last_place():
    samples = {0: [face(400)], 6: [face(400)], 12: [], 18: [], 24: []}
    path = plan_path([(0, 30)], samples, 30, FPS, SRC_W, CROP_W)
    assert set(centres(path)) == {400}


def test_cut_snaps_on_its_frame():
    samples = {0: [face(400)], 6: [face(400)], 12: [face(1500)], 18: [face(1500)]}
    path = centres(plan_path([(0, 12), (12, 24)], samples, 24, FPS, SRC_W, CROP_W))
    assert list(path[:12]) == [400] * 12 and list(path[12:]) == [1500] * 12


def test_moves_with_no_lag():
    # face moving steadily right; zero-phase smoothing keeps the crop on it, not behind it
    samples = {f: [face(500 + 10 * f)] for f in range(0, 120, 6)}
    path = centres(plan_path([(0, 120)], samples, 120, FPS, SRC_W, CROP_W))
    truth = 500 + 10 * np.arange(120)
    middle = slice(24, 96)
    assert abs(np.mean(path[middle] - truth[middle])) < 0.03 * CROP_W
    assert np.max(np.abs(path[middle] - truth[middle])) < Settings().deadband * CROP_W + 2


def test_stays_on_the_locked_character():
    # the hero (big) is locked; a second face grows bigger but the crop stays on the hero
    samples = [(f, [face(500, 120), face(1400, 80 + 4 * f)]) for f in range(0, 72, 6)]
    assert {x for _, x in follow(samples, FPS, 1.5)} == {500}


def test_switches_when_the_character_is_gone_for_a_while():
    samples = [(0, [face(500, 120)])] + [(f, [face(1400, 90)]) for f in range(6, 96, 6)]
    xs = [x for _, x in follow(samples, FPS, 1.5)]
    assert xs[1] is None                 # brief miss: hold, don't jump
    assert xs[-1] == 1400                # gone for over 1.5 s: follow who is on screen
