"""Feature engineering for anomaly detection.

Turns a telemetry reading into a fixed-length, scale-free feature vector:

    [z_a, z_b, dev_a, dev_b, is_temperature, is_pressure, is_vibration]

* ``z_*``   -- the two sensor values standardised with per-device-type
               statistics learned at training time (so a 1013 hPa pressure
               and a 23 degC temperature land on the same scale).
* ``dev_*`` -- deviation of each value from the *median* of the device's
               recent readings, in the same standardised units. The median
               keeps a single spike from polluting the next N readings.
               Zero until the device has ``MIN_HISTORY`` readings.
* one-hot   -- the device type, so the model can learn per-type structure.

The per-type statistics ship with the model (``<model>.json``), so the
extractor used for training and the one used for serving are identical.
"""

from collections import deque
from statistics import median

import numpy as np

from shared.models.telemetry import TelemetryReading
from shared.observability.logging_config import get_logger

logger = get_logger(__name__)

# Payload fields used per device type, in feature order.
SENSOR_FIELDS: dict[str, tuple[str, str]] = {
    "temperature_sensor": ("temperature_celsius", "humidity_percent"),
    "pressure_sensor": ("pressure_hpa", "flow_rate_lpm"),
    "vibration_sensor": ("rms_velocity_mm_s", "peak_frequency_hz"),
}
DEVICE_TYPES: tuple[str, ...] = tuple(SENSOR_FIELDS)

FEATURE_NAMES: tuple[str, ...] = (
    "z_a",
    "z_b",
    "dev_a",
    "dev_b",
    *(f"is_{t.split('_')[0]}" for t in DEVICE_TYPES),
)
N_FEATURES = len(FEATURE_NAMES)

# A median over fewer readings is too noisy to compare against; until a
# device has this much history its dev_* features are 0.
MIN_HISTORY = 5

# Keeps a corrupted payload from producing inf/NaN inside the model.
_FEATURE_CLIP = 1_000.0
_SKIP_KEYS = {"anomaly", "anomaly_type", "unit"}

# feature_stats layout: {device_type: {field: [mean, std]}}
FeatureStats = dict[str, dict[str, list[float]]]


class FeatureExtractor:
    """Extract model features from telemetry readings.

    Maintains a sliding window of recent values per device to compute the
    temporal ``dev_*`` features.
    """

    def __init__(self, window_size: int = 10, feature_stats: FeatureStats | None = None):
        self._window_size = window_size
        self._stats: FeatureStats = feature_stats or {}
        self._windows: dict[str, deque[tuple[float, float]]] = {}

    def set_stats(self, feature_stats: FeatureStats) -> None:
        """Replace the per-type normalisation statistics (e.g. after a model swap)."""
        self._stats = feature_stats

    def _values(self, reading: TelemetryReading) -> tuple[float, float]:
        """Pick the two sensor values for this reading's device type."""
        payload = reading.payload
        fields = SENSOR_FIELDS.get(reading.device_type)
        if fields and all(f in payload for f in fields):
            return float(payload[fields[0]]), float(payload[fields[1]])

        # Unknown device type: fall back to the first two numeric fields.
        numeric = [
            float(v)
            for k, v in payload.items()
            if k not in _SKIP_KEYS
            and isinstance(v, (int, float))
            and not isinstance(v, bool)
        ]
        numeric += [0.0] * (2 - len(numeric))
        return numeric[0], numeric[1]

    def _scale(self, device_type: str, index: int) -> tuple[float, float]:
        """Return (mean, std) for one sensor field, or an identity scale."""
        fields = SENSOR_FIELDS.get(device_type)
        type_stats = self._stats.get(device_type)
        if not fields or not type_stats:
            return 0.0, 1.0
        mean, std = type_stats[fields[index]]
        return mean, max(std, 1e-6)

    def extract_row(self, reading: TelemetryReading) -> list[float]:
        """Extract the feature vector for one reading as a plain list."""
        device_key = str(reading.device_id)
        a, b = self._values(reading)
        mean_a, std_a = self._scale(reading.device_type, 0)
        mean_b, std_b = self._scale(reading.device_type, 1)

        window = self._windows.get(device_key)
        if window is None:
            window = self._windows[device_key] = deque(maxlen=self._window_size)

        if len(window) >= MIN_HISTORY:
            dev_a = (a - median(v[0] for v in window)) / std_a
            dev_b = (b - median(v[1] for v in window)) / std_b
        else:
            dev_a = dev_b = 0.0
        window.append((a, b))

        if reading.device_type in SENSOR_FIELDS:
            z_a = (a - mean_a) / std_a
            z_b = (b - mean_b) / std_b
        else:
            # No learned scale for unknown types; rely on the temporal features.
            z_a = z_b = 0.0

        one_hot = [1.0 if reading.device_type == t else 0.0 for t in DEVICE_TYPES]
        row = [z_a, z_b, dev_a, dev_b, *one_hot]
        return [max(-_FEATURE_CLIP, min(_FEATURE_CLIP, x)) for x in row]

    def extract(self, reading: TelemetryReading) -> np.ndarray:
        """Extract features as a numpy array of shape (1, N_FEATURES)."""
        return np.asarray([self.extract_row(reading)], dtype=np.float32)

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
