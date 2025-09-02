from datetime import datetime, timedelta
from typing import List, Dict
from dateutil import tz

# A simple in-memory "DB": { lotId: [records_sorted_by_time] }
_DB: Dict[str, List[dict]] = {}

def add_record(record: dict) -> None:
    lot = record["lotId"]
    _DB.setdefault(lot, []).append(record)
    # Keep sorted by timestamp (ascending)
    _DB[lot].sort(key=lambda r: r["timestamp"])

def get_latest(lot_id: str):
    records = _DB.get(lot_id, [])
    return records[-1] if records else None

def get_history(lot_id: str, minutes: int):
    records = _DB.get(lot_id, [])
    if not records:
        return []
    cutoff = datetime.now(tz.UTC) - timedelta(minutes=minutes)
    return [r for r in records if r["timestamp"] >= cutoff]