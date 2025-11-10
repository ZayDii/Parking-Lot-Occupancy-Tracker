#!/usr/bin/env python3
"""
WHAT CHANGED (compact):
- Strong defaults + labels on: imgsz up, IOU=0.6, conf ~0.18-0.25, target_fps cap.
- Tiny-box & aspect-ratio filters near gates to reduce false positives.
- Min track age (frames) before counting to avoid spawn-noise.
- Hysteresis (cross-line margin in px) to prevent jitter flips.
- Event snapshots saved to ./events on each +1/-1 for quick audits.
- Lightweight offline queue: append failed POST events to ./queue/events.jsonl and retry on each frame.
- Graceful resume only on crash: load last occupancy from ./state/last.json unless --fresh_start.
- JSON config loader: edge_config.json overrides CLI at startup (gates, thresholds, post_url, etc.).
- Two-gate A→B=+1, B→A=−1 preserved; per-gate X-windows and live hotkeys kept.

Hotkeys (when --display):
  g : toggle active gate (G1/G2)
  t : set active gate A to mouse Y
  b : set active gate B to mouse Y
  [ : set active gate xmin to mouse X
  ] : set active gate xmax to mouse X
  d : toggle labels
  q/ESC : quit
"""

import argparse, os, json, time, math, io
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

# ------------------------------
# Helpers & FS
# ------------------------------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

# simple, light text overlay
_mouse_xy = (0, 0)
def _mouse_cb(event, x, y, flags, param):
    global _mouse_xy
    if event == cv2.EVENT_MOUSEMOVE:
        _mouse_xy = (x, y)

def put(frame, txt, org, scale=0.55, color=(255,255,0), thick=2):
    cv2.putText(frame, txt, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)

class Gate:
    def __init__(self, name, h, w):
        self.name = name
        self.A = 0
        self.B = 0
        self.xmin, self.xmax = 0, w-1
        self.state = defaultdict(lambda: {
            "last_line": None,
            "y_prev": None,
            "t_prev": 0.0,
            "deb": deque(maxlen=2),
            "age": 0
        })
        self.last_event_at = defaultdict(lambda: 0.0)

    def top(self):  return min(self.A, self.B)
    def bot(self):  return max(self.A, self.B)

# ------------------------------
# Main
# ------------------------------
def main():
    ap = argparse.ArgumentParser()

    # IO / model
    ap.add_argument("--source", default=0)
    ap.add_argument("--model", default="yolov8s.pt")
    ap.add_argument("--classes", type=str, default="2,5,7")
    ap.add_argument("--conf", type=float, default=0.20)
    ap.add_argument("--iou", type=float, default=0.60)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--max_det", type=int, default=200)
    ap.add_argument("--tracker", default="bytetrack.yaml")

    # Runtime
    ap.add_argument("--hw", choices=["cpu","hailo"], default="cpu")
    ap.add_argument("--target_fps", type=float, default=15.0)

    # Gates defaults (match prior usage)
    ap.add_argument("--g1_A", type=int, default=5)
    ap.add_argument("--g1_B", type=int, default=13)
    ap.add_argument("--g1_xmin", type=int, default=561)
    ap.add_argument("--g1_xmax", type=int, default=631)

    ap.add_argument("--g2_A", type=int, default=9)
    ap.add_argument("--g2_B", type=int, default=23)
    ap.add_argument("--g2_xmin", type=int, default=1283)
    ap.add_argument("--g2_xmax", type=int, default=1352)

    # Counting logic
    ap.add_argument("--yref", choices=["center","top","topq","bottom"], default="topq")
    ap.add_argument("--min_speed", type=float, default=1.0)
    ap.add_argument("--cooldown_s", type=float, default=0.0)
    ap.add_argument("--debounce_frames", type=int, default=2)
    ap.add_argument("--hyst_px", type=int, default=2, help="ref must pass this many px beyond a line")
    ap.add_argument("--min_track_age", type=int, default=2, help="min frames seen before counting")
    ap.add_argument("--invert_dir", action="store_true")

    # Filters
    ap.add_argument("--min_box_w", type=int, default=12)
    ap.add_argument("--min_box_h", type=int, default=12)
    ap.add_argument("--max_ar", type=float, default=5.0, help="max aspect ratio (w/h or h/w)")

    # Occupancy
    ap.add_argument("--seed_occupancy", type=int, default=10)
    ap.add_argument("--max_capacity", type=int, default=73)
    ap.add_argument("--fresh_start", action="store_true", help="ignore last state, start from seed")

    # Telemetry / queue / snapshots
    ap.add_argument("--post_url", type=str, default="")
    ap.add_argument("--api_key", type=str, default="")
    ap.add_argument("--snapshots", action="store_true", help="save crop on each +1/-1 to ./events")

    # UI
    ap.add_argument("--display", action="store_true")
    ap.add_argument("--save_annot", type=str, default="")
    ap.add_argument("--show_labels", action="store_true")

    # JSON config override
    ap.add_argument("--config", type=str, default="edge_config.json")
    
    # Other
    ap.add_argument("--implied_seq", action="store_true",
                                        help="Allow first crossing to count if ID was born in-band and moving toward that line")
    ap.add_argument("--debug_hits", action="store_true",
                help="Draw tick marks when a crossing is detected and show why an event was skipped")

    args = ap.parse_args()

    # Load JSON config overrides if present
    cfg_path = Path(args.config)
    if cfg_path.exists():
        try:
            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k,v in cfg.items():
                if hasattr(args, k):
                    setattr(args, k, v)
        except Exception as e:
            print(json.dumps({"ts": now_iso(), "config_error": str(e)}))

    # Hailo hint: reduce imgsz if requested runtime is hailo
    if args.hw == "hailo" and args.imgsz > 736:
        args.imgsz = 736

    # Model
    model = YOLO(args.model)
    try:
        model.fuse()
    except Exception:
        pass

    # FS dirs
    state_dir = Path("./state"); ensure_dir(state_dir)
    queue_dir = Path("./queue"); ensure_dir(queue_dir)
    events_dir = Path("./events"); ensure_dir(events_dir)
    queue_file = queue_dir / "events.jsonl"
    last_state = state_dir / "last.json"

    # Resume (only after crash, not daily planned restarts)
    if not args.fresh_start and last_state.exists():
        try:
            data = json.loads(last_state.read_text("utf-8"))
            seed_occ = int(data.get("occupancy", args.seed_occupancy))
            args.seed_occupancy = seed_occ
            print(json.dumps({"ts": now_iso(), "resume": True, "seed_occupancy": seed_occ}))
        except Exception as e:
            print(json.dumps({"ts": now_iso(), "resume_error": str(e)}))

    # State
    occupancy = int(args.seed_occupancy or 0)
    events_recent = deque(maxlen=8)
    flash = {"txt": "", "until": 0.0, "color": (0,255,0)}

    # Source
    cap = cv2.VideoCapture(0 if str(args.source).isdigit() else args.source)
    if not cap.isOpened():
        print(f"Could not open source: {args.source}")
        return

    ok, frame = cap.read()
    if not ok:
        print("No frames available.")
        return

    h, w = frame.shape[:2]

    # Gates
    gate1 = Gate("G1", h, w)
    gate2 = Gate("G2", h, w)
    gate1.A, gate1.B = int(args.g1_A), int(args.g1_B)
    gate1.xmin, gate1.xmax = max(0, int(args.g1_xmin)), min(w-1, int(args.g1_xmax))
    gate2.A, gate2.B = int(args.g2_A), int(args.g2_B)
    gate2.xmin, gate2.xmax = max(0, int(args.g2_xmin)), min(w-1, int(args.g2_xmax))

    gates = [gate1, gate2]
    active_gate_idx = 0

    writer = None
    if args.save_annot:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(args.save_annot, fourcc, max(5.0, args.target_fps), (w, h))

    win = "Edge Two-Gate Counter"
    if args.display:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(win, _mouse_cb)

    print(json.dumps({
        "ts": now_iso(), "msg": "counter_start",
        "frame": [w, h],
        "g1_init": [gate1.A, gate1.B, gate1.xmin, gate1.xmax],
        "g2_init": [gate2.A, gate2.B, gate2.xmin, gate2.xmax],
        "seed_occupancy": occupancy,
        "hw": args.hw, "imgsz": args.imgsz
    }))

    min_frame_dt = 1.0 / max(1.0, float(args.target_fps))
    show_labels = bool(args.show_labels)

    # Helpers
    def ref_y(y1, y2):
        if args.yref == "center":  return (y1 + y2) * 0.5
        if args.yref == "top":     return y1
        if args.yref == "bottom":  return y2
        return y1 + 0.25 * (y2 - y1)  # topq

    def box_ok(x1, y1b, x2, y2b):
        bw = max(0.0, x2 - x1)
        bh = max(0.0, y2b - y1b)
        if bw < args.min_box_w or bh < args.min_box_h:
            return False
        if bh > 0 and bw > 0:
            ar = max(bw / bh, bh / bw)
            if ar > args.max_ar:
                return False
        return True

    def persist_state():
        try:
            ensure_dir(state_dir)
            last_state.write_text(json.dumps({
                "ts": now_iso(), "occupancy": occupancy
            }), encoding="utf-8")
        except Exception as e:
            print(json.dumps({"ts": now_iso(), "persist_error": str(e)}))

    def snapshot_event(frame, x1, y1b, x2, y2b, event):
        if not args.snapshots:
            return
        try:
            ensure_dir(events_dir)
            ts = event.get("ts", now_iso()).replace(":","-")
            fn = events_dir / f"evt_{ts}_d{event['delta']}_id{event['track_id']}.jpg"
            x1i, y1i, x2i, y2i = map(int, [max(0,x1), max(0,y1b), min(w-1,x2), min(h-1,y2b)])
            crop = frame[y1i:y2i, x1i:x2i]
            if crop.size > 0:
                cv2.imwrite(str(fn), crop)
        except Exception as e:
            print(json.dumps({"ts": now_iso(), "snap_error": str(e)}))

    def queue_event(event):
        try:
            ensure_dir(queue_dir)
            with queue_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            print(json.dumps({"ts": now_iso(), "queue_error": str(e)}))

    def try_flush_queue():
        if not args.post_url:
            return
        if not queue_file.exists():
            return
        try:
            pending = []
            with queue_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    pending.append(json.loads(line))
            if not pending:
                return
            import requests
            headers = {"Content-Type": "application/json"}
            if args.api_key:
                headers["Authorization"] = f"Bearer {args.api_key}"
            new_lines = []
            for ev in pending:
                try:
                    r = requests.post(args.post_url, headers=headers, data=json.dumps(ev), timeout=2.0)
                    if r.status_code >= 300:
                        new_lines.append(ev)  # keep if failed
                except Exception:
                    new_lines.append(ev)
            # rewrite file
            with queue_file.open("w", encoding="utf-8") as f:
                for ev in new_lines:
                    f.write(json.dumps(ev) + "\n")
        except Exception as e:
            print(json.dumps({"ts": now_iso(), "flush_error": str(e)}))

    def emit_event(tid, cid, vy, yR, delta, box=None):
        nonlocal occupancy
        event = {
            "ts": now_iso(),
            "delta": int(delta),
            "track_id": int(tid),
            "cls": int(cid),
            "speed_px_s": round(abs(vy), 1),
            "y_ref": round(float(yR), 1),
            "occupancy_before": occupancy
        }
        occupancy = min(args.max_capacity, occupancy + 1) if delta > 0 else max(0, occupancy - 1)
        event["occupancy_after"] = occupancy
        events_recent.append(event)
        print(json.dumps({"event": event, "occupancy": occupancy}))
        if box is not None:
            snapshot_event(frame, *box, event)
        persist_state()

        # Send or queue
        if args.post_url:
            try:
                import requests
                headers = {"Content-Type": "application/json"}
                if args.api_key:
                    headers["Authorization"] = f"Bearer {args.api_key}"
                payload = {
                    "timestamp": event["ts"],
                    "delta": event["delta"],
                    "source": "edge-two-gates",
                    "track_id": event["track_id"],
                    "class_id": event["cls"],
                    "speed_px_s": event["speed_px_s"],
                    "occupancy_after": occupancy
                }
                r = requests.post(args.post_url, headers=headers, data=json.dumps(payload), timeout=2.0)
                if r.status_code >= 300:
                    queue_event(payload)
            except Exception:
                queue_event(payload)

    last_proc_t = 0.0

    while True:
        ok, frame = cap.read()
        if not ok: break

        t_now = time.time()
        if (t_now - last_proc_t) < (1.0 / max(1.0, args.target_fps)):
            if args.display:
                cv2.imshow(win, frame)
                if (cv2.waitKey(1) & 0xFF) in (27, ord('q')):
                    break
            continue
        last_proc_t = t_now

        # Flush offline queue opportunistically
        try_flush_queue()

        results = model.track(
            source=frame, stream=True, persist=True,
            conf=args.conf, iou=args.iou,
            classes=list({int(x) for x in args.classes.split(',') if x.strip()}),
            imgsz=args.imgsz, tracker=args.tracker, verbose=False,
            max_det=args.max_det
        )

        curr_ids = set()
        for res in results:
            if not hasattr(res, "boxes") or res.boxes is None or res.boxes.xyxy is None:
                continue
            xyxy = res.boxes.xyxy.cpu().numpy()
            clss = (res.boxes.cls.cpu().numpy().astype(int)
                    if res.boxes.cls is not None else np.full((len(xyxy),), -1))
            ids = (res.boxes.id.cpu().numpy().astype(int)
                   if res.boxes.id is not None else np.full((len(xyxy),), -1))

            for (x1, y1b, x2, y2b), cid, tid in zip(xyxy, clss, ids):
                if tid is None or tid < 0: 
                    continue
                if not box_ok(x1, y1b, x2, y2b):
                    continue

                curr_ids.add(int(tid))
                cx = float((x1 + x2) * 0.5)
                cy = float((y1b + y2b) * 0.5)
                yR = float(ref_y(y1b, y2b))

                for gate in gates:
                    st = gate.state[tid]

                    # increase track age while inside this gate X-range
                    if gate.xmin <= cx <= gate.xmax:
                        st["age"] = min(st.get("age", 0) + 1, 1000)
                    else:
                        # keep previous age but don't grow it when out of window
                        st.setdefault("age", 0)

                    if st["y_prev"] is None:
                        st["y_prev"] = yR
                        st["t_prev"] = t_now
                        st["deb"] = deque(maxlen=max(1, int(args.debounce_frames)))
                        continue

                    dt = max(1e-3, t_now - st["t_prev"])
                    vy = (yR - st["y_prev"]) / dt  # +down, -up

                    # only act when inside X-window
                    if not (gate.xmin <= cx <= gate.xmax):
                        st["y_prev"] = yR
                        st["t_prev"] = t_now
                        continue

                    top, bot = gate.top(), gate.bot()

                    # --- replace your four crossed_* booleans with this ---
                    def crossed_down(y_prev, y_now, line):
                        return (y_prev < line) and (y_now >= line)

                    def crossed_up(y_prev, y_now, line):
                        return (y_prev > line) and (y_now <= line)

                    def dist_ok(y_prev, y_now, line, margin):
                        # moved at least 'margin' px beyond the line on either side
                        return (abs(y_now - line) >= margin) or (abs(y_prev - line) >= margin)

                    crossed_top_down = crossed_down(st["y_prev"], yR, top)   and dist_ok(st["y_prev"], yR, top, args.hyst_px)
                    crossed_top_up   = crossed_up  (st["y_prev"], yR, top)   and dist_ok(st["y_prev"], yR, top, args.hyst_px)
                    crossed_bot_down = crossed_down(st["y_prev"], yR, bot)   and dist_ok(st["y_prev"], yR, bot, args.hyst_px)
                    crossed_bot_up   = crossed_up  (st["y_prev"], yR, bot)   and dist_ok(st["y_prev"], yR, bot, args.hyst_px)

                    # seed if first seen in-band and actually moving
                    if st["last_line"] is None and (top <= st["y_prev"] <= bot and top <= yR <= bot and abs(vy) > 0.5):
                        st["last_line"] = 'A' if vy > 0 else 'B'

                    # debounce sign
                    st["deb"].append(1 if vy > 0 else (-1 if vy < 0 else 0))
                    ssum = sum(st["deb"])
                    stable_sign = 1 if ssum >= len(st["deb"]) * 0.5 else (-1 if ssum <= -len(st["deb"]) * 0.5 else 0)

                    since = t_now - gate.last_event_at[tid]
                    speed_ok = abs(vy) >= args.min_speed
                    cd_ok = since >= max(0.0, args.cooldown_s)
                    age_ok = st.get("age", 0) >= int(args.min_track_age)

                    
                    # inside handle_top()/handle_bot() use this BEFORE setting last_line to 'A'/'B'
                    def handle_top():
                        # B -> A => -1 (unless invert)
                        if st["last_line"] == 'B' and speed_ok and cd_ok and age_ok:
                            delta = -1 if not args.invert_dir else +1
                            emit_event(tid, cid, vy, yR, delta, box=(x1,y1b,x2,y2b))
                            gate.last_event_at[tid] = t_now
                            st["last_line"] = None
                        elif args.implied_seq and st["last_line"] is None \
                            and (top <= st["y_prev"] <= bot) and speed_ok and cd_ok and age_ok:
                            # born in-band, moving up toward A → treat as B->A now
                            delta = -1 if not args.invert_dir else +1
                            emit_event(tid, cid, vy, yR, delta, box=(x1,y1b,x2,y2b))
                            gate.last_event_at[tid] = t_now
                            st["last_line"] = None
                        else:
                            st["last_line"] = 'A'

                    def handle_bot():
                        # A -> B => +1 (unless invert)
                        if st["last_line"] == 'A' and speed_ok and cd_ok and age_ok:
                            delta = +1 if not args.invert_dir else -1
                            emit_event(tid, cid, vy, yR, delta, box=(x1,y1b,x2,y2b))
                            gate.last_event_at[tid] = t_now
                            st["last_line"] = None
                        elif args.implied_seq and st["last_line"] is None \
                            and (top <= st["y_prev"] <= bot) and speed_ok and cd_ok and age_ok:
                            # born in-band, moving down toward B → treat as A->B now
                            delta = +1 if not args.invert_dir else -1
                            emit_event(tid, cid, vy, yR, delta, box=(x1,y1b,x2,y2b))
                            gate.last_event_at[tid] = t_now
                            st["last_line"] = None
                        else:
                            st["last_line"] = 'B'

                            
                    # when you compute crossed_* booleans, drop markers:
                    def tick(y, txt, col):
                        if args.debug_hits:
                            cv2.line(frame, (int(cx)-8, int(y)), (int(cx)+8, int(y)), col, 2)
                            put(frame, txt, (int(cx)+10, int(y)-4), 0.5, col, 2)

                    if crossed_top_down:   tick(top,  "A↓", (0,255,255))
                    if crossed_top_up:     tick(top,  "A↑", (0,255,255))
                    if crossed_bot_down:   tick(bot,  "B↓", (0,255,255))
                    if crossed_bot_up:     tick(bot,  "B↑", (0,255,255))

                    # if a crossing happened but no event fired, print the reason above the box:
                    if args.debug_hits and (crossed_top_down or crossed_top_up or crossed_bot_down or crossed_bot_up):
                        reasons = []
                        if not age_ok:  reasons.append("age")
                        if not speed_ok: reasons.append("speed")
                        if not cd_ok:    reasons.append("cooldown")
                        # show if we’re still inside the X-window when crossing:
                        if not (gate.xmin <= cx <= gate.xmax): reasons.append("xwin")
                        # show if we didn’t clear hysteresis far enough past the line last frame
                        # (useful near the very top of the frame)
                        if crossed_top_down and (st["y_prev"] >= top - args.hyst_px): reasons.append("hystA")
                        if crossed_bot_down and (st["y_prev"] >= bot - args.hyst_px): reasons.append("hystB")
                        if reasons:
                            put(frame, "skip:" + ",".join(reasons), (int(x1), max(12, int(y1b)-18)), 0.5, (0,0,255), 2)

                    # ---- debug: lightweight always-on status label near the track center ----
                    if args.debug_hits:
                        info = f"{gate.name}:{st.get('last_line','-')} age={st.get('age',0)} vy={vy:+.1f}"
                        put(frame, info, (int(cx)+6, max(12, int(cy)-6)), 0.45, (0,255,255), 1)

                    if args.debug_hits and st["last_line"] in ('A','B'):
                        want = 'B' if st["last_line"] == 'A' else 'A'
                        put(frame, f"→{want}", (int(cx)+10, max(12, int(cy)-22)), 0.6, (0,255,255), 2)

                    
                    both = (crossed_top_down or crossed_top_up) and (crossed_bot_down or crossed_bot_up)
                    if both:
                        d_top = abs(st["y_prev"] - top)
                        d_bot = abs(st["y_prev"] - bot)
                        if d_top <= d_bot:
                            handle_top(); handle_bot()
                        else:
                            handle_bot(); handle_top()
                    else:
                        if (crossed_top_down or crossed_top_up): handle_top()
                        if (crossed_bot_down or crossed_bot_up): handle_bot()

                    st["y_prev"] = yR
                    st["t_prev"] = t_now

                # draw
                if args.display:
                    in_any = False
                    for gate in gates:
                        if gate.xmin <= cx <= gate.xmax and gate.top() <= cy <= gate.bot():
                            in_any = True
                            break
                    color = (0, 255, 0) if in_any else (200, 200, 200)
                    cv2.rectangle(frame, (int(x1), int(y1b)), (int(x2), int(y2b)), color, 2)
                    if args.show_labels:
                        put(frame, f"id:{int(tid)} c:{int(cid)}", (int(x1), max(12, int(y1b) - 4)), 0.48, color, 1)

        # draw gates & HUD
        if args.display:
            for gi, gate in enumerate(gates):
                top, bot = gate.top(), gate.bot()
                cv2.rectangle(frame, (int(gate.xmin), int(top)), (int(gate.xmax), int(bot)),
                              (0,255,255) if gi != active_gate_idx else (0,200,255), 2)
                put(frame, f"{gate.name} A={top}px B={bot}px X=[{gate.xmin},{gate.xmax}]",
                    (10, 26 + gi*18), 0.5, (0,255,255) if gi != active_gate_idx else (0,200,255), 1)
            
            put(frame, f"Occupancy: {occupancy}", (10, frame.shape[0]-10), 0.9, (200,255,200), 2)

            # ticker (last 5)
            y0 = 60
            for i, e in enumerate(list(events_recent)[-5:]):
                msg = f"{e['ts'][11:19]} {'+' if e['delta']>0 else '-'}1 id={e['track_id']}"
                put(frame, msg, (10, y0 + 18*i), 0.5, (0,200,0) if e['delta'] > 0 else (0,0,255), 1)

            # crosshair
            mx, my = _mouse_xy
            cv2.line(frame, (mx, 0), (mx, frame.shape[0]), (60,60,60), 1)
            cv2.line(frame, (0, my), (frame.shape[1], my), (60,60,60), 1)

            cv2.imshow(win, frame)
            k = cv2.waitKey(1) & 0xFF
            if k in (27, ord('q')):
                break
            elif k == ord('g'):
                active_gate_idx = 1 - active_gate_idx
            elif k == ord('t'):
                gates[active_gate_idx].A = int(_mouse_xy[1])
            elif k == ord('b'):
                gates[active_gate_idx].B = int(_mouse_xy[1])
            elif k == ord('['):
                gates[active_gate_idx].xmin = max(0, min(w-1, int(_mouse_xy[0])))
            elif k == ord(']'):
                gates[active_gate_idx].xmax = max(0, min(w-1, int(_mouse_xy[0])))
            elif k == ord('d'):
                show_labels = not show_labels

        if writer is not None:
            writer.write(frame)

    cap.release()
    if writer is not None:
        writer.release()
    if args.display:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
