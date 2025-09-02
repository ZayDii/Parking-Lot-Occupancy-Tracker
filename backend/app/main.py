# backend/app/main.py
from fastapi import FastAPI, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta

app = FastAPI(title="Parking Lot Occupancy Tracker API", version="0.1.0")

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

# ---------- Health ----------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/health")
def api_health():
    return {"status": "ok", "service": "backend"}

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

# ---------- MVP Occupancy endpoints ----------
class OccupancyIn(BaseModel):
    lotId: str = Field(..., min_length=1)
    spacesTotal: int = Field(..., ge=0)
    spacesOccupied: int = Field(..., ge=0)
    timestamp: datetime

class OccupancyOut(OccupancyIn):
    pass

# In-memory time-series: { lotId: [records sorted by timestamp] }
_OCC: Dict[str, List[dict]] = {}

def _occ_add(rec: dict) -> None:
    lot = rec["lotId"]
    _OCC.setdefault(lot, []).append(rec)
    _OCC[lot].sort(key=lambda r: r["timestamp"])

def _occ_latest(lot_id: str):
    arr = _OCC.get(lot_id, [])
    return arr[-1] if arr else None

def _occ_history(lot_id: str, minutes: int):
    arr = _OCC.get(lot_id, [])
    if not arr:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return [r for r in arr if r["timestamp"] >= cutoff]

@app.post("/api/occupancy", response_model=OccupancyOut, status_code=status.HTTP_201_CREATED)
def post_occupancy(payload: OccupancyIn):
    if payload.spacesOccupied > payload.spacesTotal:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="spacesOccupied cannot exceed spacesTotal")
    ts = payload.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    rec = payload.model_dump()
    rec["timestamp"] = ts
    _occ_add(rec)
    return rec

@app.get("/api/occupancy/{lot_id}/current", response_model=OccupancyOut)
def get_current(lot_id: str):
    latest = _occ_latest(lot_id)
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No records for lotId")
    return latest

@app.get("/api/occupancy/{lot_id}/history", response_model=List[OccupancyOut])
def get_history(lot_id: str,
                minutes: int = Query(60, ge=1, le=24*60, description="Window in minutes")):
    return _occ_history(lot_id, minutes)
