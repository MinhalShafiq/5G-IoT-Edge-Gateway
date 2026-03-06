"""Feature engineering for anomaly detection.

Extracts numeric features from telemetry readings and augments them with
sliding-window statistical features (mean, std, min, max) per device.
"""

import numpy as np

from shared.models.telemetry import TelemetryReading
from shared.observability.logging_config import get_logger

logger = get_logger(__name__)


class FeatureExtractor:
    """Extract features from telemetry readings for anomaly detection.

    Maintains a sliding window of recent readings per device to compute
    statistical features (mean, standard deviation, min, max) that give
    the model temporal context beyond a single reading.
    """

    def __init__(self, window_size: int = 10):
        self._window_size = window_size
        self._windows: dict[str, list[dict]] = {}  # device_id -> recent payloads

    def extract(self, reading: TelemetryReading) -> np.ndarray:
        """Extract features from a telemetry reading.

        The feature vector consists of:
        1. Current numeric values from the reading payload.
        2. Statistical features (mean, std, min, max) computed over the
           sliding window of recent readings for this device.

        Args:
            reading: A telemetry reading from an IoT device.

        Returns:
            A numpy array of shape (1, n_features) suitable for ONNX inference.
        """
        device_key = str(reading.device_id)

        # Update the sliding window for this device
        if device_key not in self._windows:
            self._windows[device_key] = []

        self._windows[device_key].append(reading.payload)
        if len(self._windows[device_key]) > self._window_size:
            self._windows[device_key] = self._windows[device_key][
                -self._window_size :
            ]

        # Extract numeric values from the current payload (exclude bools and metadata)
        features = []
        _skip = {"anomaly", "anomaly_type", "unit"}
        for key, value in reading.payload.items():
            if key in _skip or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                features.append(float(value))

        # Add statistical features from the sliding window
        if len(self._windows[device_key]) > 1:
            window_values = []
            for past_payload in self._windows[device_key]:
                for k, v in past_payload.items():
                    if k in _skip or isinstance(v, bool):
                        continue
                    if isinstance(v, (int, float)):
                        window_values.append(float(v))

            if window_values:
                features.extend(
                    [
                        np.mean(window_values),
                        np.std(window_values),
                        np.min(window_values),
                        np.max(window_values),
                    ]
                )
            else:
                features.extend([0.0] * 4)
        else:
            # First reading for this device -- use current values as placeholders
            fill_value = features[0] if features else 0.0
            features.extend([fill_value] * 4)

        # Pad or truncate to exactly 8 features to match the ONNX model input
        target_len = 8
        if len(features) < target_len:
            features.extend([0.0] * (target_len - len(features)))
        elif len(features) > target_len:
            features = features[:target_len]

        return np.array(features, dtype=np.float32).reshape(1, -1)

    def clear_window(self, device_id: str) -> None:
        """Clear the sliding window for a specific device."""
        self._windows.pop(device_id, None)

    def clear_all(self) -> None:
        """Clear all sliding windows."""
        self._windows.clear()

    @property
    def tracked_devices(self) -> int:
        """Number of devices currently tracked in sliding windows."""
        return len(self._windows)
