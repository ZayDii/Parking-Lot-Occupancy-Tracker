# backend/app/routers/forecast.py
from datetime import timedelta
from pathlib import Path
from typing import List

import joblib
import numpy as np
import pandas as pd
import torch
from fastapi import APIRouter, HTTPException

from ml.model_lstm import OccupancyLSTM, LSTMConfig
from ml.data_loader import fetch_timeseries

router = APIRouter(prefix="/forecast", tags=["forecast"])

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "ml" / "artifacts"


def load_model_for_lot(lot_id: str):
    lot_prefix = ARTIFACT_DIR / f"lot_{lot_id}"
    model_path = lot_prefix.with_suffix(".pt")
    meta_path = lot_prefix.with_suffix(".pkl")

    if not model_path.exists() or not meta_path.exists():
        raise FileNotFoundError("Model for this lot has not been trained yet")

    meta = joblib.load(meta_path)
    cfg = LSTMConfig(n_features=len(meta["feature_cols"]))
    model = OccupancyLSTM(cfg)
    model.load_state_dict(torch.load(model_path, map_location="cpu"))
    model.eval()
    return model, meta


@router.get("/{lot_id}")
def forecast_lot(lot_id: str):
    try:
        model, meta = load_model_for_lot(lot_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Model not trained yet")

    seq_len = meta["seq_len"]
    freq_minutes = meta["freq_minutes"]
    horizons_hours: List[int] = meta["horizons_hours"]
    feat_scaler = meta["feat_scaler"]
    targ_scaler = meta["targ_scaler"]

    # Get enough recent data for one sequence
    df = fetch_timeseries(
        lot_id,
        lookback_days=2,      # just needs recent window
        freq_minutes=freq_minutes,
    )
    if df.empty or len(df) < seq_len + 1:
        raise HTTPException(status_code=400, detail="Not enough recent data")

    df_recent = df.iloc[-seq_len:]
    feature_cols = meta["feature_cols"]
    feats = df_recent[feature_cols].values
    scaled_feats = feat_scaler.transform(feats)
    x = torch.from_numpy(scaled_feats.astype(np.float32))[None, :, :]  # (1,T,F)

    with torch.no_grad():
        preds_scaled = model(x).numpy()[0]  # (4,)

    # Inverse scale to avail_ratio
    preds_ratio = targ_scaler.inverse_transform(preds_scaled.reshape(-1, 1))[:, 0]

    last_row = df.iloc[-1]
    capacity = float(last_row["capacity"])
    last_ts = df.index[-1].to_pydatetime()

    results = {}
    for h, ratio in zip(horizons_hours, preds_ratio):
        # clamp between 0 and 1
        ratio = float(max(0.0, min(1.0, ratio)))
        available_pred = int(round(capacity * ratio))
        ts_pred = last_ts + timedelta(hours=h)
        results[f"{h}h"] = {
            "timestamp": ts_pred.isoformat(),
            "available_pred": available_pred,
            "avail_ratio_pred": ratio,
        }

    return {
        "lot_id": lot_id,
        "generated_at": last_ts.isoformat(),
        "capacity": int(capacity),
        "horizons": results,
    }
