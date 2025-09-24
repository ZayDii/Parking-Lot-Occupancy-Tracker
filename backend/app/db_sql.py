# backend/app/db_sql.py
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

# Load .env only when DATABASE_URL isn't already present (i.e., local dev).
# On AWS Lambda we rely on real env vars, so python-dotenv is not required there.
if not os.getenv("DATABASE_URL"):
    try:
        from dotenv import load_dotenv  # dev-only dependency
        load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    except ModuleNotFoundError:
        # On Lambda or any environment without python-dotenv installed, skip silently.
        pass

from sqlalchemy import create_engine, select, desc
from sqlalchemy.orm import sessionmaker
from .models import Base, Lot, Device, Detection, OccupancySnapshot

# ---- Engine / Session setup ---------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "For local dev, put it in backend/.env; for deploy, set it as an environment variable."
    )

# pool_pre_ping=True avoids stale connections (useful with serverless/Neon).
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def init_db():
    """Create tables if they don't exist (typically used only in dev)."""
    Base.metadata.create_all(engine)

# ------- Mirror existing in-memory signatures ---------------------------------

def add_record(rec: Dict) -> None:
    """
    Insert a detection and update the occupancy snapshot.
    Expects keys:
      - lotId (str)
      - spacesTotal (int)
      - spacesOccupied (int)
      - timestamp (datetime)
      - cameraId (str, optional)
      - inference_ms (int, optional)
      - battery_pct (float, optional)
      - temp_c (float, optional)
    """
    ts_utc = rec["timestamp"].astimezone(timezone.utc)
    with SessionLocal() as s, s.begin():
        s.add(Detection(
            lot_id=rec["lotId"],
            ts=ts_utc,
            occupied_count=rec["spacesOccupied"],
            total_spaces=rec["spacesTotal"],
            device_id=rec.get("cameraId"),
            inference_ms=rec.get("inference_ms"),
            battery_pct=rec.get("battery_pct"),
            temp_c=rec.get("temp_c"),
        ))
        s.add(OccupancySnapshot(
            lot_id=rec["lotId"],
            ts=ts_utc,
            occupied=rec["spacesOccupied"],
            total=rec["spacesTotal"],
        ))

def get_latest(lot_id: str) -> Optional[Dict]:
    with SessionLocal() as s:
        row = (
            s.execute(
                select(OccupancySnapshot)
                .where(OccupancySnapshot.lot_id == lot_id)
                .order_by(desc(OccupancySnapshot.ts))
                .limit(1)
            )
            .scalars()
            .first()
        )
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
        rows = (
            s.execute(
                select(OccupancySnapshot)
                .where(OccupancySnapshot.lot_id == lot_id, OccupancySnapshot.ts >= cutoff)
                .order_by(OccupancySnapshot.ts)
            )
            .scalars()
            .all()
        )
        return [
            {
                "lotId": r.lot_id,
                "timestamp": r.ts.astimezone(timezone.utc),
                "spacesTotal": r.total,
                "spacesOccupied": r.occupied,
            }
            for r in rows
        ]
        
def recent_rates(lot_id: str, n: int = 60) -> List[float]:
    """
    Return up to `n` most-recent occupancy rates for a lot as floats in [0.0, 1.0],
    oldest→newest. Uses OccupancySnapshot for speed.
    """
    with SessionLocal() as s:
        rows = (
            s.execute(
                select(OccupancySnapshot)
                .where(OccupancySnapshot.lot_id == lot_id)
                .order_by(desc(OccupancySnapshot.ts))
                .limit(n)
            )
            .scalars()
            .all()
        )

        rates: List[float] = []
        for r in rows:
            tot = (r.total or 0)
            occ = (r.occupied or 0)
            if tot > 0:
                rate = max(0.0, min(1.0, occ / tot))
                rates.append(float(rate))

        rates.reverse()  # return in chronological order
        return rates