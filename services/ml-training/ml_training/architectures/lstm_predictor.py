"""LSTM predictor for time-series forecasting.

Predicts the next sensor reading from a sequence of historical readings.
Anomalies are detected when the actual reading deviates significantly
from the LSTM prediction.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class LSTMPredictor(nn.Module):
    """LSTM-based time-series predictor for IoT sensor data.

    Takes a sequence of sensor readings and predicts the next time step.
    Uses a multi-layer LSTM followed by a fully-connected output layer.

    Args:
        input_dim: Number of features per time step.
        hidden_dim: Number of LSTM hidden units.
        num_layers: Number of stacked LSTM layers.
        output_dim: Number of output features (typically same as input_dim).
        dropout: Dropout probability between LSTM layers (only used when
                 num_layers > 1).
    """

    def __init__(
        self,
        input_dim: int = 8,
        hidden_dim: int = 32,
        num_layers: int = 2,
        output_dim: int = 8,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.output_dim = output_dim

        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )

        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass through LSTM and output layer.

        Args:
            x: Input tensor of shape (batch_size, seq_len, input_dim).

        Returns:
            Prediction tensor of shape (batch_size, output_dim) — the
            predicted values for the next time step.
        """
        # lstm_out shape: (batch_size, seq_len, hidden_dim)
        lstm_out, (h_n, c_n) = self.lstm(x)

        # Use only the output from the last time step
        last_output = lstm_out[:, -1, :]

        # Project to output dimension
        prediction = self.fc(last_output)
        return prediction

    def predict_sequence(
        self, x: torch.Tensor, n_steps: int = 1
    ) -> torch.Tensor:
        """Auto-regressively predict multiple future time steps.

        Args:
            x: Input sequence of shape (batch_size, seq_len, input_dim).
            n_steps: Number of future steps to predict.

        Returns:
            Predictions tensor of shape (batch_size, n_steps, output_dim).
        """
        predictions: list[torch.Tensor] = []
        current_input = x

        for _ in range(n_steps):
            pred = self.forward(current_input)  # (batch, output_dim)
            predictions.append(pred.unsqueeze(1))

            # Append prediction as the next input step
            current_input = torch.cat(
                [current_input[:, 1:, :], pred.unsqueeze(1)], dim=1
            )

        return torch.cat(predictions, dim=1)
