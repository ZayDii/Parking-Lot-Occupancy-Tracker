# margin_core.py

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import cv2
import numpy as np


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

        # per-track state:
        #   y_prev/t_prev: last reference Y + time
        #   region: 'above' / 'inside' / 'below'
        #   in_band: True while we are inside [A,B]
        #   origin_side: 'above' or 'below' – where we came from when we entered band
        self.state = defaultdict(
            lambda: {
                "y_prev": None,
                "t_prev": 0.0,
                "region": "inside",
                "in_band": False,
                "origin_side": None,
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
      - CPU path (margin_counter.py)
      - Hailo path (hailo_margin_counter.py)

    Simple crossing logic:
      - When a car enters the band [A,B] from above and later exits below -> +1 (A->B)
      - When it enters from below and later exits above -> -1 (B->A)
      - Direction can be flipped globally with --invert_dir.
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

        # geometry from args
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

        # optional hook for DB / telemetry
        self.on_occupancy_update = None

    # ------------------------------------------------------------------
    # Core per-frame processing
    # ------------------------------------------------------------------
    def process(self, frame, detections, t_now):
        """
        frame: numpy array (BGR)
        detections: list of dicts:
            {"id": track_id, "cls": cid, "conf": conf, "xyxy": (x1,y1,x2,y2)}
        """
        args = self.args

        # --- config knobs ----
        yref_mode = getattr(args, "yref", "topq")
        min_speed = float(getattr(args, "min_speed", 0.1))
        max_speed_px_s = float(getattr(args, "max_speed_px_s", 0.0))
        cooldown_s = float(getattr(args, "cooldown_s", 0.0))
        hyst_px = int(getattr(args, "hyst_px", 2))
        # min_track_age & implied_seq are ignored in this simplified logic
        invert_dir = bool(getattr(args, "invert_dir", False))
        min_box_w = int(getattr(args, "min_box_w", 3))
        min_box_h = int(getattr(args, "min_box_h", 3))
        max_ar = float(getattr(args, "max_ar", 5.0))
        max_capacity = int(getattr(args, "max_capacity", 73))
        show_labels = bool(getattr(args, "show_labels", True))
        debug_hits = bool(getattr(args, "debug_hits", False))
        display = bool(getattr(args, "display", True))

        # mask opacity; >0 means we draw the semi-transparent gate bands
        mask_alpha = float(getattr(args, "mask_alpha", 0.25))

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

            ts_utc = datetime.now(timezone.utc)
            ts_est = ts_utc.astimezone(ZoneInfo("America/New_York"))

            event = {
                "ts_utc": ts_utc.isoformat(),
                "ts_local": ts_est.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
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

            hook = getattr(self, "on_occupancy_update", None)
            if hook is not None:
                try:
                    hook(ts_utc=ts_utc, occupancy_after=after, max_capacity=max_capacity)
                except Exception as e:
                    print(json.dumps({
                        "ts": ts_utc.isoformat(),
                        "hook_error": str(e),
                    }))

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
                top = gate.top()
                bot = gate.bot()

                # -------- init per-track state for this gate --------
                if st["y_prev"] is None:
                    st["y_prev"] = yR
                    st["t_prev"] = t_now
                    # set initial region
                    if yR < top - hyst_px:
                        st["region"] = "above"
                    elif yR > bot + hyst_px:
                        st["region"] = "below"
                    else:
                        st["region"] = "inside"
                    st["in_band"] = (st["region"] == "inside")
                    st["origin_side"] = None
                    continue

                # -------- compute vy --------
                dt = max(1e-3, t_now - st["t_prev"])
                vy = (yR - st["y_prev"]) / dt  # +down, -up

                if max_speed_px_s > 0:
                    if vy > max_speed_px_s:
                        vy = max_speed_px_s
                    elif vy < -max_speed_px_s:
                        vy = -max_speed_px_s

                # -------- current region relative to band --------
                if yR < top - hyst_px:
                    region = "above"
                elif yR > bot + hyst_px:
                    region = "below"
                else:
                    region = "inside"

                prev_region = st.get("region", "inside")

                # -------- gating conditions --------
                in_x = (gate.xmin <= cx <= gate.xmax)
                since = t_now - gate.last_event_at[tid]
                speed_ok = abs(vy) >= min_speed
                cd_ok = since >= max(0.0, cooldown_s)

                # -------- band entry/exit tracking (origin_side + fallback) --------
                #
                # When we first enter the band from above or below, remember origin_side.
                # If we later leave on the opposite side, count a crossing.
                # If we never had a clear origin_side (track spawned inside band),
                # fall back to the original vy-based logic so we still count.

                if region == "inside":
                    # Just entered the band?
                    if prev_region != "inside":
                        if prev_region in ("above", "below"):
                            st["origin_side"] = prev_region
                        else:
                            st["origin_side"] = None
                    st["in_band"] = True

                else:
                    # We are outside the band (above or below)
                    if prev_region == "inside" and st.get("in_band", False):
                        origin = st.get("origin_side")

                        if in_x and speed_ok and cd_ok:
                            if (
                                origin in ("above", "below")
                                and region in ("above", "below")
                                and region != origin
                            ):
                                # Preferred: full crossing based on origin vs exit side
                                if origin == "above" and region == "below":
                                    raw_delta = +1   # A->B (enter lot)
                                elif origin == "below" and region == "above":
                                    raw_delta = -1   # B->A (leave lot)
                                else:
                                    raw_delta = 0

                                if raw_delta != 0:
                                    delta = -raw_delta if invert_dir else raw_delta
                                    emit_event(
                                        tid, cid, vy, yR, delta, box=(x1, y1b, x2, y2b)
                                    )
                                    gate.last_event_at[tid] = t_now

                            elif origin is None:
                                # Fallback: behave like your original logic
                                # y grows downward. vy > 0 => moving down, vy < 0 => moving up.
                                if region == "below" and vy > 0:
                                    delta = +1 if not invert_dir else -1
                                    emit_event(
                                        tid, cid, vy, yR, delta, box=(x1, y1b, x2, y2b)
                                    )
                                    gate.last_event_at[tid] = t_now

                                elif region == "above" and vy < 0:
                                    delta = -1 if not invert_dir else +1
                                    emit_event(
                                        tid, cid, vy, yR, delta, box=(x1, y1b, x2, y2b)
                                    )
                                    gate.last_event_at[tid] = t_now

                    # Reset band state whenever we are outside
                    st["in_band"] = False
                    st["origin_side"] = None




                # -------- update state for next frame --------
                st["y_prev"] = yR
                st["t_prev"] = t_now
                st["region"] = region

                # -------- draw per-box --------
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

                # if debug_hits:
                    # info = (
                        # f"{gate.name}: reg={st.get('region')} "
                        # f"in={st.get('in_band')} "
                        # f"orig={st.get('origin_side')} "
                        # f"vy={vy:+.1f}"
                    # )
                    # put(
                        # frame,
                        # info,
                        # (int(cx) + 6, max(12, int(cy) - 6)),
                        # 0.45,
                        # (0, 255, 255),
                        # 1,
                    # )
                    
                

        # ------------------------------------------------------------------
        # draw gate MASKS + gate outlines + HUD
        # ------------------------------------------------------------------
        if display:
            # 1) semi-transparent gate masks
            if mask_alpha > 0:
                overlay = frame.copy()
                for gi, gate in enumerate(self.gates):
                    top, bot = gate.top(), gate.bot()
                    cv2.rectangle(
                        overlay,
                        (int(gate.xmin), int(top)),
                        (int(gate.xmax), int(bot)),
                        (0, 255, 255),
                        -1,
                    )
                cv2.addWeighted(overlay, mask_alpha, frame, 1.0 - mask_alpha, 0, frame)

            # 2) gate outlines + labels
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
                ts_display = e.get("ts_local") or e.get("ts_utc", "")
                msg = (
                    f"{ts_display} "
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
