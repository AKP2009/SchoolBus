"""Vision service entry point.

    python vision/run.py --camera 0 --sector rear --machine-id M04 --operator-id OP03 --dry-run
    python vision/run.py --source demo.mp4 --backend http://localhost:8000
    python vision/run.py --mode fatigue --camera 0 --operator-id OP03 --shift-type night --dry-run
    python vision/run.py --mode both --camera 1 --cab-camera 0 --sector rear --dry-run
    python vision/run.py --mode both --camera 0 --cab-camera rtsp://192.168.1.20:554/stream1
    python vision/run.py calibrate --distance 3

--mode proximity (default) watches --camera / --source; fatigue watches --cab-camera (falls back to
the proximity camera); both runs the two pipelines on two cameras, or on one if they are the same.
Events (JSON lines in --dry-run) go to stdout; logs and the run summary go to stderr.
Press q in an overlay window or Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from events import EventSink  # noqa: E402
from fatigue import (  # noqa: E402
    PHONE_CLASS_ID,
    PHONE_CONF,
    PHONE_EVERY_N_FRAMES,
    FaceMesh,
    FatigueMonitor,
    FatigueReading,
    resolve_shift,
)
from overlay import draw, draw_fatigue  # noqa: E402
from proximity import (  # noqa: E402
    CALIBRATION_PATH,
    IMGSZ_SLOW,
    MIN_FPS,
    SECTORS,
    Calibration,
    Detector,
    FatigueStatus,
    ProximityTracker,
    TrackReading,
    build_event,
    save_calibration,
)

log = logging.getLogger("vision")
FPS_WINDOW_S = 3.0  # fps averaged over this many seconds for the 640 -> 480 decision
WARMUP_S = 3.0  # first inferences are slow (torch start-up); don't judge fps before this


# --- cameras ------------------------------------------------------------------------------------
class Camera:
    """A webcam index, an IP camera URL or a video file.

    Webcams and files are read synchronously, one frame per `read`. A URL stream is read on a
    background thread that keeps only the newest frame, because network streams buffer and a loop
    slower than the stream would fall further and further behind.
    """

    def __init__(self, spec: str, label: str) -> None:
        self.spec = spec
        self.is_file = not spec.isdigit() and "://" not in spec
        self.is_stream = "://" in spec
        if spec.isdigit():
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            self.cap = cv2.VideoCapture(int(spec), backend)
        else:
            self.cap = cv2.VideoCapture(spec)
        if not self.cap.isOpened():
            hint = "; use --source <video file> instead" if spec.isdigit() else ""
            raise SystemExit(f"cannot open {label} camera {spec}{hint}")
        self._cond = threading.Condition()
        self._latest: tuple[np.ndarray, float] | None = None
        self._stop = threading.Event()
        if self.is_stream:
            threading.Thread(target=self._grab, name=f"grab-{label}", daemon=True).start()

    def read(self) -> tuple[bool, np.ndarray | None, float]:
        """(ok, frame, t). t is the video position for files, time.monotonic() otherwise."""
        if self.is_stream:
            with self._cond:
                if self._latest is None:
                    self._cond.wait(timeout=1.0)
                if self._latest is None:
                    return False, None, time.monotonic()
                (frame, t), self._latest = self._latest, None
                return True, frame, t
        ok, frame = self.cap.read()
        t = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 if self.is_file else time.monotonic()
        return ok, frame, t

    def _grab(self) -> None:
        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok:
                log.warning("stream %s read failed, retrying", self.spec)
                time.sleep(0.5)
                continue
            with self._cond:
                self._latest = (frame, time.monotonic())
                self._cond.notify()

    def release(self) -> None:
        self._stop.set()
        self.cap.release()


def open_capture(camera: str, source: str | None) -> tuple[cv2.VideoCapture, bool]:
    """Returns (capture, is_file) for calibration. A file source is used when given."""
    spec = source or camera
    if spec.isdigit():
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(int(spec), backend)
    else:
        cap = cv2.VideoCapture(spec)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {spec}; use --source <video file> instead")
    return cap, bool(source)


class FpsMeter:
    """Frames per second over the last FPS_WINDOW_S; samples for the summary after the warm-up."""

    def __init__(self) -> None:
        self.times: deque[float] = deque()
        self.fps = 0.0
        self.samples: list[float] = []
        self.warm_start: float | None = None  # set by the caller at the first processed frame

    def tick(self, now: float) -> None:
        self.times.append(now)
        while self.times and self.times[0] < now - FPS_WINDOW_S:
            self.times.popleft()
        if len(self.times) > 1:
            self.fps = (len(self.times) - 1) / (self.times[-1] - self.times[0])
        if self.warm and len(self.times) > 1:
            self.samples.append(self.fps)

    @property
    def warm(self) -> bool:
        return self.warm_start is not None and time.monotonic() - self.warm_start > WARMUP_S

    def full_window(self) -> bool:
        return self.warm_start is not None and (
            time.monotonic() - self.warm_start > WARMUP_S + FPS_WINDOW_S
        )

    def mean(self) -> float:
        return statistics.fmean(self.samples) if self.samples else 0.0


# --- pipelines ----------------------------------------------------------------------------------
class ProximityPipeline:
    window = "vision - proximity"

    def __init__(
        self,
        args: argparse.Namespace,
        detector: Detector,
        sink: EventSink,
        fatigue_high: Callable[[], bool],
    ) -> None:
        self.args = args
        self.detector = detector
        self.sink = sink
        self.fatigue_high = fatigue_high
        self.tracker = ProximityTracker()
        self.calibration = Calibration.load(args.calibration)
        if not self.calibration.calibrated:
            log.warning(
                "no %s, using an assumed focal length; distances are rough", args.calibration
            )
        self.meter = FpsMeter()
        self.frames = self.processed = self.events = 0
        self.readings: list[TrackReading] = []

    def widened(self) -> bool:
        return self.args.low_visibility or self.fatigue_high()

    def step(self, frame: np.ndarray, t: float) -> np.ndarray | None:
        self.frames += 1
        self.meter.tick(time.monotonic())
        d = self.detector
        if d.imgsz > IMGSZ_SLOW and self.meter.full_window() and self.meter.fps < MIN_FPS:
            log.warning(
                "fps %.1f < %.0f, dropping to imgsz %d", self.meter.fps, MIN_FPS, IMGSZ_SLOW
            )
            d.imgsz = IMGSZ_SLOW

        if self.frames % 2 == 1:  # every 2nd frame
            self.processed += 1
            focal = self.calibration.focal_for(frame.shape[0])
            self.readings = self.tracker.update(d.track(frame), t, focal, self.widened())
            if self.meter.warm_start is None:
                self.meter.warm_start = time.monotonic()
            for r in self.readings:
                if r.post:
                    a = self.args
                    self.sink.send(build_event(r, a.machine_id, a.operator_id, a.sector))
                    self.events += 1

        if self.args.no_display:
            return None
        return draw(
            frame,
            self.readings,
            self.meter.fps,
            self.args.sector,
            d.imgsz,
            self.widened(),
            self.calibration.calibrated,
        )

    def summary(self) -> str:
        return (
            f"proximity: {self.frames} frames read, {self.processed} processed, "
            f"mean fps {self.meter.mean():.1f} (after {WARMUP_S:.0f} s warm-up), "
            f"final imgsz {self.detector.imgsz}, {self.events} events"
        )


class FatiguePipeline:
    window = "vision - fatigue"

    def __init__(self, args: argparse.Namespace, detector: Detector, sink: EventSink) -> None:
        self.args = args
        self.detector = detector
        self.sink = sink
        self.face_mesh = FaceMesh()
        shift = resolve_shift(args.shift_start, args.shift_type, args.machine_id, args.shift_id)
        log.info(
            "fatigue: shift %s (%s), %.1f h in",
            shift.shift_id,
            shift.shift_type,
            shift.hours_into(),
        )
        if shift.assumed:
            log.warning(
                "outside the standard %s shift and no --shift-start: assuming it starts now",
                shift.shift_type,
            )
        if args.operator_id is None:
            log.warning("no --operator-id; fatigue_log rows need one, the backend will reject them")
        self.monitor = FatigueMonitor(args.machine_id, args.operator_id, shift)
        self.meter = FpsMeter()
        self.frames = self.face_frames = 0
        self.events: Counter[str] = Counter()
        self.reading: FatigueReading | None = None

    def is_high(self) -> bool:
        return self.monitor.high

    def step(self, frame: np.ndarray, t: float) -> np.ndarray | None:
        self.frames += 1
        self.meter.tick(time.monotonic())
        landmarks = self.face_mesh.landmarks(frame, t)
        self.face_frames += landmarks is not None
        checked = self.frames % PHONE_EVERY_N_FRAMES == 1  # every 5th frame
        conf = self.detector.phone_conf(frame, PHONE_CONF, PHONE_CLASS_ID) if checked else None
        h, w = frame.shape[:2]
        self.reading = self.monitor.update(
            t,
            landmarks,
            w,
            h,
            machine_moving=self.args.machine_moving,
            phone_conf=conf,
            phone_checked=checked,
        )
        if self.meter.warm_start is None:
            self.meter.warm_start = time.monotonic()
        for event in self.reading.events:
            self.sink.send(event)
            key = event["type"]
            if key == "fatigue_high":
                key += f"/{event['severity']}"
            self.events[key] += 1

        if self.args.no_display:
            return None
        return draw_fatigue(
            frame, self.reading, landmarks, self.meter.fps, self.args.machine_moving
        )

    def summary(self) -> str:
        cal = self.monitor.calibration
        how = "default (calibration fallback)" if cal.fallback else "calibrated"
        if not cal.done:
            how = "default (calibration not finished)"
        face = self.face_frames / self.frames if self.frames else 0.0
        events = ", ".join(f"{k} {v}" for k, v in sorted(self.events.items())) or "none"
        return (
            f"fatigue: {self.frames} frames, face in {face:.0%}, "
            f"mean fps {self.meter.mean():.1f} (after {WARMUP_S:.0f} s warm-up), "
            f"EAR threshold {cal.threshold:.3f} {how}, events: {events}"
        )

    def close(self) -> None:
        self.face_mesh.close()


# --- run ----------------------------------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    prox_spec = args.source or args.camera
    cab_spec = args.cab_camera or prox_spec
    if args.mode == "both" and args.cab_camera is None:
        log.warning("--mode both without --cab-camera: both pipelines use %s", prox_spec)

    detector = Detector(args.weights)  # one model: proximity tracking and phone detection
    sink = EventSink(args.backend, dry_run=args.dry_run, token=args.token)
    cameras: dict[str, Camera] = {}
    pipelines: list[tuple[str, ProximityPipeline | FatiguePipeline]] = []
    fatigue: FatiguePipeline | None = None
    if args.mode in ("fatigue", "both"):
        cameras[cab_spec] = Camera(cab_spec, "cab")
        fatigue = FatiguePipeline(args, detector, sink)
    if args.mode in ("proximity", "both"):
        if prox_spec not in cameras:
            cameras[prox_spec] = Camera(prox_spec, "proximity")
        poll = FatigueStatus(args.backend, args.operator_id)  # stub until the backend has it
        high = fatigue.is_high if fatigue else poll.is_high
        pipelines.append((prox_spec, ProximityPipeline(args, detector, sink, high)))
    if fatigue:
        pipelines.append((cab_spec, fatigue))

    start = time.monotonic()
    try:
        running = True
        while running:
            frames = {}
            for spec, cam in cameras.items():
                ok, frame, t = cam.read()
                if not ok:
                    if cam.is_file:
                        log.info("end of video %s", spec)
                        running = False
                        break
                    log.warning("camera %s read failed, retrying", spec)
                    time.sleep(0.1)
                    continue
                frames[spec] = (frame, t)
            if not running:
                break
            for spec, pipe in pipelines:
                if spec in frames:
                    img = pipe.step(*frames[spec])
                    if img is not None:
                        cv2.imshow(pipe.window, img)
            if not args.no_display and cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if args.duration and time.monotonic() - start >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        for cam in cameras.values():
            cam.release()
        cv2.destroyAllWindows()
        if fatigue:
            fatigue.close()
        sink.close()

    total = time.monotonic() - start
    for _, pipe in pipelines:
        print(pipe.summary(), file=sys.stderr)
    print(f"summary: {total:.1f} s, sink sent {sink.sent} dropped {sink.dropped}", file=sys.stderr)
    return 0


def run_calibrate(args: argparse.Namespace) -> int:
    """Ask one person to stand at --distance metres, measure the median box height, save f."""
    cap, _ = open_capture(args.camera, args.source)
    detector = Detector(args.weights)
    countdown_s, collect_s, need = 5.0, 20.0, 30
    heights: list[float] = []
    frame_h = 0
    start = time.monotonic()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            frame_h = frame.shape[0]
            elapsed = time.monotonic() - start
            msg = f"Stand {args.distance:g} m from the camera, full body in view"
            if elapsed < countdown_s:
                msg += f" - starting in {countdown_s - elapsed:.0f}"
            else:
                people = detector.detect_people(frame)
                if people:
                    heights.append(max(p.height_px for p in people))
                msg += f" - measuring {len(heights)}/{need}"
                if len(heights) >= need or elapsed > countdown_s + collect_s:
                    break
            if not args.no_display:
                cv2.putText(frame, msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.imshow("vision - calibrate", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    return 1
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if len(heights) < 5:
        print(f"calibration failed: person seen in only {len(heights)} frames", file=sys.stderr)
        return 1
    data = save_calibration(
        statistics.median(heights), args.distance, frame_h, len(heights), args.calibration
    )
    print(f"saved {args.calibration}: {data}", file=sys.stderr)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Vision service: proximity / blindspot (models.md §6) and fatigue (§5)"
    )
    p.add_argument("command", nargs="?", choices=["run", "calibrate"], default="run")
    p.add_argument("--mode", choices=["proximity", "fatigue", "both"], default="proximity")
    p.add_argument("--camera", default="0", help="proximity camera: webcam index or IP camera URL")
    p.add_argument("--source", help="video file for the proximity camera (demo fallback)")
    p.add_argument(
        "--cab-camera",
        help="fatigue camera: webcam index, IP camera URL or video file (default: --camera)",
    )
    p.add_argument("--sector", choices=SECTORS, default="front")
    p.add_argument("--machine-id", default="M01")
    p.add_argument("--operator-id", default=None)
    p.add_argument("--backend", default="http://localhost:8000")
    p.add_argument(
        "--token",
        default=os.environ.get("VISION_API_TOKEN"),
        help="Bearer token for the backend (default $VISION_API_TOKEN)",
    )
    p.add_argument("--dry-run", action="store_true", help="print events instead of posting")
    p.add_argument("--low-visibility", action="store_true", help="widen both zones by 2 m")
    # fatigue inputs that will come from the backend later
    p.add_argument(
        "--shift-start",
        help="ISO datetime or HH:MM, site time (default: 06:00 day / 18:00 night, most recent)",
    )
    p.add_argument("--shift-type", choices=["day", "night"], default="day")
    p.add_argument("--shift-id", help="default SH-<start date>-<machine>-<D|N>")
    p.add_argument(
        "--machine-moving",
        action="store_true",
        help="stub for telemetry: treat the machine as moving (eyes closed > 2 s is then critical)",
    )
    p.add_argument("--distance", type=float, default=3.0, help="calibrate: metres to the person")
    p.add_argument("--calibration", type=Path, default=CALIBRATION_PATH)
    p.add_argument("--weights", default="yolo11n.pt")
    p.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = never)")
    p.add_argument("--no-display", action="store_true", help="no overlay window")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per event is noise
    args = parse_args(argv)
    if args.command == "calibrate":
        return run_calibrate(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
