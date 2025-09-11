import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from sqlalchemy import create_engine, select, desc
from sqlalchemy.orm import sessionmaker
from .models import Base, Lot, Device, Detection, OccupancySnapshot

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def init_db():
    Base.metadata.create_all(engine)

# ------- Mirror existing in-memory signatures -------

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Ensure .env is loaded or env var present.")

def add_record(rec: Dict) -> None:
    """rec keys: lotId, spacesTotal, spacesOccupied, timestamp, cameraId?, inference_ms?, battery_pct?, temp_c?"""
    with SessionLocal() as s, s.begin():
        s.add(Detection(
            lot_id=rec["lotId"],
            ts=rec["timestamp"].astimezone(timezone.utc),
            occupied_count=rec["spacesOccupied"],
            total_spaces=rec["spacesTotal"],
            device_id=rec.get("cameraId"),
            inference_ms=rec.get("inference_ms"),
            battery_pct=rec.get("battery_pct"),
            temp_c=rec.get("temp_c"),
        ))
        s.add(OccupancySnapshot(
            lot_id=rec["lotId"],
            ts=rec["timestamp"].astimezone(timezone.utc),
            occupied=rec["spacesOccupied"],
            total=rec["spacesTotal"],
        ))

def get_latest(lot_id: str) -> Optional[Dict]:
    with SessionLocal() as s:
        row = (s.execute(
            select(OccupancySnapshot)
            .where(OccupancySnapshot.lot_id == lot_id)
            .order_by(desc(OccupancySnapshot.ts))
            .limit(1)
        ).scalars().first())
        if not row:
            return None
        return {
            "lotId": row.lot_id,
            "timestamp": row.ts.astimezone(timezone.utc),
            "spacesTotal": row.total,
            "spacesOccupied": row.occupied,
        }

def get_history(lot_id: str, minutes: int) -> List[Dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    with SessionLocal() as s:
        rows = (s.execute(
            select(OccupancySnapshot)
            .where(OccupancySnapshot.lot_id == lot_id, OccupancySnapshot.ts >= cutoff)
            .order_by(OccupancySnapshot.ts)
        ).scalars().all())
        return [
            {
                "lotId": r.lot_id,
                "timestamp": r.ts.astimezone(timezone.utc),
                "spacesTotal": r.total,
                "spacesOccupied": r.occupied,
            } for r in rows
        ]