# backend/ml/train_lstm.py
import os
from pathlib import Path
from typing import List

import joblib
import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset
from torch import nn, optim

from .data_loader import fetch_timeseries, build_sequences
from .model_lstm import OccupancyLSTM, LSTMConfig

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def train_for_lot(
    lot_id: str,
    lookback_days: int = 30,
    freq_minutes: int = 15,
    seq_len: int = 48,        # past 12h at 15-min
    horizons_hours: List[int] = [2, 4, 6, 8],
    batch_size: int = 64,
    epochs: int = 20,
    lr: float = 1e-3,
    device = None,
):
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")

    df = fetch_timeseries(lot_id, lookback_days=lookback_days, freq_minutes=freq_minutes)
    if df.empty or len(df) < seq_len + 40:
        print(f"[{lot_id}] Not enough data to train yet, skipping.")
        return

    X, y, feat_scaler, targ_scaler, feature_cols = build_sequences(
        df,
        seq_len=seq_len,
        horizons_hours=horizons_hours,
        freq_minutes=freq_minutes,
    )

    # Simple train/val split
    n = len(X)
    split = int(n * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

    cfg = LSTMConfig(n_features=X.shape[-1])
    model = OccupancyLSTM(cfg).to(device)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr)

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            preds = model(xb)
            loss = criterion(preds, yb)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * xb.size(0)

        train_loss /= len(train_loader.dataset)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                preds = model(xb)
                loss = criterion(preds, yb)
                val_loss += loss.item() * xb.size(0)
        val_loss /= len(val_loader.dataset)

        print(
            f"[{lot_id}] Epoch {epoch}/{epochs} "
            f"train_loss={train_loss:.4f} val_loss={val_loss:.4f}"
        )

    # Save artifacts
    lot_prefix = ARTIFACT_DIR / f"lot_{lot_id}"
    torch.save(model.state_dict(), lot_prefix.with_suffix(".pt"))

    joblib.dump(
        {
            "feat_scaler": feat_scaler,
            "targ_scaler": targ_scaler,
            "feature_cols": feature_cols,
            "seq_len": seq_len,
            "freq_minutes": freq_minutes,
            "horizons_hours": horizons_hours,
        },
        lot_prefix.with_suffix(".pkl"),
    )
    print(f"[{lot_id}] Saved model + scalers to {ARTIFACT_DIR}")


if __name__ == "__main__":
    lot_id = os.getenv("LOT_ID", "Lot96N")
    train_for_lot(lot_id)
