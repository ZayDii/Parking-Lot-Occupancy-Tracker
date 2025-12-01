# edge/edge_outbox.py
import os
import sqlite3
import threading
import time
import json
from datetime import datetime, timezone

import requests


DEFAULT_DB_PATH = os.environ.get(
    "EDGE_DB_PATH",
    os.path.join(os.path.expanduser("~"), "edge_data", "edge_events.db"),
)
DEFAULT_INGEST_URL = os.environ.get("EDGE_INGEST_URL", "")
DEFAULT_LOT_ID = os.environ.get("EDGE_LOT_ID", "96N")
DEFAULT_CAMERA_ID = os.environ.get("EDGE_CAMERA_ID", "96N-camera-1")
DEFAULT_API_KEY = os.environ.get("EDGE_API_KEY", "")


class EdgeOutbox:
    """
    Small helper that:
      - Persists occupancy snapshots into a local SQLite DB
      - Periodically POSTs unsent rows to the backend /api/ingest/detections
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        ingest_url: str = DEFAULT_INGEST_URL,
        lot_id: str = DEFAULT_LOT_ID,
        camera_id: str = DEFAULT_CAMERA_ID,
        api_key: str = DEFAULT_API_KEY,
    ) -> None:
        self.db_path = db_path
        self.ingest_url = ingest_url
        self.lot_id = lot_id
        self.camera_id = camera_id
        self.api_key = api_key

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # check_same_thread=False so we can write from the GStreamer thread
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

        self._sync_thread_started = False

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS detections_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_iso TEXT NOT NULL,
                    lot_id TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    occupied_count INTEGER NOT NULL,
                    total_spots INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    sent_at TEXT,
                    last_error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.commit()

    def insert_detection(self, ts_iso: str, occupied_count: int, total_spots: int) -> None:
        """
        Called from the margin_core emit_event hook whenever occupancy changes.
        """
        payload = {
            "lot_id": self.lot_id,
            "camera_id": self.camera_id,
            "ts_iso": ts_iso,
            "occupied_count": int(occupied_count),
            "total_spots": int(total_spots),
        }
        payload_json = json.dumps(payload, separators=(",", ":"))

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO detections_outbox
                    (ts_iso, lot_id, camera_id, occupied_count, total_spots, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts_iso, self.lot_id, self.camera_id, int(occupied_count), int(total_spots), payload_json),
            )
            self._conn.commit()

    def _fetch_unsent_batch(self, limit: int = 50):
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, payload_json
                FROM detections_outbox
                WHERE sent_at IS NULL
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            )
            return cur.fetchall()

    def _mark_sent(self, row_id: int) -> None:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._lock:
            self._conn.execute(
                """
                UPDATE detections_outbox
                SET sent_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (now_iso, row_id),
            )
            self._conn.commit()

    def _mark_error(self, row_id: int, err: str) -> None:
        msg = (err or "")[:200]
        with self._lock:
            self._conn.execute(
                """
                UPDATE detections_outbox
                SET last_error = ?, retry_count = retry_count + 1
                WHERE id = ?
                """,
                (msg, row_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Background sync
    # ------------------------------------------------------------------
    def start_background_sync(self, interval_s: float = 10.0) -> None:
        """
        Starts a daemon thread that periodically POSTs unsent rows.
        If EDGE_INGEST_URL is empty, this is a no-op (local-only logging).
        """
        if self._sync_thread_started:
            return
        if not self.ingest_url:
            # Nothing to POST to; keep local-only until env is set
            return

        self._sync_thread_started = True
        t = threading.Thread(target=self._sync_loop, args=(interval_s,), daemon=True)
        t.start()

    def _sync_loop(self, interval_s: float) -> None:
        session = requests.Session()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        while True:
            try:
                batch = self._fetch_unsent_batch(limit=50)
                if not batch:
                    time.sleep(interval_s)
                    continue

                for row in batch:
                    row_id = row["id"]
                    payload_json = row["payload_json"]
                    try:
                        resp = session.post(
                            self.ingest_url,
                            data=payload_json,
                            headers=headers,
                            timeout=5.0,
                        )
                        if 200 <= resp.status_code < 300:
                            self._mark_sent(row_id)
                        else:
                            self._mark_error(row_id, f"{resp.status_code} {resp.text[:100]}")
                            # Don't hammer server if it's rejecting us
                            time.sleep(interval_s)
                            break
                    except Exception as e:  # network / DNS / timeout, etc.
                        self._mark_error(row_id, repr(e))
                        # Back off a bit before retrying
                        time.sleep(interval_s)
                        break
            except Exception as loop_err:
                # Last-resort catch so the thread never dies silently
                err_iso = datetime.now(timezone.utc).isoformat()
                print(json.dumps({"ts": err_iso, "edge_outbox_error": str(loop_err)}))
                time.sleep(interval_s)