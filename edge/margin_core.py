# margin_core.py

import json
from collections import defaultdict, deque
from datetime import datetime, timezone

import cv2
import numpy as np


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def put(frame, txt, org, scale=0.55, color=(255, 255, 0), thick=2):
    cv2.putText(
        frame,
        txt,
        org,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thick,
        cv2.LINE_AA,
    )


class Gate:
    def __init__(self, name, h, w):
        self.name = name
        self.A = 0
        self.B = 0
        self.xmin, self.xmax = 0, w - 1
        self.state = defaultdict(
            lambda: {
                "last_line": None,
                "y_prev": None,
                "t_prev": 0.0,
                "deb": deque(maxlen=2),
                "age": 0,
            }
        )
        # per-track last event time (for cooldown)
        self.last_event_at = defaultdict(lambda: 0.0)

    def top(self):
        return min(self.A, self.B)

    def bot(self):
        return max(self.A, self.B)


class MarginCounter:
    """
    Shared margin logic used by both:
      - CPU path (margin_counter.py) – if you call process() there
      - Hailo path (hailo_margin_counter.py)

    This version also draws a semi-transparent mask over each gate
    (from A to B, and xmin to xmax) so you can clearly see the monitored margins.
    """

    def __init__(self, args, frame_shape):
        h, w = frame_shape[:2]
        self.args = args
        self.h, self.w = h, w

        # occupancy state
        self.occupancy = int(getattr(args, "seed_occupancy", 0) or 0)

        # gates
        self.gate1 = Gate("G1", h, w)
        self.gate2 = Gate("G2", h, w)

        # geometry from args (same fields as in hailo_margin_counter.py)
        self.gate1.A, self.gate1.B = int(args.g1_A), int(args.g1_B)
        self.gate1.xmin = max(0, int(args.g1_xmin))
        self.gate1.xmax = min(w - 1, int(args.g1_xmax))

        self.gate2.A, self.gate2.B = int(args.g2_A), int(args.g2_B)
        self.gate2.xmin = max(0, int(args.g2_xmin))
        self.gate2.xmax = min(w - 1, int(args.g2_xmax))

        self.gates = [self.gate1, self.gate2]
        self.active_gate_idx = 0  # just for highlighting in HUD

        # recent events for HUD ticker
        self.events_recent = deque(maxlen=8)

    # ------------------------------------------------------------------
    # Core per-frame processing
    # ------------------------------------------------------------------
    def process(self, frame, detections, t_now):
        """
        frame: numpy array (BGR)
        detections: list of dicts:
            {"id": track_id, "cls": cid, "conf": conf, "xyxy": (x1,y1,x2,y2)}

        This function:
          - filters boxes
          - applies gate / crossing logic
          - updates self.occupancy
          - draws boxes, gate masks, gates, and HUD overlays onto 'frame'
        """
        args = self.args

        # --- config knobs (same semantics as margin_counter.py) ----
        yref_mode = getattr(args, "yref", "topq")
        min_speed = float(getattr(args, "min_speed", 1.0))
        cooldown_s = float(getattr(args, "cooldown_s", 0.0))
        debounce_frames = int(getattr(args, "debounce_frames", 2))
        hyst_px = int(getattr(args, "hyst_px", 2))
        min_track_age = int(getattr(args, "min_track_age", 2))
        invert_dir = bool(getattr(args, "invert_dir", False))
        implied_seq = bool(getattr(args, "implied_seq", False))
        min_box_w = int(getattr(args, "min_box_w", 12))
        min_box_h = int(getattr(args, "min_box_h", 12))
        max_ar = float(getattr(args, "max_ar", 5.0))
        max_capacity = int(getattr(args, "max_capacity", 9999))
        show_labels = bool(getattr(args, "show_labels", True))
        debug_hits = bool(getattr(args, "debug_hits", False))
        display = bool(getattr(args, "display", True))

        # NEW: mask opacity; >0 means we draw the semi-transparent gate bands
        mask_alpha = float(getattr(args, "mask_alpha", 0.25))  # 0.25 = fairly visible

        # -----------------------------------
        # helpers
        # -----------------------------------
        def ref_y(y1, y2):
            if yref_mode == "center":
                return (y1 + y2) * 0.5
            if yref_mode == "top":
                return y1
            if yref_mode == "bottom":
                return y2
            # default "topq" (top quarter)
            return y1 + 0.25 * (y2 - y1)

        def box_ok(x1, y1b, x2, y2b):
            bw = max(0.0, x2 - x1)
            bh = max(0.0, y2b - y1b)
            if bw < min_box_w or bh < min_box_h:
                return False
            if bh > 0 and bw > 0:
                ar = max(bw / bh, bh / bw)
                if ar > max_ar:
                    return False
            return True

        def emit_event(tid, cid, vy, yR, delta, box=None):
            """Update occupancy and record an event."""
            before = self.occupancy
            after = before + (1 if delta > 0 else -1)
            after = max(0, min(max_capacity, after))

            event = {
                "ts": now_iso(),
                "delta": int(delta),
                "track_id": int(tid),
                "cls": int(cid),
                "speed_px_s": round(abs(vy), 1),
                "y_ref": round(float(yR), 1),
                "occupancy_before": before,
                "occupancy_after": after,
            }

            self.occupancy = after
            self.events_recent.append(event)

            # minimal JSON log for debugging
            print(json.dumps({"event": event, "occupancy": self.occupancy}))

        # -----------------------------------
        # main logic
        # -----------------------------------
        for det in detections:
            tid = det.get("id", -1)
            cid = det.get("cls", -1)
            if tid is None or tid < 0:
                continue

            x1, y1b, x2, y2b = det["xyxy"]
            if not box_ok(x1, y1b, x2, y2b):
                continue

            cx = float((x1 + x2) * 0.5)
            cy = float((y1b + y2b) * 0.5)
            yR = float(ref_y(y1b, y2b))

            for gate in self.gates:
                st = gate.state[tid]

                # grow age when inside gate X-window
                if gate.xmin <= cx <= gate.xmax:
                    st["age"] = min(st.get("age", 0) + 1, 1000)
                else:
                    st.setdefault("age", 0)

                # first sighting
                if st["y_prev"] is None:
                    st["y_prev"] = yR
                    st["t_prev"] = t_now
                    st["deb"] = deque(maxlen=max(1, debounce_frames))
                    continue

                dt = max(1e-3, t_now - st["t_prev"])
                vy = (yR - st["y_prev"]) / dt  # +down, -up

                if not (gate.xmin <= cx <= gate.xmax):
                    st["y_prev"] = yR
                    st["t_prev"] = t_now
                    continue

                top = gate.top()
                bot = gate.bot()

                # crossing helpers
                def crossed_down(y_prev, y_now, line):
                    return (y_prev < line) and (y_now >= line)

                def crossed_up(y_prev, y_now, line):
                    return (y_prev > line) and (y_now <= line)

                def dist_ok(y_prev, y_now, line, margin):
                    return (abs(y_now - line) >= margin) or (
                        abs(y_prev - line) >= margin
                    )

                crossed_top_down = crossed_down(st["y_prev"], yR, top) and dist_ok(
                    st["y_prev"], yR, top, hyst_px
                )
                crossed_top_up = crossed_up(st["y_prev"], yR, top) and dist_ok(
                    st["y_prev"], yR, top, hyst_px
                )
                crossed_bot_down = crossed_down(st["y_prev"], yR, bot) and dist_ok(
                    st["y_prev"], yR, bot, hyst_px
                )
                crossed_bot_up = crossed_up(st["y_prev"], yR, bot) and dist_ok(
                    st["y_prev"], yR, bot, hyst_px
                )

                # seed last_line if born in-band & moving
                if (
                    st["last_line"] is None
                    and (top <= st["y_prev"] <= bot and top <= yR <= bot and abs(vy) > 0.5)
                ):
                    st["last_line"] = "A" if vy > 0 else "B"

                st["deb"].append(1 if vy > 0 else (-1 if vy < 0 else 0))
                ssum = sum(st["deb"])
                stable_sign = (
                    1
                    if ssum >= len(st["deb"]) * 0.5
                    else (-1 if ssum <= -len(st["deb"]) * 0.5 else 0)
                )
                _ = stable_sign  # reserved if you want to re-use debounced sign

                since = t_now - gate.last_event_at[tid]
                speed_ok = abs(vy) >= min_speed
                cd_ok = since >= max(0.0, cooldown_s)
                age_ok = st.get("age", 0) >= int(min_track_age)

                # direction handlers
                def handle_top():
                    # B -> A => -1 (unless invert)
                    if st["last_line"] == "B" and speed_ok and cd_ok and age_ok:
                        delta = -1 if not invert_dir else +1
                        emit_event(tid, cid, vy, yR, delta, box=(x1, y1b, x2, y2b))
                        gate.last_event_at[tid] = t_now
                        st["last_line"] = None
                    elif (
                        implied_seq
                        and st["last_line"] is None
                        and (top <= st["y_prev"] <= bot)
                        and speed_ok
                        and cd_ok
                        and age_ok
                    ):
                        delta = -1 if not invert_dir else +1
                        emit_event(tid, cid, vy, yR, delta, box=(x1, y1b, x2, y2b))
                        gate.last_event_at[tid] = t_now
                        st["last_line"] = None
                    else:
                        st["last_line"] = "A"

                def handle_bot():
                    # A -> B => +1 (unless invert)
                    if st["last_line"] == "A" and speed_ok and cd_ok and age_ok:
                        delta = +1 if not invert_dir else -1
                        emit_event(tid, cid, vy, yR, delta, box=(x1, y1b, x2, y2b))
                        gate.last_event_at[tid] = t_now
                        st["last_line"] = None
                    elif (
                        implied_seq
                        and st["last_line"] is None
                        and (top <= st["y_prev"] <= bot)
                        and speed_ok
                        and cd_ok
                        and age_ok
                    ):
                        delta = +1 if not invert_dir else -1
                        emit_event(tid, cid, vy, yR, delta, box=(x1, y1b, x2, y2b))
                        gate.last_event_at[tid] = t_now
                        st["last_line"] = None

                both = (crossed_top_down or crossed_top_up) and (
                    crossed_bot_down or crossed_bot_up
                )
                if both:
                    d_top = abs(st["y_prev"] - top)
                    d_bot = abs(st["y_prev"] - bot)
                    if d_top <= d_bot:
                        handle_top()
                        handle_bot()
                    else:
                        handle_bot()
                        handle_top()
                else:
                    if crossed_top_down or crossed_top_up:
                        handle_top()
                    if crossed_bot_down or crossed_bot_up:
                        handle_bot()

                st["y_prev"] = yR
                st["t_prev"] = t_now

                # draw per-box
                if display:
                    in_any = False
                    for g in self.gates:
                        if g.xmin <= cx <= g.xmax and g.top() <= cy <= g.bot():
                            in_any = True
                            break
                    color = (0, 255, 0) if in_any else (200, 200, 200)
                    cv2.rectangle(
                        frame, (int(x1), int(y1b)), (int(x2), int(y2b)), color, 2
                    )
                    if show_labels:
                        put(
                            frame,
                            f"id:{int(tid)} c:{int(cid)}",
                            (int(x1), max(12, int(y1b) - 4)),
                            0.48,
                            color,
                            1,
                        )

                if debug_hits:
                    info = f"{gate.name}:{st.get('last_line','-')} age={st.get('age',0)} vy={vy:+.1f}"
                    put(
                        frame,
                        info,
                        (int(cx) + 6, max(12, int(cy) - 6)),
                        0.45,
                        (0, 255, 255),
                        1,
                    )

        # ------------------------------------------------------------------
        # draw gate MASKS + gate outlines + HUD
        # ------------------------------------------------------------------
        if display:
            # 1) semi-transparent gate masks (this is what you’ve been expecting to see)
            if mask_alpha > 0:
                overlay = frame.copy()
                for gi, gate in enumerate(self.gates):
                    top, bot = gate.top(), gate.bot()
                    cv2.rectangle(
                        overlay,
                        (int(gate.xmin), int(top)),
                        (int(gate.xmax), int(bot)),
                        (0, 255, 255),  # yellowish band
                        -1,
                    )
                cv2.addWeighted(overlay, mask_alpha, frame, 1.0 - mask_alpha, 0, frame)

            # 2) gate outlines + labels (same as margin_counter.py)
            for gi, gate in enumerate(self.gates):
                top, bot = gate.top(), gate.bot()
                cv2.rectangle(
                    frame,
                    (int(gate.xmin), int(top)),
                    (int(gate.xmax), int(bot)),
                    (0, 255, 255)
                    if gi != self.active_gate_idx
                    else (0, 200, 255),
                    2,
                )
                put(
                    frame,
                    f"{gate.name} A={top}px B={bot}px X=[{gate.xmin},{gate.xmax}]",
                    (10, 26 + gi * 18),
                    0.5,
                    (0, 255, 255)
                    if gi != self.active_gate_idx
                    else (0, 200, 255),
                    1,
                )

            # 3) Occupancy HUD
            put(
                frame,
                f"Occupancy: {self.occupancy}",
                (10, frame.shape[0] - 10),
                0.9,
                (200, 255, 200),
                2,
            )

            # 4) ticker (last few events)
            y0 = 60
            for i, e in enumerate(list(self.events_recent)[-5:]):
                msg = (
                    f"{e['ts'][11:19]} "
                    f"{'+' if e['delta'] > 0 else '-'}1 id={e['track_id']}"
                )
                put(
                    frame,
                    msg,
                    (10, y0 + 18 * i),
                    0.5,
                    (0, 200, 0) if e["delta"] > 0 else (0, 0, 255),
                    1,
                )

        return frame
