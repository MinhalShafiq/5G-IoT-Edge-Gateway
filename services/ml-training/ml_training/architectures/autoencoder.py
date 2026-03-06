"""Autoencoder architecture for anomaly detection.

An autoencoder learns to compress sensor data into a low-dimensional
latent space and reconstruct it. At inference time, high reconstruction
error indicates an anomaly because the model has only seen normal data
during training.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class Autoencoder(nn.Module):
    """Symmetric autoencoder for reconstruction-based anomaly detection.

    Architecture:
        Encoder: input_dim -> 16 -> encoding_dim  (with ReLU activations)
        Decoder: encoding_dim -> 16 -> input_dim

    Args:
        input_dim: Number of input features (sensor readings).
        encoding_dim: Dimensionality of the latent bottleneck.
    """

    def __init__(self, input_dim: int = 8, encoding_dim: int = 4) -> None:
        super().__init__()

        self.input_dim = input_dim
        self.encoding_dim = encoding_dim

        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 16),
            nn.ReLU(),
            nn.Linear(16, encoding_dim),
            nn.ReLU(),
        )

        self.decoder = nn.Sequential(
            nn.Linear(encoding_dim, 16),
            nn.ReLU(),
            nn.Linear(16, input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass: encode then decode.

        Args:
            x: Input tensor of shape (batch_size, input_dim).

        Returns:
            Reconstructed tensor of shape (batch_size, input_dim).
        """
        encoded = self.encoder(x)
        decoded = self.decoder(encoded)
        return decoded

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """Encode input to latent representation.

        Useful for extracting feature embeddings or computing anomaly
        scores based on latent-space distance.
        """
        return self.encoder(x)

    def get_reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        """Compute per-sample MSE reconstruction error.

        Args:
            x: Input tensor of shape (batch_size, input_dim).

        Returns:
            Tensor of shape (batch_size,) with MSE per sample.
        """
        reconstructed = self.forward(x)
        return torch.mean((x - reconstructed) ** 2, dim=1)
