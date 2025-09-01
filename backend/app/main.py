from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Parking Lot Occupancy Tracker API", version="0.1.0")

# CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Spot(BaseModel):
    id: str
    label: str
    occupied: bool = False
    last_update: Optional[str] = None  # ISO string

# In-memory store (replace with DB later)
_SPOTS: dict[str, Spot] = {}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/health")
def api_health():
    return {"status": "ok", "service": "backend"}

@app.get("/api/spots", response_model=List[Spot])
def list_spots():
    return list(_SPOTS.values())

@app.post("/api/spots", response_model=Spot)
def create_spot(spot: Spot):
    if spot.id in _SPOTS:
        raise ValueError("Spot already exists")
    _SPOTS[spot.id] = spot
    return spot

@app.patch("/api/spots/{spot_id}", response_model=Spot)
def update_spot(spot_id: str, patch: Spot):
    if spot_id not in _SPOTS:
        raise ValueError("Spot not found")
    # Simple merge
    cur = _SPOTS[spot_id].model_dump()
    for k, v in patch.model_dump(exclude_unset=True).items():
        cur[k] = v
    updated = Spot(**cur)
    _SPOTS[spot_id] = updated
    return updated

@app.delete("/api/spots/{spot_id}")
def delete_spot(spot_id: str):
    if spot_id in _SPOTS:
        del _SPOTS[spot_id]
    return {"ok": True}
