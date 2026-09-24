"""Tests for vision/proximity.py (models.md §6) using synthetic box sequences, no camera."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

import proximity as P
from proximity import Detection, ProximityTracker, Zone

FOCAL = 600.0  # px; a 1.7 m person at 3 m is then 340 px tall
FPS = 15.0  # processed frames per second in the synthetic sequences


def box_for(distance_m: float, cls: str = "person", focal: float = FOCAL) -> tuple:
    h = P.REAL_HEIGHT_M[cls] * focal / distance_m
    return (100.0, 50.0, 200.0, 50.0 + h)


def det(distance_m: float, track_id: int = 1, cls: str = "person") -> Detection:
    return Detection(track_id, cls, 0.83, box_for(distance_m, cls))


def run(distances: list[float], widened: bool = False, t0: float = 0.0, tracker=None):
    """Feed one track through the tracker at FPS; returns (tracker, readings)."""
    tracker = tracker or ProximityTracker()
    readings = [
        tracker.update([det(d)], t0 + i / FPS, FOCAL, widened)[0] for i, d in enumerate(distances)
    ]
    return tracker, readings


# --- distance -------------------------------------------------------------------------------------
@pytest.mark.parametrize("d", [1.0, 3.0, 7.5, 20.0])
def test_distance_inverts_box_height(d):
    assert P.estimate_distance_m(det(d).height_px, "person", FOCAL) == pytest.approx(d)


def test_distance_uses_class_height():
    assert P.estimate_distance_m(det(10.0, cls="truck").height_px, "truck", FOCAL) == pytest.approx(
        10.0
    )


def test_distance_fuses_ultrasonic_as_min():
    h = det(6.0).height_px
    assert P.estimate_distance_m(h, "person", FOCAL, ultrasonic_m=2.5) == pytest.approx(2.5)
    assert P.estimate_distance_m(h, "person", FOCAL, ultrasonic_m=9.0) == pytest.approx(6.0)


def test_zero_height_box_is_far_not_a_crash():
    assert P.estimate_distance_m(0.0, "person", FOCAL) == float("inf")


def test_calibration_round_trip(tmp_path):
    path = tmp_path / "calibration.json"
    P.save_calibration(
        box_height_px=340.0, distance_m=3.0, frame_height_px=480, samples=30, path=path
    )
    cal = P.Calibration.load(path)
    assert cal.calibrated and cal.focal_px == pytest.approx(600.0)
    assert cal.focal_for(960) == pytest.approx(1200.0)  # scales with resolution
    assert json.loads(path.read_text(encoding="utf-8"))["distance_m"] == 3.0


def test_missing_calibration_falls_back(tmp_path):
    cal = P.Calibration.load(tmp_path / "nope.json")
    assert not cal.calibrated and cal.focal_px > 0


# --- zones ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("d", "widened", "zone"),
    [
        (2.9, False, Zone.red),
        (3.0, False, Zone.orange),
        (7.0, False, Zone.orange),
        (7.1, False, Zone.clear),
        (4.9, True, Zone.red),
        (5.0, True, Zone.orange),
        (9.0, True, Zone.orange),
        (9.1, True, Zone.clear),
    ],
)
def test_zone_limits(d, widened, zone):
    assert P.classify_zone(d, widened) is zone


def test_zone_hysteresis_on_exit_only():
    assert P.classify_zone(3.2, previous=Zone.red) is Zone.red
    assert P.classify_zone(3.31, previous=Zone.red) is Zone.orange
    assert P.classify_zone(7.2, previous=Zone.orange) is Zone.orange
    assert P.classify_zone(7.31, previous=Zone.orange) is Zone.clear
    assert P.classify_zone(2.99, previous=Zone.orange) is Zone.red  # entry is exact
    assert P.classify_zone(5.2, widened=True, previous=Zone.red) is Zone.red


def test_standing_on_the_limit_does_not_flicker():
    seq = [2.9, 3.1, 2.95, 3.05, 3.1, 2.9] * 7  # jitter around 3 m for 2.8 s
    _, r = run(seq)
    assert [z for _, z, _ in posts(r)] == [Zone.red, Zone.red]  # entry + one 2 s repeat


def test_severity_mapping_and_approach_bump():
    assert P.zone_severity(Zone.orange, False) == "warning"
    assert P.zone_severity(Zone.red, False) == "critical"
    assert P.zone_severity(Zone.orange, True) == "critical"
    assert P.zone_severity(Zone.red, True) == "critical"  # never emergency: that pages the manager
    assert P.zone_severity(Zone.clear, True) is None


@pytest.mark.parametrize(
    ("sector", "etype"),
    [
        ("rear", "blindspot_intrusion"),
        ("left", "blindspot_intrusion"),
        ("right", "blindspot_intrusion"),
        ("front", "proximity_breach"),
        ("cab", "proximity_breach"),
    ],
)
def test_event_type_by_sector(sector, etype):
    assert P.event_type_for(sector) == etype


# --- approaching ----------------------------------------------------------------------------------
def linear(start: float, speed_mps: float, seconds: float) -> list[float]:
    n = int(seconds * FPS) + 1
    return [start - speed_mps * i / FPS for i in range(n)]


def test_approaching_when_closing_faster_than_half_metre_per_second():
    _, r = run(linear(10.0, 1.0, 1.5))
    assert r[-1].approaching
    assert r[-1].closing_speed_mps == pytest.approx(1.0, rel=0.05)


def test_not_approaching_when_slow():
    _, r = run(linear(10.0, 0.3, 2.0))
    assert not any(x.approaching for x in r)


def test_not_approaching_when_receding_or_still():
    _, r = run(linear(4.0, -1.0, 2.0))
    assert not any(x.approaching for x in r)
    _, r = run([5.0] * 30)
    assert not any(x.approaching for x in r)


def test_no_approach_verdict_before_enough_history():
    _, r = run(linear(10.0, 3.0, 0.5))
    assert all(x.closing_speed_mps is None and not x.approaching for x in r)


def test_approach_uses_last_second_only():
    # fast approach, then 2 s standing still: the old approach must not count any more
    seq = linear(10.0, 2.0, 1.0) + [8.0] * int(2 * FPS)
    _, r = run(seq)
    assert any(x.approaching for x in r[: int(FPS) + 1])
    assert not r[-1].approaching


def test_tracks_are_independent():
    tr = ProximityTracker()
    for i in range(int(1.5 * FPS)):
        t = i / FPS
        out = tr.update([det(10.0 - 1.0 * t, 1), det(6.0, 2)], t, FOCAL)
    assert out[0].approaching and not out[1].approaching


# --- debounce -------------------------------------------------------------------------------------
def posts(readings):
    return [(i, x.zone, x.severity) for i, x in enumerate(readings) if x.post]


def test_post_only_on_zone_change_while_orange():
    _, r = run([5.0] * 60)  # 4 s standing in orange
    assert posts(r) == [(0, Zone.orange, "warning")]


def test_red_reposts_every_two_seconds():
    _, r = run([2.0] * int(5 * FPS))  # 5 s in red
    idx = [i for i, *_ in posts(r)]
    assert idx == [0, 30, 60]  # t = 0, 2, 4 s


def test_zone_changes_each_post_and_clear_is_silent():
    seq = [5.0] * 3 + [2.0] * 3 + [5.0] * 3 + [12.0] * 3 + [5.0] * 3
    _, r = run(seq)
    assert [(z, s) for _, z, s in posts(r)] == [
        (Zone.orange, "warning"),
        (Zone.red, "critical"),
        (Zone.orange, "warning"),
        (Zone.orange, "warning"),  # re-entry after going clear
    ]
    assert [i for i, *_ in posts(r)] == [0, 3, 6, 12]


def test_severity_rise_inside_zone_posts_once():
    # standing in orange, then walking in at 1 m/s but still orange: approaching raises severity
    seq = [6.9] * 20 + linear(6.9, 1.0, 1.5)
    _, r = run(seq)
    p = posts(r)
    assert p[0] == (0, Zone.orange, "warning")
    assert p[1][1:] == (Zone.orange, "critical")
    assert len([x for x in p if x[1] is Zone.orange]) == 2


def test_widened_zone_turns_orange_into_red():
    _, r = run([4.0] * 3, widened=True)
    assert posts(r) == [(0, Zone.red, "critical")]


def test_lost_track_is_forgotten():
    tr, _ = run([2.0] * 3)
    tr.update([], 10.0, FOCAL)
    assert tr.tracks == {}
    _, r = run([2.0], t0=10.1, tracker=tr)
    assert r[0].post  # new episode posts immediately


# --- event payload --------------------------------------------------------------------------------
def test_event_matches_api_contract():
    _, r = run(linear(4.0, 1.5, 1.2))
    ev = P.build_event(
        r[-1], "M04", "OP03", "rear", now=datetime(2026, 9, 23, 10, 15, 3, tzinfo=UTC)
    )
    assert set(ev) == {
        "type",
        "machine_id",
        "operator_id",
        "ts",
        "severity",
        "distance_m",
        "sector",
        "approaching",
        "details",
    }
    assert ev["type"] == "blindspot_intrusion"
    assert ev["ts"] == "2026-09-23T10:15:03Z"
    assert ev["severity"] == "critical" and ev["approaching"] is True
    assert ev["distance_m"] == pytest.approx(2.2, abs=0.1)
    assert ev["details"] == {"track_id": 1, "class": "person", "conf": 0.83}
