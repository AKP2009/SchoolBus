"""Tests for vision/fatigue.py (models.md §5) using synthetic landmark sequences, no camera."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest

import fatigue as F
from fatigue import FatigueMonitor, Shift

W, H = 640, 480
FPS = 15.0
NOW = datetime(2026, 9, 24, 4, 30, tzinfo=UTC)  # 10:00 IST
DAY_SHIFT = Shift(NOW, "day", "SH-2026-09-24-M04-D")  # 0 h into a day shift → no time term


def face(ear: float = 0.30, mar: float = 0.05, pitch_deg: float = 0.0) -> np.ndarray:
    """478 Face Mesh landmarks in pixels.

    The solvePnP points are the generic face model rotated by `pitch_deg` (negative = head down)
    and projected; eye and mouth points give the wanted EAR / MAR.
    """
    lm = np.zeros((478, 3))
    theta = math.radians(-pitch_deg)  # head down = positive rotation about the camera x axis
    rot = np.array(
        [[1, 0, 0], [0, math.cos(theta), -math.sin(theta)], [0, math.sin(theta), math.cos(theta)]]
    )
    rvec, _ = cv2.Rodrigues(rot)
    proj, _ = cv2.projectPoints(
        F.PNP_MODEL_MM, rvec, np.array([0.0, 0.0, 1500.0]), F.camera_matrix(W, H), np.zeros(4)
    )
    for idx, (x, y) in zip(F.PNP_LANDMARKS, proj.reshape(-1, 2), strict=True):
        lm[idx, :2] = (x, y)

    def eye(indices: tuple[int, ...], outer: int, direction: float) -> None:
        p1, p2, p3, p4, p5, p6 = indices
        w = 30.0
        ox, oy = lm[outer, :2]
        ix = ox + direction * w
        x1, x4 = (ox, ix) if outer == p1 else (ix, ox)
        lm[p1, :2], lm[p4, :2] = (x1, oy), (x4, oy)
        v = ear * w  # EAR = (2v) / (2w)
        for top, bottom, frac in ((p2, p6, 1 / 3), (p3, p5, 2 / 3)):
            x = x1 + (x4 - x1) * frac
            lm[top, :2], lm[bottom, :2] = (x, oy - v / 2), (x, oy + v / 2)

    eye(F.RIGHT_EYE, 33, +1.0)  # outer corner 33, inner 133 towards the nose (image right)
    eye(F.LEFT_EYE, 263, -1.0)  # outer corner 263, inner 362 towards the nose (image left)

    cx, cy = (lm[61, :2] + lm[291, :2]) / 2
    w = 40.0
    lm[F.MOUTH_CORNERS[0], :2], lm[F.MOUTH_CORNERS[1], :2] = (cx - w / 2, cy), (cx + w / 2, cy)
    for i, (top, bottom) in enumerate(F.MOUTH_VERTICAL):
        x = cx + (i - 1) * 8
        lm[top, :2], lm[bottom, :2] = (x, cy - mar * w / 2), (x, cy + mar * w / 2)
    return lm


class Clock:
    """Feeds a monitor at FPS; `now` advances with t so shift hours stay consistent."""

    def __init__(self, monitor: FatigueMonitor, t: float = 0.0) -> None:
        self.m, self.t = monitor, t
        self.events: list[dict] = []

    def run(self, seconds: float, lm_fn=lambda t: face(), **kw) -> F.FatigueReading:
        r = None
        for _ in range(round(seconds * FPS)):
            now = NOW + timedelta(seconds=self.t)
            r = self.m.update(self.t, lm_fn(self.t), W, H, now=now, **kw)
            self.events += r.events
            self.t += 1 / FPS
        return r

    def of_type(self, event_type: str, severity: str | None = None) -> list[dict]:
        return [
            e
            for e in self.events
            if e["type"] == event_type and (severity is None or e.get("severity") == severity)
        ]


def monitor(shift: Shift = DAY_SHIFT) -> FatigueMonitor:
    return FatigueMonitor("M04", "OP03", shift)


def calibrated(shift: Shift = DAY_SHIFT) -> Clock:
    c = Clock(monitor(shift))
    c.run(F.CALIBRATION_S + 1)
    assert c.m.calibration.done and not c.m.calibration.fallback
    return c


# --- EAR / MAR / pitch ----------------------------------------------------------------------------
def test_eye_aspect_ratio_formula():
    # p1 (0,0), p4 (10,0), verticals 3 and 5 → (3 + 5) / (2·10) = 0.4
    p = np.array([(0, 0), (3, -1.5), (7, -2.5), (10, 0), (7, 2.5), (3, 1.5)], dtype=float)
    assert F.eye_aspect_ratio(p) == pytest.approx(0.4)


def test_degenerate_eye_is_zero_not_a_crash():
    assert F.eye_aspect_ratio(np.zeros((6, 2))) == 0.0


@pytest.mark.parametrize("value", [0.1, 0.22, 0.3])
def test_ear_and_mar_from_landmarks(value):
    lm = face(ear=value, mar=value * 2)
    assert F.ear(lm) == pytest.approx(value)
    assert F.mar(lm) == pytest.approx(value * 2)


@pytest.mark.parametrize("pitch", [-30.0, -20.0, 0.0, 15.0])
def test_head_pitch_from_solvepnp(pitch):
    assert F.head_pitch_deg(face(pitch_deg=pitch), W, H) == pytest.approx(pitch, abs=1.0)


# --- score and levels -----------------------------------------------------------------------------
def test_score_formula_terms():
    assert F.fatigue_score(0.0, 0, 0, 0.0, "day") == 0.0
    assert F.fatigue_score(0.15, 0, 0, 0.0, "day") == pytest.approx(0.225)
    assert F.fatigue_score(0.0, 3, 0, 0.0, "day") == pytest.approx(0.15)
    assert F.fatigue_score(0.0, 0, 3, 0.0, "day") == pytest.approx(0.15)
    assert F.fatigue_score(0.0, 0, 0, 5.0, "day") == pytest.approx(0.075)
    assert F.fatigue_score(0.0, 0, 0, 0.0, "night") == pytest.approx(0.10)


def test_score_terms_saturate_at_one():
    assert F.fatigue_score(0.9, 10, 10, 14.0, "night") == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0.0, "low"),
        (0.3499, "low"),
        (0.35, "medium"),
        (0.5999, "medium"),
        (0.6, "high"),
        (1, "high"),
    ],
)
def test_level_thresholds(score, level):
    assert F.fatigue_level(score) == level


# --- calibration ----------------------------------------------------------------------------------
def test_calibration_threshold_is_075_of_open_eye_mean_ignoring_blinks():
    cal = F.EarCalibration()
    for i in range(int(30 * FPS) + 1):
        blink = i % 60 < 3  # 3-frame blink every 4 s
        cal.update(i / FPS, 0.08 if blink else 0.32, 0.0)
    assert cal.done and not cal.fallback
    assert cal.threshold == pytest.approx(0.75 * 0.32)


def test_calibration_falls_back_without_a_face():
    cal = F.EarCalibration()
    for i in range(int(30 * FPS) + 1):
        cal.update(i / FPS, 0.3 if i < 20 else None, None)
    assert cal.done and cal.fallback and cal.threshold == F.EAR_CLOSED_DEFAULT


def test_calibration_rejects_implausible_threshold():
    cal = F.EarCalibration()
    for i in range(int(30 * FPS) + 1):
        cal.update(i / FPS, 0.9, None)  # 0.75 × 0.9 = 0.675, not a real eye
    assert cal.fallback and cal.threshold == F.EAR_CLOSED_DEFAULT


def test_calibration_sets_neutral_pitch():
    c = Clock(monitor())
    c.run(F.CALIBRATION_S + 1, lambda t: face(pitch_deg=-10))  # camera mounted above the face
    assert c.m.calibration.neutral_pitch == pytest.approx(-10, abs=1)
    r = c.run(1, lambda t: face(pitch_deg=-10))
    assert r.pitch_deg == pytest.approx(0, abs=1)


# --- PERCLOS --------------------------------------------------------------------------------------
def test_perclos_share_of_closed_frames():
    p = F.Perclos()
    for i in range(int(60 * FPS)):
        p.add(i / FPS, i % 10 == 0)
    assert p.value() == pytest.approx(0.1, abs=0.005)


def test_perclos_forgets_frames_older_than_60s():
    p = F.Perclos()
    for i in range(int(60 * FPS)):
        p.add(i / FPS, True)
    for i in range(int(60 * FPS), int(120 * FPS) + 1):
        p.add(i / FPS, False)
    assert p.value() == 0.0


def test_perclos_empty_is_none():
    assert F.Perclos().value() is None


def test_monitor_perclos_from_landmarks():
    c = calibrated()
    # 1 closed frame in 5 for 60 s → PERCLOS 0.2 (threshold 0.225, closed EAR 0.1)
    r = c.run(60, lambda t: face(ear=0.1 if round(t * FPS) % 5 == 0 else 0.3))
    assert r.perclos == pytest.approx(0.2, abs=0.01)


# --- yawn / head-down -----------------------------------------------------------------------------
def test_yawn_needs_more_than_1_5_s():
    c = calibrated()
    c.run(1.4, lambda t: face(mar=0.7))
    c.run(1, lambda t: face(mar=0.1))
    assert c.m.yawns.count(c.t) == 0
    c.run(1.7, lambda t: face(mar=0.7))
    r = c.run(3, lambda t: face(mar=0.7))  # still yawning: one yawn, not several
    assert r.yawns_10min == 1


def test_head_down_needs_more_than_2_s_below_minus_20():
    c = calibrated()
    c.run(2.5, lambda t: face(pitch_deg=-15))  # not far enough down
    c.run(1.9, lambda t: face(pitch_deg=-30))  # not long enough
    c.run(1, lambda t: face())
    assert c.m.head_downs.count(c.t) == 0
    r = c.run(2.2, lambda t: face(pitch_deg=-30))
    assert r.head_down_10min == 1


def test_counters_cover_10_minutes():
    counter = F.WindowCounter()
    counter.add(0.0)
    counter.add(300.0)
    assert counter.count(599.0) == 2
    assert counter.count(600.0) == 1  # the first one is now 10 min old


# --- debounce -------------------------------------------------------------------------------------
NIGHT_LATE = Shift(NOW - timedelta(hours=10), "night", "SH-2026-09-23-M04-N")  # 0.25 from shift


def test_fatigue_high_posts_once_on_entering_high_and_again_after_leaving():
    c = calibrated(NIGHT_LATE)

    def drowsy(t: float) -> np.ndarray:  # every 3rd frame closed: PERCLOS ≈ 0.33
        return face(ear=0.1 if round(t * FPS) % 3 == 0 else 0.3)

    c.run(90, drowsy)
    assert c.m.last.level == "high"
    assert len(c.of_type("fatigue_high", "warning")) == 1
    c.run(90, lambda t: face())  # PERCLOS → 0, score 0.25
    assert c.m.last.level == "low"
    c.run(90, drowsy)
    highs = c.of_type("fatigue_high", "warning")
    assert len(highs) == 2
    assert highs[0]["details"]["fatigue_level"] == "high"
    assert highs[0]["sector"] == "cab" and highs[0]["machine_id"] == "M04"


def test_fatigue_high_hysteresis_holds_between_055_and_06():
    m = monitor()
    m.high = True
    m.perclos.add(0.0, False)
    # score 0.45·(0.2/0.3) + 0.15·(1/3) = 0.35: well below HIGH_EXIT → leaves high
    m.update(1.0, None, W, H, now=NOW)
    assert not m.high


def test_eyes_closed_while_moving_is_critical_and_repeats_every_2s():
    c = calibrated()
    c.run(1.9, lambda t: face(ear=0.1), machine_moving=True)
    assert not c.of_type("fatigue_high", "critical")
    c.run(0.3, lambda t: face(ear=0.1), machine_moving=True)
    assert len(c.of_type("fatigue_high", "critical")) == 1
    c.run(2.1, lambda t: face(ear=0.1), machine_moving=True)
    crit = c.of_type("fatigue_high", "critical")
    assert len(crit) == 2
    assert crit[0]["details"]["reason"] == "eyes_closed"
    assert crit[0]["details"]["eyes_closed_s"] >= 2.0


def test_eyes_closed_while_stationary_is_not_critical():
    c = calibrated()
    c.run(5, lambda t: face(ear=0.1), machine_moving=False)
    assert not c.of_type("fatigue_high", "critical")


def test_blink_resets_eyes_closed_timer():
    c = calibrated()
    for _ in range(3):
        c.run(1.5, lambda t: face(ear=0.1), machine_moving=True)
        c.run(0.2, lambda t: face(), machine_moving=True)
    assert not c.of_type("fatigue_high", "critical")


def phone_seq(tracker: F.PhoneTracker, start: float, seconds: float, conf=0.7) -> list[bool]:
    """Detection every 5th frame at 15 fps = 3 checks per second."""
    step = F.PHONE_EVERY_N_FRAMES / FPS
    return [tracker.update(start + i * step, conf) for i in range(int(seconds / step) + 1)]


def test_phone_must_persist_3s_and_posts_once_per_episode():
    p = F.PhoneTracker()
    assert not any(phone_seq(p, 0.0, 2.9))
    assert phone_seq(p, 3.0, 0.0) == [True]
    assert not any(phone_seq(p, 3.4, 10))  # still holding it: no repeat
    p.update(16.0, None)  # 2.6 s gap → episode over
    assert not p.confirmed
    fired = phone_seq(p, 16.1, 3.1)
    assert fired.count(True) == 1


def test_phone_tolerates_short_detection_gaps():
    p = F.PhoneTracker()
    phone_seq(p, 0.0, 1.0)
    p.update(1.5, None)  # missed detections for 1.5 s
    assert phone_seq(p, 2.0, 1.1).count(True) == 1


def test_monitor_posts_phone_use():
    c = calibrated()
    for i in range(int(4 * FPS)):
        checked = i % F.PHONE_EVERY_N_FRAMES == 0
        now = NOW + timedelta(seconds=c.t)
        r = c.m.update(c.t, face(), W, H, phone_conf=0.8, phone_checked=checked, now=now)
        c.events += r.events
        c.t += 1 / FPS
    phone = c.of_type("phone_use")
    assert len(phone) == 1 and phone[0]["severity"] == "warning"
    assert phone[0]["details"]["class"] == "cell phone"


# --- fatigue_sample -------------------------------------------------------------------------------
SAMPLE_KEYS = {
    "type",
    "operator_id",
    "shift_id",
    "ts",
    "ear_avg",
    "perclos_60s",
    "yawn_count",
    "head_down_events",
    "phone_detected",
    "fatigue_score",
    "fatigue_level",
}


def test_fatigue_sample_every_minute_matches_contract():
    c = Clock(monitor())
    c.run(121)
    samples = c.of_type("fatigue_sample")
    assert len(samples) == 2
    s = samples[0]
    assert set(s) == SAMPLE_KEYS
    assert s["shift_id"] == "SH-2026-09-24-M04-D" and s["operator_id"] == "OP03"
    assert s["ear_avg"] == pytest.approx(0.3, abs=0.001)
    assert s["perclos_60s"] == 0.0 and s["fatigue_level"] == "low"
    assert s["yawn_count"] == 0 and s["phone_detected"] is False


def test_sample_counts_are_per_minute():
    c = calibrated()  # t ≈ 31 s
    c.run(2, lambda t: face(mar=0.7))  # one yawn in the first minute
    c.run(90)
    first, second = c.of_type("fatigue_sample")
    assert first["yawn_count"] == 1
    assert second["yawn_count"] == 0


def test_no_face_minute_has_null_ear():
    c = Clock(monitor())
    c.run(61, lambda t: None)
    s = c.of_type("fatigue_sample")[0]
    assert s["ear_avg"] is None and s["perclos_60s"] is None


# --- shift ----------------------------------------------------------------------------------------
def test_shift_defaults_to_standard_start():
    s = F.resolve_shift(None, "day", "M04", now=NOW)  # 10:00 IST
    assert s.shift_id == "SH-2026-09-24-M04-D"
    assert s.hours_into(NOW) == pytest.approx(4.0)


def test_night_shift_after_midnight_belongs_to_previous_date():
    now = datetime(2026, 9, 24, 19, 30, tzinfo=UTC)  # 01:00 IST on the 25th
    s = F.resolve_shift(None, "night", "M01", now=now)
    assert s.shift_id == "SH-2026-09-24-M01-N"
    assert s.hours_into(now) == pytest.approx(7.0)


def test_shift_start_hhmm_and_iso():
    assert F.resolve_shift("08:30", "day", "M04", now=NOW).hours_into(NOW) == pytest.approx(1.5)
    iso = F.resolve_shift("2026-09-24T07:00:00", "day", "M04", shift_id="X", now=NOW)
    assert iso.hours_into(NOW) == pytest.approx(3.0) and iso.shift_id == "X"


def test_default_outside_the_shift_starts_now():
    now = datetime(2026, 9, 23, 21, 36, tzinfo=UTC)  # 03:06 IST: last day start was 21 h ago
    s = F.resolve_shift(None, "day", "M04", now=now)
    assert s.assumed and s.hours_into(now) == pytest.approx(0.0)
    assert s.shift_id == "SH-2026-09-24-M04-D"
    explicit = F.resolve_shift("06:00", "day", "M04", now=now)  # asked for it: keep 21 h
    assert not explicit.assumed and explicit.hours_into(now) == pytest.approx(21.1, abs=0.01)
