"""Fatigue and attention (models.md §5).

Pipeline per cab-camera frame:

1. `FaceMesh.landmarks(frame)` — MediaPipe FaceLandmarker (the Tasks API of Face Mesh: 478 points
   with iris), returned as pixel coordinates.
2. `ear(...)`, `mar(...)`, `head_pitch_deg(...)` — eye aspect ratio, mouth aspect ratio, and head
   pitch from `cv2.solvePnP` on six face landmarks.
3. `FatigueMonitor.update(...)` — the 30 s EAR calibration, rolling 60 s PERCLOS, yawn and
   head-down triggers with 10-minute counters, the phone persistence check, the fatigue score and
   level, the eyes-closed-while-moving check, and the debounce that decides what to post.
4. The payloads it returns are the `POST /events` bodies of docs/api_contract.md: a
   `fatigue_sample` every minute, `fatigue_high` and `phone_use` alerts.

Everything except `FaceMesh` is pure Python + numpy (+ cv2 for solvePnP), so it is unit-tested with
synthetic landmark sequences. MediaPipe is imported lazily inside `FaceMesh`.
"""

from __future__ import annotations

import math
import os
import statistics
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from proximity import utc_ts

# --- models.md §5 parameters -------------------------------------------------------------------
# Face Mesh parameters. refine_landmarks=True has no switch in the Tasks API: the model always
# returns the 468 mesh points plus 10 iris points.
FACE_MESH_KWARGS: dict[str, Any] = {
    "num_faces": 1,  # max_num_faces
    "min_face_detection_confidence": 0.5,  # min_detection_confidence
    "min_face_presence_confidence": 0.5,
    "min_tracking_confidence": 0.5,
}
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/"
    "face_landmarker.task"
)
WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"

# EAR landmarks p1..p6: outer corner, upper x2, inner corner, lower x2 (Soukupová & Čech).
RIGHT_EYE = (33, 160, 158, 133, 153, 144)  # subject's right eye, left side of the image
LEFT_EYE = (362, 385, 387, 263, 373, 380)
# MAR: three inner-lip vertical gaps over the inner mouth width. Closed ≈ 0, talking ≈ 0.1–0.4.
MOUTH_VERTICAL = ((82, 87), (13, 14), (312, 317))
MOUTH_CORNERS = (78, 308)

EAR_CLOSED_DEFAULT = 0.22  # fallback threshold until / unless calibration succeeds
CALIBRATION_S = 30.0
CALIBRATION_FACTOR = 0.75  # threshold = 0.75 × open-eye mean
MIN_CALIBRATION_FRAMES = 60  # fewer face frames than this in 30 s → keep the fallback
OPEN_EYE_OF_MEDIAN = 0.8  # calibration frames below 0.8 × median EAR are blinks, not open eyes
CALIBRATION_THRESHOLD_RANGE = (0.12, 0.30)  # a threshold outside this is a bad calibration

PERCLOS_WINDOW_S = 60.0
PERCLOS_MIN_SPAN_S = 30.0  # fatigue_high needs at least this much PERCLOS history
YAWN_MAR = 0.6
YAWN_HOLD_S = 1.5
HEAD_DOWN_PITCH_DEG = -20.0  # relative to the neutral pitch measured during calibration
HEAD_DOWN_HOLD_S = 2.0
COUNT_WINDOW_S = 600.0  # yawns / head-down events over 10 minutes

PHONE_CLASS_ID = 67  # COCO "cell phone"
PHONE_CONF = 0.5
PHONE_EVERY_N_FRAMES = 5
PHONE_PERSIST_S = 3.0  # detected continuously this long before posting
PHONE_GAP_S = 2.0  # a phone unseen this long ends the episode (like proximity's track TTL)

EYES_CLOSED_CRITICAL_S = 2.0  # eyes closed this long while the machine moves → critical
CRITICAL_REPEAT_S = 2.0  # re-post while it lasts, like proximity's red zone

SAMPLE_PERIOD_S = 60.0  # one fatigue_sample (fatigue_log row) per minute

LEVEL_MEDIUM = 0.35
LEVEL_HIGH = 0.6
HIGH_EXIT = 0.55  # leaving "high" needs the score below this, so the alert doesn't flicker

# docs/synthetic_data.md: day 06:00–14:00, night 18:00–02:00, site time Asia/Kolkata
# fixed +05:30 like data/generator/common.py: India has no DST, and Windows has no tz database
SITE_TZ = timezone(timedelta(hours=5, minutes=30), "IST")
SHIFT_START = {"day": time(6, 0), "night": time(18, 0)}
SHIFT_LENGTH = timedelta(hours=8)


# --- geometry -----------------------------------------------------------------------------------
def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a[:2] - b[:2]))


def eye_aspect_ratio(p: np.ndarray) -> float:
    """EAR = (|p2−p6| + |p3−p5|) / (2 |p1−p4|) for six eye points in order."""
    width = _dist(p[0], p[3])
    if width <= 0:
        return 0.0
    return (_dist(p[1], p[5]) + _dist(p[2], p[4])) / (2.0 * width)


def ear(landmarks: np.ndarray) -> float:
    """Mean EAR of both eyes. `landmarks` is (N, 2+) pixel coordinates in Face Mesh order."""
    right = eye_aspect_ratio(landmarks[list(RIGHT_EYE)])
    left = eye_aspect_ratio(landmarks[list(LEFT_EYE)])
    return (right + left) / 2.0


def mar(landmarks: np.ndarray) -> float:
    width = _dist(landmarks[MOUTH_CORNERS[0]], landmarks[MOUTH_CORNERS[1]])
    if width <= 0:
        return 0.0
    gaps = [_dist(landmarks[a], landmarks[b]) for a, b in MOUTH_VERTICAL]
    return float(np.mean(gaps)) / width


# solvePnP: a generic face in millimetres, in camera axes (x right, y down, z away from the camera),
# nose tip at the origin. A frontal, level face then has rotation ≈ identity.
PNP_LANDMARKS = (1, 152, 33, 263, 61, 291)  # nose tip, chin, eye outer corners, mouth corners
PNP_MODEL_MM = np.array(
    [
        (0.0, 0.0, 0.0),
        (0.0, 330.0, 65.0),
        (-225.0, -170.0, 135.0),
        (225.0, -170.0, 135.0),
        (-150.0, 150.0, 125.0),
        (150.0, 150.0, 125.0),
    ],
    dtype=np.float64,
)


def camera_matrix(frame_w: int, frame_h: int) -> np.ndarray:
    """Pinhole camera with focal ≈ frame width, principal point at the centre, no distortion."""
    f = float(frame_w)
    return np.array([[f, 0, frame_w / 2.0], [0, f, frame_h / 2.0], [0, 0, 1]], dtype=np.float64)


def head_pitch_deg(landmarks: np.ndarray, frame_w: int, frame_h: int) -> float | None:
    """Head pitch in degrees: negative = chin towards the chest. None if solvePnP fails."""
    image_pts = np.ascontiguousarray(landmarks[list(PNP_LANDMARKS), :2], dtype=np.float64)
    ok, rvec, _ = cv2.solvePnP(
        PNP_MODEL_MM,
        image_pts,
        camera_matrix(frame_w, frame_h),
        np.zeros(4),
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        return None
    rot, _ = cv2.Rodrigues(rvec)
    # rotation about the camera x axis; positive tips the chin away from the camera (head down)
    return -math.degrees(math.atan2(rot[2, 1], rot[2, 2]))


# --- score --------------------------------------------------------------------------------------
def fatigue_score(
    perclos: float,
    yawns_10min: int,
    head_down_10min: int,
    hours_into_shift: float,
    shift_type: str,
) -> float:
    """models.md §5, 0–1."""
    return (
        0.45 * min(perclos / 0.3, 1.0)
        + 0.15 * min(yawns_10min / 3, 1.0)
        + 0.15 * min(head_down_10min / 3, 1.0)
        + 0.15 * min(max(hours_into_shift, 0.0) / 10, 1.0)
        + 0.10 * (shift_type == "night")
    )


def fatigue_level(score: float) -> str:
    """low < 0.35 ≤ medium < 0.6 ≤ high."""
    if score >= LEVEL_HIGH:
        return "high"
    if score >= LEVEL_MEDIUM:
        return "medium"
    return "low"


# --- shift --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Shift:
    start: datetime  # aware
    shift_type: str
    shift_id: str | None
    assumed: bool = False  # no --shift-start and outside the standard shift: starts now

    def hours_into(self, now: datetime | None = None) -> float:
        return max(((now or datetime.now(UTC)) - self.start).total_seconds() / 3600.0, 0.0)


def resolve_shift(
    shift_start: str | None,
    shift_type: str,
    machine_id: str,
    shift_id: str | None = None,
    now: datetime | None = None,
) -> Shift:
    """Shift from the CLI (a stand-in until the backend tells the vision service the shift).

    `shift_start` is an ISO datetime (naive = site time) or `HH:MM` site time, meaning the most
    recent such time; default is the standard start of `shift_type` (06:00 day / 18:00 night)
    if that shift is still running, else now (0 h in, `assumed=True`).
    The shift id is `SH-<site date of the start>-<machine>-<D|N>` unless given.
    """
    now = now or datetime.now(UTC)
    local_now = now.astimezone(SITE_TZ)
    explicit = shift_start is not None
    assumed = False
    if shift_start and "T" not in shift_start and len(shift_start) <= 5:
        hh, mm = (int(x) for x in shift_start.split(":"))
        start_time = time(hh, mm)
        shift_start = None
    else:
        start_time = SHIFT_START[shift_type]
    if shift_start:
        start = datetime.fromisoformat(shift_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=SITE_TZ)
    else:
        start = datetime.combine(local_now.date(), start_time, SITE_TZ)
        if start > local_now:
            start -= timedelta(days=1)
        if not explicit and local_now - start > SHIFT_LENGTH:
            start, assumed = local_now, True
    local_start = start.astimezone(SITE_TZ)
    sid = shift_id or f"SH-{local_start.date().isoformat()}-{machine_id}-{shift_type[0].upper()}"
    return Shift(start.astimezone(UTC), shift_type, sid, assumed)


# --- rolling state ------------------------------------------------------------------------------
@dataclass
class EarCalibration:
    """Collects EAR for the first 30 s; threshold = 0.75 × mean of the open-eye frames."""

    duration_s: float = CALIBRATION_S
    start: float | None = None
    samples: list[float] = field(default_factory=list)
    threshold: float = EAR_CLOSED_DEFAULT
    pitches: list[float] = field(default_factory=list)
    neutral_pitch: float = 0.0
    done: bool = False
    fallback: bool = False  # finished, but too little data or implausible → default threshold

    def update(self, t: float, ear_value: float | None, pitch: float | None) -> None:
        if self.done:
            return
        if self.start is None:
            self.start = t
        if ear_value is not None:
            self.samples.append(ear_value)
        if pitch is not None:
            self.pitches.append(pitch)
        if t - self.start >= self.duration_s:
            self._finish()

    def _finish(self) -> None:
        self.done = True
        if self.pitches:
            self.neutral_pitch = statistics.median(self.pitches)
        if len(self.samples) < MIN_CALIBRATION_FRAMES:
            self.fallback = True
            return
        median = statistics.median(self.samples)
        open_eye = [s for s in self.samples if s >= OPEN_EYE_OF_MEDIAN * median]
        threshold = CALIBRATION_FACTOR * statistics.fmean(open_eye)
        lo, hi = CALIBRATION_THRESHOLD_RANGE
        if lo <= threshold <= hi:
            self.threshold = threshold
        else:
            self.fallback = True

    def remaining_s(self, t: float) -> float:
        if self.done or self.start is None:
            return 0.0 if self.done else self.duration_s
        return max(self.duration_s - (t - self.start), 0.0)


@dataclass
class Perclos:
    """Share of face frames with eyes closed over the last 60 s."""

    window_s: float = PERCLOS_WINDOW_S
    frames: deque[tuple[float, bool]] = field(default_factory=deque)
    closed: int = 0

    def add(self, t: float, is_closed: bool) -> None:
        self.frames.append((t, is_closed))
        self.closed += is_closed
        while self.frames and self.frames[0][0] <= t - self.window_s:
            self.closed -= self.frames.popleft()[1]

    def value(self) -> float | None:
        return self.closed / len(self.frames) if self.frames else None

    def span_s(self) -> float:
        return self.frames[-1][0] - self.frames[0][0] if len(self.frames) > 1 else 0.0


@dataclass
class HoldTrigger:
    """True once when a condition has held for more than `hold_s`; re-arms when it goes false."""

    hold_s: float
    since: float | None = None
    fired: bool = False

    def update(self, t: float, condition: bool) -> bool:
        if not condition:
            self.since, self.fired = None, False
            return False
        if self.since is None:
            self.since = t
        if not self.fired and t - self.since > self.hold_s:
            self.fired = True
            return True
        return False

    def held_s(self, t: float) -> float:
        return 0.0 if self.since is None else t - self.since


@dataclass
class WindowCounter:
    """Event timestamps over a sliding window (the 10-minute yawn / head-down counters)."""

    window_s: float = COUNT_WINDOW_S
    times: deque[float] = field(default_factory=deque)

    def add(self, t: float) -> None:
        self.times.append(t)

    def count(self, t: float) -> int:
        while self.times and self.times[0] <= t - self.window_s:
            self.times.popleft()
        return len(self.times)


@dataclass
class PhoneTracker:
    """Phone must be seen continuously for 3 s (gaps under 2 s allowed) before it is confirmed.

    Posts once per episode; an episode ends after 2 s without a detection, so a new one posts again.
    """

    first_seen: float | None = None
    last_seen: float | None = None
    confirmed: bool = False
    best_conf: float = 0.0

    def expire(self, t: float) -> None:
        """End the episode if the phone has not been seen for PHONE_GAP_S."""
        if self.last_seen is not None and t - self.last_seen > PHONE_GAP_S:
            self.first_seen = self.last_seen = None
            self.confirmed = False
            self.best_conf = 0.0

    def update(self, t: float, conf: float | None) -> bool:
        """`conf` is the best phone confidence on this detection frame, None if no phone.
        Returns True once, when the phone is confirmed."""
        self.expire(t)
        if conf is None:
            return False
        if self.first_seen is None:
            self.first_seen = t
        self.last_seen = t
        self.best_conf = max(self.best_conf, conf)
        if not self.confirmed and t - self.first_seen >= PHONE_PERSIST_S:
            self.confirmed = True
            return True
        return False

    def seen_s(self) -> float:
        if self.first_seen is None or self.last_seen is None:
            return 0.0
        return self.last_seen - self.first_seen


# --- monitor ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FatigueReading:
    """State after one frame, for the overlay, plus the events to post."""

    face: bool
    ear: float | None
    mar: float | None
    pitch_deg: float | None  # relative to the calibrated neutral pitch
    eyes_closed: bool
    closed_s: float
    perclos: float | None
    yawns_10min: int
    head_down_10min: int
    score: float
    level: str
    phone_seen: bool
    phone_confirmed: bool
    calibrating: bool
    calibration_left_s: float
    ear_threshold: float
    calibration_fallback: bool
    events: list[dict[str, Any]]


class FatigueMonitor:
    """All fatigue state for one operator. `update` is called once per cab-camera frame.

    Debounce (same shape as proximity):
    - `fatigue_high` (warning) when the level becomes high; it must drop below HIGH_EXIT before it
      can post again, and it needs PERCLOS_MIN_SPAN_S of PERCLOS history first.
    - `fatigue_high` (critical, reason eyes_closed) when the eyes stay closed > 2 s while the
      machine moves, then every 2 s while that lasts; eyes opening or the machine stopping resets
      it.
    - `phone_use` (warning) once per phone episode, after 3 s of persistence.
    - `fatigue_sample` every 60 s.
    """

    def __init__(
        self,
        machine_id: str,
        operator_id: str | None,
        shift: Shift,
        t0: float | None = None,
    ) -> None:
        self.machine_id = machine_id
        self.operator_id = operator_id
        self.shift = shift
        self.calibration = EarCalibration()
        self.perclos = Perclos()
        self.yawn = HoldTrigger(YAWN_HOLD_S)
        self.head_down = HoldTrigger(HEAD_DOWN_HOLD_S)
        self.eyes_closed = HoldTrigger(EYES_CLOSED_CRITICAL_S)
        self.yawns = WindowCounter()
        self.head_downs = WindowCounter()
        self.phone = PhoneTracker()
        self.high = False
        self.last_critical: float | None = None
        # per-minute sample accumulators
        self.sample_start = t0
        self.minute_ears: list[float] = []
        self.minute_yawns = 0
        self.minute_head_downs = 0
        self.minute_phone = False
        self.last: FatigueReading | None = None

    def update(
        self,
        t: float,
        landmarks: np.ndarray | None,
        frame_w: int,
        frame_h: int,
        machine_moving: bool = False,
        phone_conf: float | None = None,
        phone_checked: bool = False,
        now: datetime | None = None,
    ) -> FatigueReading:
        """`phone_checked` says whether YOLO ran on this frame; `phone_conf` is its best phone
        confidence (None = no phone). `now` is the wall clock for timestamps and shift hours."""
        now = now or datetime.now(UTC)
        if self.sample_start is None:
            self.sample_start = t
        events: list[dict[str, Any]] = []
        cal = self.calibration

        ear_v = mar_v = raw_pitch = None
        if landmarks is not None:
            ear_v = ear(landmarks)
            mar_v = mar(landmarks)
            raw_pitch = head_pitch_deg(landmarks, frame_w, frame_h)
        cal.update(t, ear_v, raw_pitch)
        pitch = None if raw_pitch is None else raw_pitch - cal.neutral_pitch

        closed = ear_v is not None and ear_v < cal.threshold
        if ear_v is not None:
            self.perclos.add(t, closed)
            self.minute_ears.append(ear_v)
        if self.yawn.update(t, mar_v is not None and mar_v > YAWN_MAR):
            self.yawns.add(t)
            self.minute_yawns += 1
        # head-down needs the neutral pitch, so it only runs once calibration is over
        head_down = cal.done and pitch is not None and pitch < HEAD_DOWN_PITCH_DEG
        if self.head_down.update(t, head_down):
            self.head_downs.add(t)
            self.minute_head_downs += 1

        perclos = self.perclos.value()
        yawns_10 = self.yawns.count(t)
        head_10 = self.head_downs.count(t)
        hours = self.shift.hours_into(now)
        score = fatigue_score(perclos or 0.0, yawns_10, head_10, hours, self.shift.shift_type)
        level = fatigue_level(score)
        base_details = {
            "fatigue_score": round(score, 3),
            "fatigue_level": level,
            "perclos_60s": None if perclos is None else round(perclos, 3),
            "yawns_10min": yawns_10,
            "head_down_10min": head_10,
        }

        # fatigue_high on entering high, with hysteresis
        if self.high:
            self.high = score >= HIGH_EXIT
        elif level == "high" and self.perclos.span_s() >= PERCLOS_MIN_SPAN_S:
            self.high = True
            events.append(self._event("fatigue_high", "warning", now, base_details))

        # eyes closed > 2 s while moving → critical, repeated every 2 s
        closed_moving = closed and machine_moving
        self.eyes_closed.update(t, closed_moving)
        closed_s = self.eyes_closed.held_s(t)
        if closed_moving and closed_s > EYES_CLOSED_CRITICAL_S:
            if self.last_critical is None or t - self.last_critical >= CRITICAL_REPEAT_S:
                self.last_critical = t
                details = {
                    "reason": "eyes_closed",
                    "eyes_closed_s": round(closed_s, 2),
                    "machine_moving": True,
                    **base_details,
                }
                events.append(self._event("fatigue_high", "critical", now, details))
        else:
            self.last_critical = None

        if phone_checked and self.phone.update(t, phone_conf):
            details = {
                "class": "cell phone",
                "conf": round(self.phone.best_conf, 2),
                "seen_s": round(self.phone.seen_s(), 1),
            }
            events.append(self._event("phone_use", "warning", now, details))
        self.phone.expire(t)
        self.minute_phone |= self.phone.confirmed

        if t - self.sample_start >= SAMPLE_PERIOD_S:
            events.append(self._sample(now, perclos, score, level))
            self.sample_start = t

        self.last = FatigueReading(
            face=landmarks is not None,
            ear=ear_v,
            mar=mar_v,
            pitch_deg=pitch,
            eyes_closed=closed,
            closed_s=closed_s if closed else 0.0,
            perclos=perclos,
            yawns_10min=yawns_10,
            head_down_10min=head_10,
            score=score,
            level=level,
            phone_seen=self.phone.first_seen is not None,
            phone_confirmed=self.phone.confirmed,
            calibrating=not cal.done,
            calibration_left_s=cal.remaining_s(t),
            ear_threshold=cal.threshold,
            calibration_fallback=cal.fallback,
            events=events,
        )
        return self.last

    def _event(
        self, event_type: str, severity: str, now: datetime, details: dict[str, Any]
    ) -> dict[str, Any]:
        """`POST /events` alert body; sector is the cab camera."""
        return {
            "type": event_type,
            "machine_id": self.machine_id,
            "operator_id": self.operator_id,
            "ts": utc_ts(now),
            "severity": severity,
            "sector": "cab",
            "details": {"shift_id": self.shift.shift_id, **details},
        }

    def _sample(
        self, now: datetime, perclos: float | None, score: float, level: str
    ) -> dict[str, Any]:
        """`fatigue_sample` body = one fatigue_log row. Counts are for the minute just ended."""
        sample = {
            "type": "fatigue_sample",
            "operator_id": self.operator_id,
            "shift_id": self.shift.shift_id,
            "ts": utc_ts(now),
            "ear_avg": round(statistics.fmean(self.minute_ears), 3) if self.minute_ears else None,
            "perclos_60s": None if perclos is None else round(perclos, 3),
            "yawn_count": self.minute_yawns,
            "head_down_events": self.minute_head_downs,
            "phone_detected": self.minute_phone,
            "fatigue_score": round(score, 3),
            "fatigue_level": level,
        }
        self.minute_ears = []
        self.minute_yawns = self.minute_head_downs = 0
        self.minute_phone = self.phone.confirmed
        return sample


# --- detectors ----------------------------------------------------------------------------------
def download_face_model(path: Path | None = None) -> Path:
    path = path or WEIGHTS_DIR / "face_landmarker.task"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(FACE_MODEL_URL, tmp)  # noqa: S310 (fixed https URL)
        tmp.replace(path)
    return path


class FaceMesh:
    """MediaPipe FaceLandmarker in VIDEO mode. The model file downloads on first use."""

    def __init__(self, model_path: Path | None = None) -> None:
        os.environ.setdefault("GLOG_minloglevel", "2")  # MediaPipe's C++ INFO/WARNING lines
        import mediapipe as mp  # lazy: tests must not need mediapipe
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(download_face_model(model_path))),
            running_mode=vision.RunningMode.VIDEO,
            **FACE_MESH_KWARGS,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ms = -1

    def landmarks(self, frame_bgr: np.ndarray, t: float) -> np.ndarray | None:
        """(478, 3) array: x, y in pixels, z in MediaPipe's relative units. None if no face."""
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts_ms = max(int(t * 1000), self._last_ms + 1)  # VIDEO mode needs increasing timestamps
        self._last_ms = ts_ms
        result = self._landmarker.detect_for_video(image, ts_ms)
        if not result.face_landmarks:
            return None
        pts = result.face_landmarks[0]
        return np.array([(p.x * w, p.y * h, p.z * w) for p in pts], dtype=np.float64)

    def close(self) -> None:
        self._landmarker.close()
