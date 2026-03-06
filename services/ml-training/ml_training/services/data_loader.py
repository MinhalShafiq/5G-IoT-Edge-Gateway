"""Telemetry data loader for training.

Loads aggregated sensor data from PostgreSQL and converts it into numpy
arrays suitable for model training.
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

logger = structlog.get_logger(__name__)


class TelemetryDataLoader:
    """Load telemetry readings from PostgreSQL and prepare training datasets."""

    def __init__(self, postgres_dsn: str):
        self._dsn = postgres_dsn

    async def load_training_data(
        self,
        device_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 50000,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Load telemetry data from PostgreSQL and convert to numpy arrays.

        Queries the ``telemetry_readings`` table, extracts numeric features
        from the JSON payload column, and splits into train/validation sets
        with an 80/20 ratio.

        Returns:
            Tuple of (X_train, X_val) as float32 numpy arrays.
        """
        engine = create_async_engine(self._dsn, pool_size=5)

        try:
            async with engine.connect() as conn:
                # Build dynamic query with optional filters
                conditions: list[str] = []
                params: dict = {"limit": limit}

                if device_type:
                    conditions.append("device_type = :device_type")
                    params["device_type"] = device_type

                if start_time:
                    conditions.append("timestamp >= :start_time")
                    params["start_time"] = start_time

                if end_time:
                    conditions.append("timestamp <= :end_time")
                    params["end_time"] = end_time

                where_clause = ""
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)

                query = text(
                    f"SELECT payload FROM telemetry_readings "
                    f"{where_clause} "
                    f"ORDER BY timestamp DESC LIMIT :limit"
                )

                result = await conn.execute(query, params)
                rows = result.fetchall()

            logger.info("loaded telemetry rows", count=len(rows))

            if len(rows) == 0:
                raise ValueError("No telemetry data found matching the query filters")

            # Extract numeric features from JSON payloads
            features = self._extract_features(rows)

            # Split 80/20 train/validation
            split_idx = int(len(features) * 0.8)
            x_train = features[:split_idx]
            x_val = features[split_idx:]

            # Ensure we have at least some data in each split
            if len(x_train) == 0 or len(x_val) == 0:
                split_idx = max(1, len(features) - 1)
                x_train = features[:split_idx]
                x_val = features[split_idx:]

            logger.info(
                "training data prepared",
                n_train=len(x_train),
                n_val=len(x_val),
                n_features=x_train.shape[1] if len(x_train.shape) > 1 else 1,
            )

            return x_train, x_val

        finally:
            await engine.dispose()

    def _extract_features(self, rows: list) -> np.ndarray:
        """Convert rows of JSON payloads to a feature matrix.

        Extracts all numeric values from each JSON payload and pads/truncates
        to a consistent feature width. Non-numeric values are ignored.
        """
        all_features: list[list[float]] = []

        for row in rows:
            payload = row[0]  # payload column
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    continue

            if not isinstance(payload, dict):
                continue

            # Extract all numeric values from the payload
            numeric_values: list[float] = []
            for value in payload.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric_values.append(float(value))
                elif isinstance(value, dict):
                    # Handle nested dicts (e.g., {"temperature": {"value": 22.5}})
                    for v in value.values():
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            numeric_values.append(float(v))

            if numeric_values:
                all_features.append(numeric_values)

        if not all_features:
            raise ValueError("No numeric features could be extracted from payloads")

        # Pad/truncate to consistent width (use max observed width, capped at 16)
        max_width = min(max(len(f) for f in all_features), 16)
        padded: list[list[float]] = []
        for f in all_features:
            if len(f) >= max_width:
                padded.append(f[:max_width])
            else:
                padded.append(f + [0.0] * (max_width - len(f)))

        result = np.array(padded, dtype=np.float32)

        # Normalize features (zero-mean, unit-variance) to improve training
        mean = result.mean(axis=0, keepdims=True)
        std = result.std(axis=0, keepdims=True)
        std[std == 0] = 1.0  # Avoid division by zero
        result = (result - mean) / std

        return result
