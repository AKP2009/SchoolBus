"""Debug overlays: proximity (boxes, track id, distance, zone, fps, sector) and fatigue (EAR,
PERCLOS, level, yawn / head-down counts, phone, calibration).

Colours are the cab status colours of docs/design.md (BGR for OpenCV). Every coloured label also
carries its status word, so colour is never the only signal.
"""

from __future__ import annotations

import cv2
import numpy as np

from fatigue import LEFT_EYE, RIGHT_EYE, FatigueReading
from proximity import TrackReading, Zone, zone_limits

# docs/design.md "Status (safety signage)", cab hex → BGR
ZONE_BGR = {
    Zone.red: (0x4D, 0x48, 0xE5),  # #E5484D critical
    Zone.orange: (0x1A, 0x7A, 0xFF),  # #FF7A1A warning
    Zone.clear: (0x6B, 0xA3, 0x2F),  # #2FA36B ok
}
ZONE_WORD = {Zone.red: "CRITICAL", Zone.orange: "WARNING", Zone.clear: "CLEAR"}
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _label(img: np.ndarray, text: str, org: tuple[int, int], bg: tuple[int, int, int]) -> None:
    (w, h), base = cv2.getTextSize(text, FONT, 0.55, 1)
    x, y = org
    y = max(y, h + base + 2)
    cv2.rectangle(img, (x, y - h - base - 4), (x + w + 6, y), bg, -1)
    cv2.putText(img, text, (x + 3, y - base - 1), FONT, 0.55, WHITE, 1, cv2.LINE_AA)


def draw(
    frame: np.ndarray,
    readings: list[TrackReading],
    fps: float,
    sector: str,
    imgsz: int,
    widened: bool,
    calibrated: bool,
) -> np.ndarray:
    img = frame.copy()
    for r in readings:
        x1, y1, x2, y2 = (int(v) for v in r.detection.box)
        colour = ZONE_BGR[r.zone]
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, 3 if r.zone is Zone.red else 2)
        arrow = " >>" if r.approaching else ""
        text = (
            f"#{r.detection.track_id} {r.detection.cls_name} {r.distance_m:.1f} m "
            f"{ZONE_WORD[r.zone]}{arrow}"
        )
        _label(img, text, (x1, y1), colour)

    red, orange = zone_limits(widened)
    lines = [
        f"SECTOR: {sector.upper()}",
        f"fps {fps:4.1f}  imgsz {imgsz}",
        f"red <{red:.0f} m  orange <={orange:.0f} m" + ("  (widened)" if widened else ""),
    ]
    if not calibrated:
        lines.append("UNCALIBRATED - run: python vision/run.py calibrate")
    for i, line in enumerate(lines):
        _label(img, line, (8, 26 + i * 24), BLACK)
    return img


LEVEL_BGR = {
    "low": ZONE_BGR[Zone.clear],
    "medium": ZONE_BGR[Zone.orange],
    "high": ZONE_BGR[Zone.red],
}
EYE_POINTS = RIGHT_EYE + LEFT_EYE


def draw_fatigue(
    frame: np.ndarray,
    r: FatigueReading | None,
    landmarks: np.ndarray | None,
    fps: float,
    machine_moving: bool,
    moving_source: str = "flag",
) -> np.ndarray:
    img = frame.copy()
    if landmarks is not None:
        for i in EYE_POINTS:
            x, y = (int(v) for v in landmarks[i, :2])
            cv2.circle(img, (x, y), 2, WHITE, -1, cv2.LINE_AA)
    moving = "MOVING" if machine_moving else "stopped"
    lines: list[tuple[str, tuple[int, int, int]]] = [
        (f"CAB  fps {fps:4.1f}  machine {moving} ({moving_source})", BLACK)
    ]
    if r is None:
        lines.append(("starting...", BLACK))
    else:
        if r.calibrating:
            lines.append(
                (f"CALIBRATING {r.calibration_left_s:.0f} s - eyes open, look ahead", BLACK)
            )
        else:
            how = "default, calibration failed" if r.calibration_fallback else "calibrated"
            lines.append((f"EAR threshold {r.ear_threshold:.3f} ({how})", BLACK))
        if not r.face:
            lines.append(("NO FACE", ZONE_BGR[Zone.orange]))
        else:
            eyes = f"CLOSED {r.closed_s:.1f} s" if r.eyes_closed else "open"
            lines.append((f"EAR {r.ear:.3f}  eyes {eyes}", BLACK))
        perclos = "--" if r.perclos is None else f"{r.perclos:.0%}"
        lines.append((f"PERCLOS 60 s {perclos}", BLACK))
        lines.append((f"FATIGUE {r.level.upper()}  score {r.score:.2f}", LEVEL_BGR[r.level]))
        lines.append((f"yawns 10 min {r.yawns_10min}  head-down 10 min {r.head_down_10min}", BLACK))
        if r.phone_confirmed:
            lines.append(("PHONE DETECTED", ZONE_BGR[Zone.orange]))
        elif r.phone_seen:
            lines.append(("phone seen, checking 3 s", BLACK))
        else:
            lines.append(("phone: none", BLACK))
    for i, (line, bg) in enumerate(lines):
        _label(img, line, (8, 26 + i * 24), bg)
    return img
