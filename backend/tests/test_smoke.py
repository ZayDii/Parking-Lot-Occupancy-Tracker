def test_placeholder():
    assert 2 + 2 == 4

from datetime import datetime, timedelta, timezone
import statistics as stats
import os
from sqlalchemy import create_engine, select, desc
from sqlalchemy.orm import sessionmaker
from .schemas import (
    OccupancyIn, OccupancyOut, DetectionIn,
    SnapshotOut, ForecastOut, ForecastPoint, SystemStatus, LotIn, LotOut,
    DeviceIn, DeviceOut
)
from fastapi import FastAPI, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict     

# ---------- Ingest occupancy data from edge ----------
@app.post("/api/occupancy", response_model=OccupancyOut)
def post_occupancy(payload: OccupancyIn):
    return db.post_occupancy(payload)

# ---------- Get current occupancy snapshot ----------
@app.get("/api/occupancy/{lot_id}/current", response_model=OccupancyOut)
def get_current(lot_id: str):
    return db.get_current(lot_id)

# ---------- Get occupancy history ----------
@app.get("/api/occupancy/{lot_id}/history", response_model=List[OccupancyOut])
def get_history(lot_id: str, minutes: int = 60):
    return db.get_history(lot_id, minutes)

# ---------- Ingest detection data from edge ----------
@app.post("/api/ingest/detections")
def ingest_detection(d: DetectionIn):
    return db.ingest_detection(d)

# ---------- Get system status ----------
@app.get("/api/status", response_model=SystemStatus)
def get_status():
    return db.get_status()

# ---------- Get forecasts ----------
@app.get("/api/forecasts/{lot_id}", response_model=List[ForecastOut])
def get_forecasts(lot_id: str):
    return db.get_forecasts(lot_id)

# ---------- Create or update a parking lot ----------
@app.post("/api/lots", response_model=LotOut, status_code=status.HTTP_201_CREATED)
def create_lot(lot: LotIn):
    return db.create_lot(lot)

# ---------- List all parking lots ----------
@app.get("/api/lots", response_model=List[LotOut])
def list_lots():
    return db.list_lots()

# ---------- Get details of a specific parking lot ----------
@app.get("/api/lots/{lot_id}", response_model=LotOut)
def get_lot(lot_id: str):
    return db.get_lot(lot_id)

# ---------- Delete a parking lot ----------
@app.delete("/api/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(lot_id: str):
    return db.delete_lot(lot_id)

# ---------- Create or update a device ----------
@app.post("/api/devices", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def create_device(device: DeviceIn):        
    return db.create_device(device)

# ---------- List all devices ----------
@app.get("/api/devices", response_model=List[DeviceOut])
def list_devices():
    return db.list_devices()

# ---------- Get details of a specific device ----------
@app.get("/api/devices/{device_id}", response_model=DeviceOut)
def get_device(device_id: str):
    return db.get_device(device_id)

# ---------- Delete a device ----------
@app.delete("/api/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: str):
    return db.delete_device(device_id)      
