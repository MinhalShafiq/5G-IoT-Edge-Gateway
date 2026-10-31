"""Unit tests for the ml-inference feature extractor."""

from uuid import uuid4

import pytest

from shared.models.telemetry import TelemetryReading
from ml_inference.services.feature_extractor import N_FEATURES, FeatureExtractor

STATS = {
    "temperature_sensor": {
        "temperature_celsius": [23.0, 3.5],
        "humidity_percent": [50.0, 1.0],
    },
    "pressure_sensor": {"pressure_hpa": [1013.0, 2.0], "flow_rate_lpm": [30.0, 1.0]},
    "vibration_sensor": {
        "rms_velocity_mm_s": [1.25, 0.1],
        "peak_frequency_hz": [125.0, 5.0],
    },
}


def _temp(device_id, temperature, humidity=50.0):
    return TelemetryReading(
        device_id=device_id,
        device_type="temperature_sensor",
        payload={
            "temperature_celsius": temperature,
            "humidity_percent": humidity,
            "unit": "celsius",
            "anomaly": False,
        },
    )


def test_standardises_with_per_type_stats():
    row = FeatureExtractor(feature_stats=STATS).extract_row(_temp(uuid4(), 30.0, 52.0))
    assert len(row) == N_FEATURES
    assert row[0] == pytest.approx(2.0)  # (30 - 23) / 3.5
    assert row[1] == pytest.approx(2.0)  # (52 - 50) / 1
    assert row[2:4] == [0.0, 0.0]  # not enough history yet
    assert row[4:] == [1.0, 0.0, 0.0]  # temperature one-hot


def test_deviation_uses_robust_median():
    extractor = FeatureExtractor(feature_stats=STATS)
    device = uuid4()
    for _ in range(5):
        extractor.extract_row(_temp(device, 23.0))
    extractor.extract_row(_temp(device, 110.0))  # spike enters the window

    # The reading after the spike must not look anomalous because of it.
    row = extractor.extract_row(_temp(device, 23.0))
    assert row[2] == pytest.approx(0.0)


def test_unknown_device_type_does_not_crash():
    reading = TelemetryReading(
        device_id=uuid4(), device_type="gas_sensor", payload={"ppm": 400, "unit": "ppm"}
    )
    row = FeatureExtractor(feature_stats=STATS).extract_row(reading)
    assert len(row) == N_FEATURES
    assert row[4:] == [0.0, 0.0, 0.0]
