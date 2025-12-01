# backend/ml/model_lstm.py
from dataclasses import dataclass
import torch
from torch import nn


@dataclass
class LSTMConfig:
    n_features: int
    hidden_size: int = 64
    num_layers: int = 2
    dropout: float = 0.2
    output_size: int = 4  # 2h, 4h, 6h, 8h


class OccupancyLSTM(nn.Module):
    """
    Input:  (batch, seq_len, n_features)
    Output: (batch, 4) -> [t+2h, t+4h, t+6h, t+8h] availability
    """

    def __init__(self, cfg: LSTMConfig):
        super().__init__()
        self.cfg = cfg

        self.lstm = nn.LSTM(
            input_size=cfg.n_features,
            hidden_size=cfg.hidden_size,
            num_layers=cfg.num_layers,
            batch_first=True,
            dropout=cfg.dropout if cfg.num_layers > 1 else 0.0,
        )

        self.fc = nn.Linear(cfg.hidden_size, cfg.output_size)

    def forward(self, x):
        # x: (B, T, F)
        out, _ = self.lstm(x)
        # Use last time step
        last_hidden = out[:, -1, :]  # (B, hidden)
        preds = self.fc(last_hidden)  # (B, 4)
        return preds
