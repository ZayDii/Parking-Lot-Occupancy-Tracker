from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime

# Base: forbid unknown keys across all payloads
class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

# --- Occupancy payloads used by /api/occupancy (camelCase kept) ---
class OccupancyIn(BaseModel):
    lotId: str
    spacesTotal: int
    spacesOccupied: int
    timestamp: Optional[datetime] = Field(
        default=None,
        description="Optional; server uses current UTC if omitted."
    )
    # Make Swagger show a no-timestamp example
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "lotId": "96N",
            "spacesTotal": 50,
            "spacesOccupied": 15
        }
    })

class OccupancyOut(OccupancyIn):
    pass

# --- Edge ingestion payload (Pi → server) ---
class DetectionIn(StrictModel):
    lot_id: str = Field(..., min_length=1)
    camera_id: str = Field(..., min_length=1)
    ts_iso: str  # ISO-8601 string (e.g., "2025-09-02T12:34:56Z")
    occupied_count: int = Field(..., ge=0)
    total_spots: int = Field(..., gt=0)

# --- Snapshot/forecast/status for frontend ---
class SnapshotOut(StrictModel):
    lot_id: str
    ts_iso: str
    occupied_count: int
    total_spots: int
    occupancy_rate: float  # 0..1

class ForecastPoint(StrictModel):
    ts_iso: str
    expected_occupancy_rate: float

class ForecastOut(StrictModel):
    lot_id: str
    horizon_hours: int
    points: List[ForecastPoint]

class SystemStatus(StrictModel):
    service_uptime_s: int
    edge_last_seen_iso: Optional[str] = None
    est_battery_pct: Optional[float] = None
    cameras_online: int = 0
