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

import json
from datetime import timezone, datetime
from edge_outbox import EdgeOutbox
from margin_core import MarginCounter

from hailo_apps.hailo_app_python.core.common.buffer_utils import (
    get_caps_from_pad,
    get_numpy_from_buffer,
)
from hailo_apps.hailo_app_python.core.gstreamer.gstreamer_app import app_callback_class
from hailo_apps.hailo_app_python.apps.detection.detection_pipeline import (GStreamerDetectionApp,
)

WATCHDOG_COUNT_FILE = "/tmp/hailo_edge_watchdog_count"

STATE_DIR = Path("/home/ee96/Parking-Lot-Occupancy-Tracker/edge/state")
LAST_STATE = STATE_DIR / "last.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

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
        self.tracker = SimpleTracker(max_dist=90, max_age=60)
        
        # Bootstrap state for initial auto-occupancy
        self.start_ts = time.time()
        self.bootstrap_secs = getattr(args, "bootstrap_secs", 0.0)
        self.bootstrap_offset = getattr(args, "bootstrap_offset", 0)
        self.bootstrap_ids = set()  # unique track ids seen in scan ROI
        # If bootstrap_secs <= 0, we skip bootstrapping
        self.bootstrap_done = (self.bootstrap_secs <= 0.0)


        # Watchdog: last time we got a good frame
        self.last_frame_ts = time.time()

        # Start watchdog thread
        t = threading.Thread(target=self._watchdog_loop, daemon=True)
        t.start()
        
        # Outbox: local SQLite buffer + background sync to backend
        self.outbox = EdgeOutbox()

        # Define the hook that MarginCounter will call on each +1/-1 event
        def _on_occ(ts_utc, occupancy_after, max_capacity):
            # Always normalize to UTC ISO string
            ts_iso = ts_utc.astimezone(timezone.utc).isoformat()
            # 1) Persist into the outbox / DB
            try:
                self.outbox.insert_detection(ts_iso, occupancy_after, max_capacity)
            except Exception as e:
                # Don't kill the pipeline on DB errors
                print(f"[OUTBOX ERROR] {e}", file=sys.stderr)

            # 2) Persist "last known occupancy" for crash/reboot resume
            try:
                ensure_dir(STATE_DIR)
                LAST_STATE.write_text(
                    json.dumps(
                        {
                            "ts": ts_iso,
                            "occupancy": int(occupancy_after),
                        }
                    ),
                    encoding="utf-8",
                )
            except Exception as e:
                # Just log; don't crash the pipeline
                print(
                    json.dumps(
                        {"ts": now_iso(), "persist_error": str(e)}
                    ),
                    file=sys.stderr,
                )

        self.on_occupancy_update = _on_occ

        # Fire up background sync thread (no-op if EDGE_INGEST_URL unset)
        self.outbox.start_background_sync()

    def _watchdog_loop(self):
        timeout = 20.0  # seconds with no new frames before we say "frozen"
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
    
    # FPS ~10
    user_data.frame_index = getattr(user_data, "frame_index", 0) + 1
    if user_data.frame_index % 2 != 0:
        # Skip processing every other frame
        return Gst.PadProbeReturn.OK

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
    
    # Optional horizontal flip so User Frame is unmirrored
    if user_data.args.flip_user_frame:
        frame_bgr = cv2.flip(frame_bgr, 1)


    # Mark that we successfully got a frame
    user_data.last_frame_ts = time.time()

    # Lazily create MarginCounter when we know frame shape
    if user_data.counter is None:
        user_data.counter = MarginCounter(user_data.args, frame_bgr.shape)

        # Wire occupancy callback → SQLite outbox
        if getattr(user_data, "on_occupancy_update", None):
            user_data.counter.on_occupancy_update = user_data.on_occupancy_update

    t_now = time.time()

    # Get detections from Hailo ROI
    roi = hailo.get_roi_from_buffer(buffer)
    hailo_dets = roi.get_objects_typed(hailo.HAILO_DETECTION)

    # Collect boxes + confidences + IDs for vehicle-like classes
    raw_boxes = []
    raw_confs = []
    raw_ids   = []   # Hailo tracker IDs

    for det in hailo_dets:
        label = det.get_label()
        conf = det.get_confidence()
        bbox = det.get_bbox()

        # Only keep cars from the beginning
        if label != "car":
            continue

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

        raw_boxes.append((x1, y1, x2, y2))
        raw_confs.append(float(conf))

        # 🔹 Get Hailo's UNIQUE_ID (track ID) for this car
        uid_objs = det.get_objects_typed(hailo.HAILO_UNIQUE_ID)
        if uid_objs and len(uid_objs) > 0:
            tid = uid_objs[0].get_id()
        else:
            tid = -1  # tracker didn't tag this detection
        raw_ids.append(int(tid))

    # ---------------------------------------------
    # ROI filter: keep only boxes in top 30% of frame
    # ---------------------------------------------
    # ROI_FRAC = 0.30
    # roi_bottom = int(ROI_FRAC * height)

    # roi_boxes = []
    # roi_confs = []
    # for (x1, y1, x2, y2), conf in zip(raw_boxes, raw_confs):
        # cy = 0.5 * (y1 + y2)
        # if cy <= roi_bottom:
            # roi_boxes.append((x1, y1, x2, y2))
            # roi_confs.append(conf)

    # raw_boxes = roi_boxes
    # raw_confs = roi_confs

    # DEBUG: how many boxes did we actually keep this frame?
    # print("raw_boxes in this frame:", len(raw_boxes))

    # If we flipped the image, flip the boxes so coords still align
    if user_data.args.flip_user_frame and raw_boxes:
        flipped_boxes = []
        for (x1, y1, x2, y2) in raw_boxes:
            fx1 = width - x2
            fx2 = width - x1
            flipped_boxes.append((fx1, y1, fx2, y2))
        raw_boxes = flipped_boxes

    # Prefer Hailo tracker IDs; fallback to SimpleTracker if none
    if raw_ids and any(tid >= 0 for tid in raw_ids):
        track_ids = raw_ids
        #print("USING HAILO TRACKER IDs:", track_ids)
    else:
        track_ids = user_data.tracker.update(raw_boxes)
        #print("USING SIMPLETRACKER IDs:", track_ids)
        
    #if track_ids:
        #print("Track IDs this frame:", track_ids)

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

    h, w = frame_out.shape[:2] if 'frame_out' in locals() else frame_bgr.shape[:2]

    elapsed = t_now - user_data.start_ts

    # -------------------------------------------------------------
    # Bootstrap: auto-estimate initial occupancy for first N seconds
    # -------------------------------------------------------------
    if not user_data.bootstrap_done:
        # Determine scan ROI
        sxmin = max(0, user_data.args.scan_xmin)
        sxmax = user_data.args.scan_xmax if user_data.args.scan_xmax > 0 else w
        symin = max(0, user_data.args.scan_ymin)
        symax = user_data.args.scan_ymax if user_data.args.scan_ymax > 0 else h

        for det in detections:
            x1, y1, x2, y2 = det["xyxy"]
            tid = det["id"]
            cx = 0.5 * (x1 + x2)
            cy = 0.5 * (y1 + y2)
            if sxmin <= cx <= sxmax and symin <= cy <= symax:
                user_data.bootstrap_ids.add(tid)

        if elapsed >= user_data.bootstrap_secs:
            auto_count = len(user_data.bootstrap_ids)
            seed = auto_count + user_data.bootstrap_offset
            user_data.args.seed_occupancy = seed
            print(f"[BOOTSTRAP] auto_count={auto_count}, offset={user_data.bootstrap_offset}, seed_occupancy={seed}")
            user_data.counter = MarginCounter(user_data.args, frame_bgr.shape)
            # re-attach occupancy hook
            if getattr(user_data, "on_occupancy_update", None):
                user_data.counter.on_occupancy_update = user_data.on_occupancy_update
            user_data.bootstrap_done = True


    # If bootstrap is disabled or done and we somehow still don't have a counter, create it now
    if user_data.bootstrap_done and user_data.counter is None:
        user_data.counter = MarginCounter(user_data.args, frame_bgr.shape)
        if getattr(user_data, "on_occupancy_update", None):
            user_data.counter.on_occupancy_update = user_data.on_occupancy_update


    # If we are still in the bootstrap window (no counter yet), just display status text
    if user_data.counter is None:
        frame_out = frame_bgr.copy()
        cv2.putText(
            frame_out,
            f"Bootstrapping occupancy... {elapsed:.1f}/{user_data.bootstrap_secs:.1f}s",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame_out,
            f"Scan count so far: {len(user_data.bootstrap_ids)}",
            (20, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        user_data.set_frame(frame_out)
        return Gst.PadProbeReturn.OK


    # Run shared margin logic (updates occupancy, HUD, etc.)
    frame_out = user_data.counter.process(frame_bgr, detections, t_now)

    # EXTRA: always draw detections on the User Frame (debug visual)
    # for det in detections:
        # x1, y1, x2, y2 = det["xyxy"]
        # tid = det["id"]
        # # green boxes on user frame
        # cv2.rectangle(
            # frame_out,
            # (int(x1), int(y1)),
            # (int(x2), int(y2)),
            # (0, 255, 0),
            # 2,
        # )
        # cv2.putText(
            # frame_out,
            # f"id:{tid}",
            # (int(x1), max(12, int(y1) - 4)),
            # cv2.FONT_HERSHEY_SIMPLEX,
            # 0.5,
            # (0, 255, 0),
            # 1,
            # cv2.LINE_AA,
        # )

    # ------------------------------------------------------------------
    # Draw gate masks directly here (Hailo overlay), independent of margin_core
    # ------------------------------------------------------------------
    args = user_data.args
    h, w = frame_out.shape[:2]

    # ------------------------------------------------------------------
    # Flip output horizontally (mirror) so orientation matches CPU script
    # ------------------------------------------------------------------
    # frame_out = cv2.flip(frame_out, 1)

    # # Draw ROI line (top 30%) for visualization
    # ROI_FRAC = 0.30
    # roi_bottom = int(ROI_FRAC * frame_out.shape[0])
    # cv2.line(
        # frame_out,
        # (0, roi_bottom),
        # (frame_out.shape[1], roi_bottom),
        # (0, 0, 255),
        # 2,
    # )
    # cv2.putText(
        # frame_out,
        # "ROI (top 30%)",
        # (10, max(20, roi_bottom - 10)),
        # cv2.FONT_HERSHEY_SIMPLEX,
        # 0.6,
        # (0, 0, 255),
        # 2,
    # )

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

    # Flipping view horizontally
    parser.add_argument(
        "--flip_user_frame",
        action="store_true",
        help="Flip frame & detections horizontally before MarginCounter so text is not mirrored.",
    )
    
    # Initial auto-scan of parked cars
    parser.add_argument(
        "--bootstrap_secs",
        type=float,
        default=0.0,
        help="If >0, first N seconds are used to auto-count parked cars for initial occupancy.",
    )
    parser.add_argument(
        "--bootstrap_offset",
        type=int,
        default=0,
        help="Extra cars to add on top of the auto-count.",
    )
    parser.add_argument(
        "--scan_xmin",
        type=int,
        default=0,
        help="Scan ROI left x (pixels).",
    )
    parser.add_argument(
        "--scan_xmax",
        type=int,
        default=-1,
        help="Scan ROI right x (pixels). -1 = full width.",
    )
    parser.add_argument(
        "--scan_ymin",
        type=int,
        default=0,
        help="Scan ROI top y (pixels).",
    )
    parser.add_argument(
        "--scan_ymax",
        type=int,
        default=-1,
        help="Scan ROI bottom y (pixels). -1 = full height.",
    )


    # Gate geometry (copy from your working margin_counter.py defaults)
    parser.add_argument("--g1_A", type=int, default=30)
    parser.add_argument("--g1_B", type=int, default=52)
    parser.add_argument("--g1_xmin", type=int, default=292)
    parser.add_argument("--g1_xmax", type=int, default=398)

    parser.add_argument("--g2_A", type=int, default=45)
    parser.add_argument("--g2_B", type=int, default=70)
    parser.add_argument("--g2_xmin", type=int, default=940)
    parser.add_argument("--g2_xmax", type=int, default=1085)

    parser.add_argument("--seed_occupancy", type=int, default=0)

    # Margin / motion thresholds (same semantics as in margin_core)
    parser.add_argument("--yref", choices=["center", "top", "topq", "bottom"], default="topq")
    parser.add_argument("--min_speed", type=float, default=1.0)
    parser.add_argument("--max_speed_px_s", type=float, default=0.0,
    help="If >0, clamp |vy| to this many px/s before checks; 0 = disable clamp.")
    parser.add_argument("--cooldown_s", type=float, default=0.0)
    parser.add_argument("--debounce_frames", type=int, default=2)
    parser.add_argument("--hyst_px", type=int, default=2)
    parser.add_argument("--min_track_age", type=int, default=2)
    parser.add_argument("--invert_dir", action="store_true")
    parser.add_argument("--implied_seq", action="store_true")
    parser.add_argument("--min_box_w", type=int, default=7)
    parser.add_argument("--min_box_h", type=int, default=7)
    parser.add_argument("--max_ar", type=float, default=5.0)
    parser.add_argument("--max_capacity", type=int, default=73)
    parser.add_argument("--debug_hits", action="store_true")

    # Control gate mask opacity on Hailo output
    parser.add_argument("--mask_alpha", type=float, default=0.0) # 0.25

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
