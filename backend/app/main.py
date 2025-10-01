# backend/app/main.py
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict
import statistics as stats
import logging
import traceback

# ---- logging ---------------------------------------------------------------
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)

# === constants ===
TOTAL_SPOTS = 77
SPOT_COUNTS = {"regular": 73, "handicapped": 4}

# Load .env only if DATABASE_URL isn't already set (local dev); skip if dotenv not installed
if not os.getenv("DATABASE_URL"):
    try:
        from dotenv import load_dotenv  # dev-only
        load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    except ModuleNotFoundError:
        pass

from fastapi import FastAPI, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from . import db_sql as db
from pydantic import BaseModel, Field
from .schemas import (
    OccupancyIn, OccupancyOut, DetectionIn,
    SnapshotOut, ForecastOut, ForecastPoint, SystemStatus
)

app = FastAPI(title="Parking Lot Occupancy Tracker API", version="0.2.0")

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

# CORS for local dev (tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Spot models ----------
class SpotBase(BaseModel):
    id: str = Field(..., min_length=1)
    label: str
    occupied: bool = False

class SpotCreate(SpotBase):
    last_update: Optional[datetime] = None  # server will set if missing

class SpotUpdate(BaseModel):
    label: Optional[str] = None
    occupied: Optional[bool] = None
    last_update: Optional[datetime] = None

class Spot(SpotBase):
    last_update: Optional[datetime] = None

# In-memory store (replace with DB later)
_SPOTS: Dict[str, Spot] = {}

# ---------- Spots CRUD ----------
@app.get("/api/spots", response_model=List[Spot])
def list_spots():
    return list(_SPOTS.values())

@app.post("/api/spots", response_model=Spot, status_code=status.HTTP_201_CREATED)
def create_spot(spot: SpotCreate):
    if spot.id in _SPOTS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Spot already exists")
    last_update = spot.last_update or datetime.now(timezone.utc)
    created = Spot(**spot.model_dump(), last_update=last_update)
    _SPOTS[spot.id] = created
    return created

@app.patch("/api/spots/{spot_id}", response_model=Spot)
def update_spot(spot_id: str, patch: SpotUpdate):
    cur = _SPOTS.get(spot_id)
    if not cur:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Spot not found")
    data = cur.model_dump()
    for k, v in patch.model_dump(exclude_unset=True).items():
        data[k] = v
    # auto timestamp if not provided
    data["last_update"] = data.get("last_update") or datetime.now(timezone.utc)
    updated = Spot(**data)
    _SPOTS[spot_id] = updated
    return updated

@app.delete("/api/spots/{spot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_spot(spot_id: str):
    if spot_id not in _SPOTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Spot not found")
    del _SPOTS[spot_id]
    return None

# ---------- Occupancy time-series ----------
@app.post("/api/occupancy", response_model=OccupancyOut, status_code=status.HTTP_201_CREATED)
def post_occupancy(payload: OccupancyIn):
    if payload.spacesOccupied > payload.spacesTotal:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="spacesOccupied cannot exceed spacesTotal")

    # 1) Use server time if client omitted timestamp
    ts = payload.timestamp or datetime.now(timezone.utc)

    # 2) Normalize to UTC
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)

    # 3) Ensure strictly newer than the current latest so /snapshot updates
    latest = db.get_latest(payload.lotId)
    if latest and ts <= latest["timestamp"]:
        ts = latest["timestamp"] + timedelta(seconds=1)

    rec = {
        "lotId": payload.lotId,
        "spacesTotal": payload.spacesTotal,
        "spacesOccupied": payload.spacesOccupied,
        "timestamp": ts,
    }
    db.add_record(rec)
    _EDGE_LAST_SEEN[payload.lotId] = ts
    return rec

@app.get("/api/occupancy/{lot_id}/current", response_model=OccupancyOut)
def get_current(lot_id: str):
    latest = db.get_latest(lot_id)
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No records for lotId")
    return latest

@app.get("/api/occupancy/{lot_id}/history", response_model=List[OccupancyOut])
def get_history(lot_id: str,
                minutes: int = Query(60, ge=1, le=24*60, description="Window in minutes")):
    return db.get_history(lot_id, minutes)

# ---------- Edge ingestion (Pi → server) ----------
@app.post("/api/ingest/detections")
def ingest_detection(d: DetectionIn):
    """
    Normalize edge payloads:
      - Ignore incoming total_spots; enforce canonical TOTAL_SPOTS.
      - Clamp occupied_count to [0, TOTAL_SPOTS].
      - Parse ISO timestamp and store as UTC.
    """
    # 1) Parse timestamp (accepts ...Z or offset form)
    try:
        ts_utc = datetime.fromisoformat(d.ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad ts_iso: {e}")

    # 2) Enforce canonical totals and clamp occupied
    total = TOTAL_SPOTS  # <-- single source of truth
    try:
        occ = int(d.occupied_count)
    except Exception:
        raise HTTPException(status_code=400, detail="occupied_count must be an integer")

    if occ < 0:
        logger.warning("ingest_detection: negative occupied_count %s; clamping to 0", occ)
        occ = 0
    if occ > total:
        logger.warning(
            "ingest_detection: occupied_count %s exceeds TOTAL_SPOTS %s; clamping",
            occ, total
        )
        occ = total

    # 3) Persist normalized record
    rec = {
        "lotId": d.lot_id,
        "spacesTotal": total,          # <-- enforced
        "spacesOccupied": occ,         # <-- clamped
        "timestamp": ts_utc,           # <-- UTC
        "cameraId": d.camera_id,
    }

    try:
        db.add_record(rec)
        _EDGE_LAST_SEEN[d.lot_id] = ts_utc
        return {"ok": True, "lot_id": d.lot_id, "occupied_count": occ, "total_spots": total}
    except Exception as e:
        logger.error("add_record failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB write failed: {e}")

# ---------- Unified snapshot & baseline forecast ----------
@app.get("/api/occupancy/snapshot", response_model=SnapshotOut)
def occupancy_snapshot(lot_id: str = Query(..., min_length=1)):
    latest = db.get_latest(lot_id)
    if not latest:
        # return a neutral snapshot instead of 404
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return SnapshotOut(
            lot_id=lot_id,
            ts_iso=now,
            occupied_count=0,
            total_spots=0,
            occupancy_rate=0.0,
        )

    ts = latest["timestamp"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    tot = latest["spacesTotal"] or 0
    occ = latest["spacesOccupied"] or 0
    rate = (occ / tot) if tot else 0.0
    return SnapshotOut(
        lot_id=lot_id,
        ts_iso=ts,
        occupied_count=occ,
        total_spots=tot,
        occupancy_rate=float(max(0.0, min(1.0, rate))),
    )

@app.get("/api/forecast", response_model=ForecastOut)
def get_forecast(lot_id: str = Query(..., min_length=1), hours: int = Query(12, ge=1, le=48)):
    now = datetime.now(timezone.utc)
    # NOTE: ensure db_sql.py defines recent_rates(...) or this will raise AttributeError.
    rates = db.recent_rates(lot_id, n=60)  # ~last hour if ~1/min sampling
    if rates:
        baseline = stats.median(rates)
    else:
        latest = db.get_latest(lot_id)
        baseline = ((latest["spacesOccupied"] / latest["spacesTotal"])
                    if latest and latest["spacesTotal"] else 0.0)

    points = []
    for h in range(1, hours + 1):
        t = (now + timedelta(hours=h)).isoformat().replace("+00:00", "Z")
        points.append(ForecastPoint(ts_iso=t, expected_occupancy_rate=float(max(0.0, min(1.0, baseline)))))
    return ForecastOut(lot_id=lot_id, horizon_hours=hours, points=points)

# ---------- System status (placeholder) ----------
_SERVICE_START = datetime.now(timezone.utc)
_EDGE_LAST_SEEN: Dict[str, datetime] = {}

@app.get("/api/status", response_model=SystemStatus)
def get_status():
    uptime = int((datetime.now(timezone.utc) - _SERVICE_START).total_seconds())
    recent = [t for t in _EDGE_LAST_SEEN.values() if (datetime.now(timezone.utc) - t) <= timedelta(minutes=2)]
    last_seen = max(_EDGE_LAST_SEEN.values()).isoformat().replace("+00:00", "Z") if _EDGE_LAST_SEEN else None
    return SystemStatus(
        service_uptime_s=uptime,
        edge_last_seen_iso=last_seen,
        est_battery_pct=None,   # wire real telemetry later
        cameras_online=len(recent),
    )

from mangum import Mangum
handler = Mangum(app)