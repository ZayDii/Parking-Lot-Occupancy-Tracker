from pathlib import Path
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # GLib not needed right now

import os
import sys
import time
import argparse

import cv2
import hailo
import threading
import signal

import math
from collections import defaultdict, deque

from margin_core import MarginCounter

from hailo_apps.hailo_app_python.core.common.buffer_utils import (
    get_caps_from_pad,
    get_numpy_from_buffer,
)
from hailo_apps.hailo_app_python.core.gstreamer.gstreamer_app import app_callback_class
from hailo_apps.hailo_app_python.apps.detection.detection_pipeline import (
    GStreamerDetectionApp,
)

WATCHDOG_COUNT_FILE = "/tmp/hailo_edge_watchdog_count"

class SimpleTracker:
    """
    Very small centroid-based tracker.
    Keeps IDs stable across frames as long as objects move smoothly.
    Good enough for the gate-crossing logic.
    """
    def __init__(self, max_dist=80, max_age=20):
        self.next_id = 0
        self.tracks = {}   # id -> {"cx":..., "cy":..., "age":...}
        self.max_dist = max_dist
        self.max_age = max_age

    def update(self, boxes):
        """
        boxes: list of (x1, y1, x2, y2)
        returns: list of track_ids, same length as boxes
        """
        if not boxes:
            # Age all tracks, drop old ones
            for tid in list(self.tracks.keys()):
                self.tracks[tid]["age"] += 1
                if self.tracks[tid]["age"] > self.max_age:
                    del self.tracks[tid]
            return []

        centers = [((x1 + x2) * 0.5, (y1 + y2) * 0.5) for (x1, y1, x2, y2) in boxes]

        # Age existing tracks
        for tid in list(self.tracks.keys()):
            self.tracks[tid]["age"] += 1

        assigned_tracks = {}
        used_tids = set()

        # Greedy nearest-neighbour assignment
        for i, (cx, cy) in enumerate(centers):
            best_tid = None
            best_d = self.max_dist
            for tid, trk in self.tracks.items():
                if tid in used_tids:
                    continue
                d = math.hypot(cx - trk["cx"], cy - trk["cy"])
                if d < best_d:
                    best_d = d
                    best_tid = tid

            if best_tid is None:
                # create new track
                best_tid = self.next_id
                self.next_id += 1

            self.tracks[best_tid] = {"cx": cx, "cy": cy, "age": 0}
            used_tids.add(best_tid)
            assigned_tracks[i] = best_tid

        # Drop stale tracks
        self.tracks = {
            tid: trk
            for tid, trk in self.tracks.items()
            if trk["age"] <= self.max_age
        }

        return [assigned_tracks.get(i, -1) for i in range(len(boxes))]



# -----------------------------------------------------------------------------------
# User callback data
# -----------------------------------------------------------------------------------
class user_app_callback_class(app_callback_class):
    def __init__(self, args):
        super().__init__()
        self.use_frame = True
        self.args = args
        self.counter = None
        self.tracker = SimpleTracker(max_dist=80, max_age=20)

        # Watchdog: last time we got a good frame
        self.last_frame_ts = time.time()

        # Start watchdog thread
        t = threading.Thread(target=self._watchdog_loop, daemon=True)
        t.start()

    def _watchdog_loop(self):
        timeout = 10.0  # seconds with no new frames before we say "frozen"
        while True:
            time.sleep(1.0)
            since = time.time() - self.last_frame_ts
            if since > timeout:
                # Read current freeze count from file
                try:
                    with open(WATCHDOG_COUNT_FILE, "r") as f:
                        count = int(f.read().strip() or "0")
                except Exception:
                    count = 0

                count += 1
                try:
                    with open(WATCHDOG_COUNT_FILE, "w") as f:
                        f.write(str(count))
                except Exception:
                    pass

                print(f"Watchdog: no frames for {since:.1f}s (freeze #{count})")

                if count >= 3:
                    # 3rd freeze in this boot: tell outer loop to reboot
                    print("Watchdog: hit 3 freezes, exiting with code 200 for reboot...")
                    os._exit(200)  # exit immediately with special code
                else:
                    # 1st or 2nd freeze: simulate your double Ctrl+C and let wrapper restart
                    pid = os.getpid()
                    print("Watchdog: sending SIGINT x2 to self")
                    os.kill(pid, signal.SIGINT)
                    time.sleep(1.5)
                    os.kill(pid, signal.SIGINT)
                    break


                
# -----------------------------------------------------------------------------------
# Callback function
# -----------------------------------------------------------------------------------
def app_callback(pad, info, user_data):
    # Get the GstBuffer from the probe info
    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK
        
    # mark that we saw a frame
    user_data.last_frame_ts = time.time()

    # Increment internal frame counter
    user_data.increment()

    # Get caps and frame metadata
    fmt, width, height = get_caps_from_pad(pad)
    if fmt is None or width is None or height is None:
        return Gst.PadProbeReturn.OK

    # Get video frame (RGB) and convert to BGR for OpenCV / MarginCounter
    frame_rgb = None
    if user_data.use_frame:
        frame_rgb = get_numpy_from_buffer(buffer, fmt, width, height)

    if frame_rgb is None:
        return Gst.PadProbeReturn.OK

    frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    # Mark that we successfully got a frame
    user_data.last_frame_ts = time.time()

    # Lazily create MarginCounter when we know frame shape
    if user_data.counter is None:
        user_data.counter = MarginCounter(user_data.args, frame_bgr.shape)


    t_now = time.time()

    # Get detections from Hailo ROI
    roi = hailo.get_roi_from_buffer(buffer)
    hailo_dets = roi.get_objects_typed(hailo.HAILO_DETECTION)

    # Collect boxes + confidences for vehicle-like classes
    raw_boxes = []
    raw_confs = []

    for det in hailo_dets:
        label = det.get_label()
        conf = det.get_confidence()
        bbox = det.get_bbox()

        # Hailo bbox is normalized 0–1 → convert to pixel coords
        x1n, y1n, x2n, y2n = (
            bbox.xmin(),
            bbox.ymin(),
            bbox.xmax(),
            bbox.ymax(),
        )
        x1 = float(x1n * width)
        y1 = float(y1n * height)
        x2 = float(x2n * width)
        y2 = float(y2n * height)

        # Only keep vehicles (adjust labels if needed)
        if label not in ("car", "truck", "bus"):
            continue

        raw_boxes.append((x1, y1, x2, y2))
        raw_confs.append(float(conf))


    # DEBUG: how many boxes did we actually keep this frame?
    # print("raw_boxes in this frame:", len(raw_boxes))

    # Assign persistent IDs using our SimpleTracker
    track_ids = user_data.tracker.update(raw_boxes)

    # DEBUG: show the IDs we assigned
    # print("assigned track_ids:", track_ids)

    detections = []
    for (x1, y1, x2, y2), conf, tid in zip(raw_boxes, raw_confs, track_ids):
        if tid < 0:
            continue

        # Map all vehicles to class 2 ("car") for MarginCounter logic
        detections.append(
            {
                "id": int(tid),
                "cls": 2,
                "conf": conf,
                "xyxy": (x1, y1, x2, y2),
            }
        )

    # DEBUG: how many detections will MarginCounter see?
    # print("detections passed to MarginCounter:", len(detections))


    # Run shared margin logic (updates occupancy, HUD, etc.)
    frame_out = user_data.counter.process(frame_bgr, detections, t_now)

    # EXTRA: always draw detections on the User Frame (debug visual)
    for det in detections:
        x1, y1, x2, y2 = det["xyxy"]
        tid = det["id"]
        # green boxes on user frame
        cv2.rectangle(
            frame_out,
            (int(x1), int(y1)),
            (int(x2), int(y2)),
            (0, 255, 0),
            2,
        )
        cv2.putText(
            frame_out,
            f"id:{tid}",
            (int(x1), max(12, int(y1) - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    # ------------------------------------------------------------------
    # Draw gate masks directly here (Hailo overlay), independent of margin_core
    # ------------------------------------------------------------------
    args = user_data.args
    h, w = frame_out.shape[:2]

    # ------------------------------------------------------------------
    # Flip output horizontally (mirror) so orientation matches CPU script
    # ------------------------------------------------------------------
    # frame_out = cv2.flip(frame_out, 1)

    # Give annotated frame back to app for display
    user_data.set_frame(frame_out)

    return Gst.PadProbeReturn.OK


# -----------------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Hailo-powered margin counter (wraps GStreamerDetectionApp)",
        add_help=False,  # we'll let Hailo show its own -h if needed
    )

    # Gate geometry (copy from your working margin_counter.py defaults)
    parser.add_argument("--g1_A", type=int, default=85)
    parser.add_argument("--g1_B", type=int, default=124)
    parser.add_argument("--g1_xmin", type=int, default=484)
    parser.add_argument("--g1_xmax", type=int, default=573)

    parser.add_argument("--g2_A", type=int, default=109)
    parser.add_argument("--g2_B", type=int, default=153)
    parser.add_argument("--g2_xmin", type=int, default=1464)
    parser.add_argument("--g2_xmax", type=int, default=1558)

    parser.add_argument("--seed_occupancy", type=int, default=0)

    # Margin / motion thresholds (same semantics as in margin_core)
    parser.add_argument("--yref", choices=["center", "top", "topq", "bottom"], default="topq")
    parser.add_argument("--min_speed", type=float, default=1.0)
    parser.add_argument("--cooldown_s", type=float, default=0.0)
    parser.add_argument("--debounce_frames", type=int, default=2)
    parser.add_argument("--hyst_px", type=int, default=2)
    parser.add_argument("--min_track_age", type=int, default=2)
    parser.add_argument("--invert_dir", action="store_true")
    parser.add_argument("--implied_seq", action="store_true")
    parser.add_argument("--min_box_w", type=int, default=12)
    parser.add_argument("--min_box_h", type=int, default=12)
    parser.add_argument("--max_ar", type=float, default=5.0)
    parser.add_argument("--max_capacity", type=int, default=73)
    parser.add_argument("--debug_hits", action="store_true")

    # Control gate mask opacity on Hailo output
    parser.add_argument("--mask_alpha", type=float, default=0.25)

    # IMPORTANT: parse only our args, leave the rest for Hailo
    args, remaining = parser.parse_known_args()

    # Inject --input rpi for Hailo if the user hasn't specified an input
    if "--input" not in remaining:
        remaining = ["--input", "rpi"] + remaining

    # Strip our custom args out of sys.argv so GStreamerDetectionApp
    # only sees its own flags.
    sys.argv = [sys.argv[0]] + remaining

    # Always show overlays in this script
    args.display = True
    args.show_labels = True

    # Point Hailo runtime at the examples .env (same as original detection.py)
    hailo_root = Path("/home/ee96/hailo-rpi5-examples")
    env_file = hailo_root / ".env"
    os.environ["HAILO_ENV_FILE"] = str(env_file)

    print("== Hailo margin counter starting ==")
    print("HAILO_ENV_FILE =", os.environ.get("HAILO_ENV_FILE"))
    print("Our args:", args)
    print("Remaining argv for GStreamerDetectionApp:", sys.argv)

    # Create callback data and app
    user_data = user_app_callback_class(args)
    app = GStreamerDetectionApp(app_callback, user_data)

    print("GStreamerDetectionApp created")

    try:
        print("Calling app.run() ...")
        app.run()
        print("app.run() returned (pipeline finished).")
    except Exception as e:
        print("ERROR in app.run():", repr(e))
        sys.exit(1)
