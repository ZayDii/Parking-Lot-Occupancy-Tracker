# backend/ml/data_loader.py
import os
from typing import List, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.preprocessing import MinMaxScaler

DATABASE_URL = os.getenv("DATABASE_URL")


# 1) Load .env just like db_sql.py does
if not os.getenv("DATABASE_URL"):
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    except ModuleNotFoundError:
        pass

DATABASE_URL = os.getenv("DATABASE_URL")


def get_engine():
    if DATABASE_URL is None:
        raise RuntimeError("DATABASE_URL env var is not set")
    return create_engine(DATABASE_URL)


def fetch_timeseries(
    lot_id: str,
    lookback_days: int = 30,
    freq_minutes: int = 15,
) -> pd.DataFrame:
    engine = get_engine()
    query = text(
        """
        SELECT ts AS ts_utc,
               occupied,
               total AS capacity
        FROM occupancy_snapshots
        WHERE lot_id = :lot_id
          AND ts >= NOW() AT TIME ZONE 'UTC' - INTERVAL :days
        ORDER BY ts ASC;
        """
    )

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"lot_id": lot_id, "days": f"{lookback_days} days"})

    if df.empty:
        return df

    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df.set_index("ts_utc", inplace=True)

    # Resample to regular interval (forward fill last known occupancy)
    rule = f"{freq_minutes}T"
    df = df.resample(rule).ffill()

    # Basic features
    df["available"] = df["capacity"] - df["occupied"]
    df["avail_ratio"] = df["available"] / df["capacity"].clip(lower=1)

    # Time features
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek

    # Cyclical encoding for hour/dow
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["dow_sin"] = np.sin(2 * np.pi * df["dow"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["dow"] / 7)

    return df


def build_sequences(
    df: pd.DataFrame,
    seq_len: int,
    horizons_hours: List[int],
    freq_minutes: int,
) -> Tuple[np.ndarray, np.ndarray, MinMaxScaler, MinMaxScaler, List[str]]:
    """
    Converts the dataframe into (X, y) suitable for LSTM.

    y has shape (N, 4) for horizons [2h,4h,6h,8h] of *avail_ratio*.
    """
    if df.empty:
        raise ValueError("No data to build sequences")

    feature_cols = [
        "avail_ratio",
        "occupied",
        "capacity",
        "hour_sin",
        "hour_cos",
        "dow_sin",
        "dow_cos",
    ]

    df_features = df[feature_cols].copy()

    # Scale features and target [0,1]
    feat_scaler = MinMaxScaler()
    targ_scaler = MinMaxScaler()

    scaled_features = feat_scaler.fit_transform(df_features.values)
    target_vals = df[["avail_ratio"]].values
    scaled_target = targ_scaler.fit_transform(target_vals)

    steps_per_hour = int(60 / freq_minutes)
    horizon_steps = [h * steps_per_hour for h in horizons_hours]
    max_h = max(horizon_steps)

    X, y = [], []
    for i in range(seq_len, len(df) - max_h):
        # sequence [i-seq_len, i)
        X.append(scaled_features[i - seq_len : i, :])
        # targets at future horizons
        y.append([scaled_target[i + hs, 0] for hs in horizon_steps])

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32)

    return X, y, feat_scaler, targ_scaler, feature_cols
