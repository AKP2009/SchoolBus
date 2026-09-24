"""Proximity and blindspot detection (models.md §6).

Pipeline per processed frame:

1. `Detector.track(frame)` — YOLO nano + ByteTrack, classes person / car / truck.
2. `estimate_distance_m(...)` — pinhole model d ≈ H_real × f / h_pixels, f from calibration.
3. `ProximityTracker.update(...)` — per-track distance history → closing speed → approaching flag,
   zone (red / orange / clear, widened by 2 m for low visibility or high fatigue), and the
   debounce that decides when to post an event.
4. `build_event(...)` — the `POST /events` payload of docs/api_contract.md.

Everything except `Detector` is pure Python so it is unit-tested with synthetic box sequences.
Ultralytics / OpenCV are imported lazily inside `Detector`.
"""

from __future__ import annotations

import copy
import json
import math
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

# --- models.md §6 parameters -------------------------------------------------------------------
CLASS_IDS = {0: "person", 2: "car", 7: "truck"}  # COCO ids
TRACK_KWARGS: dict[str, Any] = {
    "classes": [0, 2, 7],
    "conf": 0.45,
    "iou": 0.45,
    "imgsz": 640,
    "tracker": "bytetrack.yaml",
    "persist": True,
}
IMGSZ_FAST = 640
IMGSZ_SLOW = 480  # used once the loop runs under MIN_FPS
MIN_FPS = 10.0

# Real object heights for the pinhole model. Person is models.md §6; car and truck are our own
# assumptions (see docs/assumptions.md).
REAL_HEIGHT_M = {"person": 1.7, "car": 1.5, "truck": 3.0}

RED_LIMIT_M = 3.0
ORANGE_LIMIT_M = 7.0
WIDEN_M = 2.0  # low visibility or high fatigue
# Leaving a zone needs this much extra distance, so a person standing on a limit doesn't flip
# warning/critical every frame. Entry limits are exact.
ZONE_HYSTERESIS_M = 0.3

APPROACH_SPEED_MPS = 0.5  # closing faster than this ...
APPROACH_WINDOW_S = 1.0  # ... measured over this window
MIN_APPROACH_SPAN_S = 0.7  # need at least this much history inside the window

RED_REPEAT_S = 2.0  # re-post while a track stays red
TRACK_TTL_S = 2.0  # forget a track not seen for this long

BLINDSPOT_SECTORS = {"rear", "left", "right"}
SECTORS = ("front", "rear", "left", "right", "cab")

# Used only when vision/calibration.json is missing: f ≈ 1.2 × frame height (≈ 45° vertical FOV,
# a typical laptop webcam). Run `python vision/run.py calibrate` for real numbers.
DEFAULT_FOCAL_PER_FRAME_HEIGHT = 1.2
CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration.json"


class Zone(StrEnum):
    red = "red"
    orange = "orange"
    clear = "clear"


SEVERITY_LEVELS = ("info", "warning", "critical", "emergency")  # severity_level enum order
# Proximity never goes above critical: emergency pages the site manager (design.md alerts) and is
# reserved for SOS and injuries.
MAX_PROXIMITY_SEVERITY = "critical"
ZONE_SEVERITY = {Zone.red: "critical", Zone.orange: "warning"}


# --- calibration --------------------------------------------------------------------------------
@dataclass(frozen=True)
class Calibration:
    """Focal length in pixels at a given frame height. Scales linearly with resolution."""

    focal_px: float
    frame_height_px: int
    calibrated: bool = True

    def focal_for(self, frame_height_px: int) -> float:
        return self.focal_px * frame_height_px / self.frame_height_px

    @classmethod
    def default(cls, frame_height_px: int = 480) -> Calibration:
        return cls(DEFAULT_FOCAL_PER_FRAME_HEIGHT * frame_height_px, frame_height_px, False)

    @classmethod
    def load(cls, path: Path = CALIBRATION_PATH) -> Calibration:
        """Load the saved constant, or the uncalibrated default if the file is missing / broken."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(float(data["focal_px"]), int(data["frame_height_px"]), True)
        except (OSError, ValueError, KeyError, TypeError):
            return cls.default()


def focal_from_measurement(
    box_height_px: float, distance_m: float, real_height_m: float = REAL_HEIGHT_M["person"]
) -> float:
    """Invert the pinhole model: f = h_pixels × d / H_real."""
    if box_height_px <= 0 or distance_m <= 0 or real_height_m <= 0:
        raise ValueError("box height, distance and real height must be positive")
    return box_height_px * distance_m / real_height_m


def save_calibration(
    box_height_px: float,
    distance_m: float,
    frame_height_px: int,
    samples: int,
    path: Path = CALIBRATION_PATH,
) -> dict[str, Any]:
    data = {
        "focal_px": round(focal_from_measurement(box_height_px, distance_m), 2),
        "frame_height_px": int(frame_height_px),
        "distance_m": distance_m,
        "box_height_px": round(float(box_height_px), 2),
        "person_height_m": REAL_HEIGHT_M["person"],
        "samples": samples,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


# --- distance, zone -----------------------------------------------------------------------------
def estimate_distance_m(
    box_height_px: float,
    cls_name: str,
    focal_px: float,
    ultrasonic_m: float | None = None,
) -> float:
    """d ≈ H_real × f / h_pixels, fused with the ultrasonic reading as min(camera, ultrasonic)."""
    real = REAL_HEIGHT_M.get(cls_name, REAL_HEIGHT_M["person"])
    d_camera = real * focal_px / box_height_px if box_height_px > 0 else math.inf
    if ultrasonic_m is not None and ultrasonic_m > 0:
        return min(d_camera, ultrasonic_m)
    return d_camera


def zone_limits(widened: bool) -> tuple[float, float]:
    extra = WIDEN_M if widened else 0.0
    return RED_LIMIT_M + extra, ORANGE_LIMIT_M + extra


def classify_zone(distance_m: float, widened: bool = False, previous: Zone | None = None) -> Zone:
    red, orange = zone_limits(widened)
    if previous is Zone.red:
        red += ZONE_HYSTERESIS_M
    if previous in (Zone.red, Zone.orange):
        orange += ZONE_HYSTERESIS_M
    if distance_m < red:
        return Zone.red
    if distance_m <= orange:
        return Zone.orange
    return Zone.clear


def zone_severity(zone: Zone, approaching: bool) -> str | None:
    """red → critical, orange → warning; approaching raises one level, capped at critical."""
    base = ZONE_SEVERITY.get(zone)
    if base is None:
        return None
    i = SEVERITY_LEVELS.index(base) + (1 if approaching else 0)
    return SEVERITY_LEVELS[min(i, SEVERITY_LEVELS.index(MAX_PROXIMITY_SEVERITY))]


def event_type_for(sector: str) -> str:
    return "blindspot_intrusion" if sector in BLINDSPOT_SECTORS else "proximity_breach"


# --- per-track history --------------------------------------------------------------------------
@dataclass
class DistanceHistory:
    """Timestamped distances of one track, trimmed to the approach window."""

    samples: deque[tuple[float, float]] = field(default_factory=deque)

    def add(self, t: float, distance_m: float) -> None:
        self.samples.append((t, distance_m))
        # keep one sample at or before the window start so the full window is measurable
        while len(self.samples) > 2 and self.samples[1][0] <= t - APPROACH_WINDOW_S:
            self.samples.popleft()

    def closing_speed_mps(self) -> float | None:
        """Positive when the object gets closer. None until enough history exists."""
        if len(self.samples) < 2:
            return None
        t_now, d_now = self.samples[-1]
        t_old, d_old = self.samples[0]
        span = t_now - t_old
        if span < MIN_APPROACH_SPAN_S:
            return None
        return (d_old - d_now) / span

    def approaching(self) -> bool:
        speed = self.closing_speed_mps()
        return speed is not None and speed > APPROACH_SPEED_MPS


@dataclass
class TrackState:
    history: DistanceHistory = field(default_factory=DistanceHistory)
    last_seen: float = 0.0
    zone: Zone = Zone.clear
    severity: str | None = None
    last_post: float | None = None


@dataclass(frozen=True)
class Detection:
    track_id: int
    cls_name: str
    conf: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in frame pixels

    @property
    def height_px(self) -> float:
        return self.box[3] - self.box[1]


@dataclass(frozen=True)
class TrackReading:
    """One detection after distance / zone / approach logic. `post` says whether to send it."""

    detection: Detection
    distance_m: float
    zone: Zone
    approaching: bool
    closing_speed_mps: float | None
    severity: str | None
    post: bool


class ProximityTracker:
    """Keeps per-track state and applies the debounce.

    Post an event when a track's zone changes into red or orange, when its severity rises inside the
    same zone (it started approaching), and every RED_REPEAT_S while it stays red. Going clear posts
    nothing (there is no "clear" event type) but resets the state, so re-entry posts again.
    """

    def __init__(self) -> None:
        self.tracks: dict[int, TrackState] = {}

    def update(
        self,
        detections: list[Detection],
        t: float,
        focal_px: float,
        widened: bool = False,
        ultrasonic_m: float | None = None,
    ) -> list[TrackReading]:
        readings = [self._update_one(d, t, focal_px, widened, ultrasonic_m) for d in detections]
        for tid in [k for k, s in self.tracks.items() if t - s.last_seen > TRACK_TTL_S]:
            del self.tracks[tid]
        return readings

    def _update_one(
        self,
        det: Detection,
        t: float,
        focal_px: float,
        widened: bool,
        ultrasonic_m: float | None,
    ) -> TrackReading:
        state = self.tracks.setdefault(det.track_id, TrackState())
        distance = estimate_distance_m(det.height_px, det.cls_name, focal_px, ultrasonic_m)
        state.history.add(t, distance)
        state.last_seen = t

        zone = classify_zone(distance, widened, state.zone)
        approaching = state.history.approaching()
        severity = zone_severity(zone, approaching)

        post = False
        if zone is not Zone.clear:
            if zone is not state.zone:
                post = True
            elif _rank(severity) > _rank(state.severity):
                post = True
            elif zone is Zone.red and (
                state.last_post is None or t - state.last_post >= RED_REPEAT_S
            ):
                post = True
        if post:
            state.last_post = t
        if zone is Zone.clear:
            state.last_post = None
        state.zone = zone
        state.severity = severity

        return TrackReading(
            detection=det,
            distance_m=distance,
            zone=zone,
            approaching=approaching,
            closing_speed_mps=state.history.closing_speed_mps(),
            severity=severity,
            post=post,
        )


def _rank(severity: str | None) -> int:
    return -1 if severity is None else SEVERITY_LEVELS.index(severity)


def utc_ts(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_event(
    reading: TrackReading,
    machine_id: str,
    operator_id: str | None,
    sector: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """`POST /events` body, docs/api_contract.md."""
    det = reading.detection
    return {
        "type": event_type_for(sector),
        "machine_id": machine_id,
        "operator_id": operator_id,
        "ts": utc_ts(now),
        "severity": reading.severity,
        "distance_m": round(reading.distance_m, 1),
        "sector": sector,
        "approaching": reading.approaching,
        "details": {"track_id": det.track_id, "class": det.cls_name, "conf": round(det.conf, 2)},
    }


# --- detector -----------------------------------------------------------------------------------
class Detector:
    """Ultralytics YOLO nano + ByteTrack. Weights download on first use to vision/weights/."""

    def __init__(self, weights: str | Path = "yolo11n.pt") -> None:
        # stdout carries the --dry-run JSON lines; keep Ultralytics' banner and logs off it
        os.environ.setdefault("YOLO_VERBOSE", "False")
        from ultralytics import YOLO  # lazy: tests must not need torch

        weights_path = Path(weights)
        if not weights_path.is_absolute() and weights_path.parent == Path("."):
            weights_dir = Path(__file__).resolve().parent / "weights"
            weights_dir.mkdir(exist_ok=True)
            weights_path = weights_dir / weights_path.name
        self.model = YOLO(str(weights_path))
        self.imgsz = IMGSZ_FAST
        self._plain: Any = None  # see _plain_model

    def track(self, frame: np.ndarray) -> list[Detection]:
        kwargs = {**TRACK_KWARGS, "imgsz": self.imgsz}
        result = self.model.track(frame, verbose=False, **kwargs)[0]
        boxes = result.boxes
        if boxes is None or boxes.id is None:  # no confirmed tracks yet
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        ids = boxes.id.int().cpu().tolist()
        classes = boxes.cls.int().cpu().tolist()
        confs = boxes.conf.cpu().tolist()
        return [
            Detection(tid, CLASS_IDS.get(c, str(c)), float(conf), tuple(float(v) for v in box))
            for tid, c, conf, box in zip(ids, classes, confs, xyxy, strict=True)
        ]

    def _plain_model(self) -> Any:
        """The same loaded weights behind a separate predictor for plain (untracked) detection.

        `model.track` registers ByteTrack callbacks on the model and its predictor; a later
        `model.predict` on the same object would run them too, feeding cab-camera frames into the
        proximity tracker and dropping unconfirmed detections. A shallow copy shares the network but
        gets its own predictor and callbacks.
        """
        if self._plain is None:
            from ultralytics.utils import callbacks

            plain = copy.copy(self.model)
            plain.predictor = None
            plain.callbacks = callbacks.get_default_callbacks()
            self._plain = plain
        return self._plain

    def phone_conf(self, frame: np.ndarray, conf: float, class_id: int) -> float | None:
        """Best cell-phone confidence in the frame (models.md §5), or None if there is none."""
        result = self._plain_model().predict(
            frame, classes=[class_id], conf=conf, imgsz=self.imgsz, verbose=False
        )[0]
        if result.boxes is None or len(result.boxes) == 0:
            return None
        return float(result.boxes.conf.max())

    def detect_people(self, frame: np.ndarray) -> list[Detection]:
        """Plain detection (no tracking), used by calibration."""
        result = self._plain_model().predict(
            frame, classes=[0], conf=TRACK_KWARGS["conf"], imgsz=self.imgsz, verbose=False
        )[0]
        if result.boxes is None:
            return []
        return [
            Detection(-1, "person", float(conf), tuple(float(v) for v in box))
            for box, conf in zip(
                result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().tolist(), strict=True
            )
        ]
