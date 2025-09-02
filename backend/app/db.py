# backend/app/db.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, TypedDict
import threading, bisect

# ---- Types ----
class OccRecord(TypedDict, total=False):
    """One occupancy record (used by /api/occupancy and /api/ingest/detections)."""
    lotId: str
    spacesTotal: int
    spacesOccupied: int
    timestamp: datetime     # must be timezone-aware (UTC)
    cameraId: str           # optional

# ---- In-memory store ----
_DB: Dict[str, List[OccRecord]] = {}     # { lotId: [records sorted by timestamp asc] }
_LOCK = threading.Lock()
_RETENTION = timedelta(hours=24)          # keep last 24h per lot by default

# ---- Internals ----
def _ensure_aware_utc(ts: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)

def _key_list(lst: List[OccRecord]) -> List[datetime]:
    """Extract timestamps (helper for bisect)."""
    return [r["timestamp"] for r in lst]

# ---- Public API ----
def add_record(record: OccRecord) -> None:
    """
    Insert a record in timestamp order and prune beyond retention.
    Required keys: lotId, spacesTotal, spacesOccupied, timestamp (UTC or naive).
    """
    for k in ("lotId", "spacesTotal", "spacesOccupied", "timestamp"):
        if k not in record:
            raise ValueError(f"Missing key '{k}' in record")

    ts = _ensure_aware_utc(record["timestamp"])
    rec = {**record, "timestamp": ts}

    with _LOCK:
        lst = _DB.setdefault(rec["lotId"], [])
        idx = bisect.bisect_left(_key_list(lst), ts)
        lst.insert(idx, rec)

        # prune older than retention
        cutoff = datetime.now(timezone.utc) - _RETENTION
        first_keep = bisect.bisect_left(_key_list(lst), cutoff)
        if first_keep > 0:
            del lst[:first_keep]

def get_latest(lot_id: str) -> Optional[OccRecord]:
    """Return the most recent record for a lot, or None."""
    with _LOCK:
        lst = _DB.get(lot_id, [])
        return lst[-1] if lst else None

def get_history(lot_id: str, minutes: int) -> List[OccRecord]:
    """Return all records from the last `minutes` minutes (sorted asc)."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    with _LOCK:
        lst = _DB.get(lot_id, [])
        if not lst:
            return []
        i = bisect.bisect_left(_key_list(lst), cutoff)
        return lst[i:].copy()

def recent_rates(lot_id: str, n: int = 60) -> List[float]:
    """
    Return up to last n occupancy rates (0..1) for forecasting baselines.
    """
    with _LOCK:
        lst = _DB.get(lot_id, [])
        if not lst:
            return []
        tail = lst[-n:] if n > 0 else lst[:]
        out: List[float] = []
        for r in tail:
            tot = r.get("spacesTotal") or 0
            occ = r.get("spacesOccupied") or 0
            out.append((occ / tot) if tot > 0 else 0.0)
        return out

# ---- Utilities (handy for tests / maintenance) ----
def set_retention(hours: float) -> None:
    """Adjust retention window (e.g., set_retention(1.0) for 1 hour)."""
    global _RETENTION
    _RETENTION = timedelta(hours=hours)

def clear(lot_id: Optional[str] = None) -> None:
    """Clear all data (or just one lot). Useful in pytest or local resets."""
    with _LOCK:
        if lot_id is None:
            _DB.clear()
        else:
            _DB.pop(lot_id, None)

def list_lots() -> List[str]:
    """Return known lotIds (for diagnostics)."""
    with _LOCK:
        return list(_DB.keys())
