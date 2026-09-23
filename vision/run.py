"""Vision service entry point.

    python vision/run.py --camera 0 --sector rear --machine-id M04 --operator-id OP03 --dry-run
    python vision/run.py --source demo.mp4 --backend http://localhost:8000
    python vision/run.py calibrate --distance 3

Proximity events (JSON lines in --dry-run) go to stdout; logs and the run summary go to stderr.
Press q in the overlay window or Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import time
from collections import deque
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))

from events import EventSink  # noqa: E402
from overlay import draw  # noqa: E402
from proximity import (  # noqa: E402
    CALIBRATION_PATH,
    IMGSZ_SLOW,
    MIN_FPS,
    SECTORS,
    Calibration,
    Detector,
    FatigueStatus,
    ProximityTracker,
    build_event,
    save_calibration,
)

log = logging.getLogger("vision")
WINDOW = "vision - proximity"
FPS_WINDOW_S = 3.0  # fps averaged over this many seconds for the 640 -> 480 decision
WARMUP_S = 3.0  # first inferences are slow (torch start-up); don't judge fps before this


def open_capture(camera: int, source: str | None) -> tuple[cv2.VideoCapture, bool]:
    """Returns (capture, is_file). A file source is used when given (demo fallback)."""
    if source:
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise SystemExit(f"cannot open video file {source}")
        return cap, True
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(camera, backend)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {camera}; use --source <video file> instead")
    return cap, False


def run_proximity(args: argparse.Namespace) -> int:
    cap, is_file = open_capture(args.camera, args.source)
    detector = Detector(args.weights)
    tracker = ProximityTracker()
    sink = EventSink(args.backend, dry_run=args.dry_run, token=args.token)
    fatigue = FatigueStatus(args.backend, args.operator_id)
    calibration = Calibration.load(args.calibration)
    if not calibration.calibrated:
        log.warning("no %s, using an assumed focal length; distances are rough", args.calibration)

    start = time.monotonic()
    warm_start: float | None = None  # set at the first processed frame
    frame_times: deque[float] = deque()
    fps_samples: list[float] = []
    frames = processed = events = 0
    readings = []
    fps = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                if is_file:
                    log.info("end of video")
                    break
                log.warning("camera read failed, retrying")
                time.sleep(0.1)
                continue
            now = time.monotonic()
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 if is_file else now
            frames += 1

            frame_times.append(now)
            while frame_times and frame_times[0] < now - FPS_WINDOW_S:
                frame_times.popleft()
            if len(frame_times) > 1:
                fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
            elapsed = now - start
            warm = warm_start is not None and now - warm_start > WARMUP_S
            if warm and len(frame_times) > 1:
                fps_samples.append(fps)
                full_window = now - warm_start > WARMUP_S + FPS_WINDOW_S
                if detector.imgsz > IMGSZ_SLOW and full_window and fps < MIN_FPS:
                    log.warning("fps %.1f < %.0f, dropping to imgsz %d", fps, MIN_FPS, IMGSZ_SLOW)
                    detector.imgsz = IMGSZ_SLOW

            if frames % 2 == 1:  # every 2nd frame
                processed += 1
                widened = args.low_visibility or fatigue.is_high()
                focal = calibration.focal_for(frame.shape[0])
                readings = tracker.update(detector.track(frame), t, focal, widened)
                if warm_start is None:
                    warm_start = time.monotonic()
                for r in readings:
                    if r.post:
                        sink.send(build_event(r, args.machine_id, args.operator_id, args.sector))
                        events += 1

            if not args.no_display:
                widened = args.low_visibility or fatigue.is_high()
                cv2.imshow(
                    WINDOW,
                    draw(
                        frame,
                        readings,
                        fps,
                        args.sector,
                        detector.imgsz,
                        widened,
                        calibration.calibrated,
                    ),
                )
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if args.duration and elapsed >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
        sink.close()

    total = time.monotonic() - start
    mean_fps = statistics.fmean(fps_samples) if fps_samples else 0.0
    print(
        f"summary: {total:.1f} s, {frames} frames read, {processed} processed, "
        f"mean fps {mean_fps:.1f} (after {WARMUP_S:.0f} s warm-up), final imgsz {detector.imgsz}, "
        f"{events} events, sink sent {sink.sent} dropped {sink.dropped}",
        file=sys.stderr,
    )
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
    p = argparse.ArgumentParser(description="Proximity / blindspot vision service (models.md §6)")
    p.add_argument("mode", nargs="?", choices=["proximity", "calibrate"], default="proximity")
    p.add_argument("--camera", type=int, default=0, help="webcam index")
    p.add_argument("--source", help="video file instead of the webcam (demo fallback)")
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
    if args.mode == "calibrate":
        return run_calibrate(args)
    return run_proximity(args)


if __name__ == "__main__":
    raise SystemExit(main())
