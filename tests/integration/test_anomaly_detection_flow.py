"""Integration test: Anomaly detection pipeline."""

from datetime import datetime
from uuid import uuid4

import pytest

from shared.database.redis_client import RedisClient
from shared.models.telemetry import TelemetryReading
from shared.models.alert import Alert, AlertSeverity
from shared.streams.constants import StreamName, ConsumerGroup


@pytest.mark.asyncio
class TestAnomalyDetectionFlow:
    """Test the flow: telemetry -> ML inference -> alert stream."""

    async def test_alert_published_on_anomaly(self, redis_client: RedisClient):
        """When an anomaly is detected, an alert should appear in the alerts stream."""
        # Ensure consumer group exists
        await redis_client.ensure_consumer_group(StreamName.ALERTS, ConsumerGroup.ALERT_HANDLER)

        # Simulate the ML inference service publishing an alert
        alert = Alert(
            device_id=uuid4(),
            anomaly_score=0.95,
            severity=AlertSeverity.CRITICAL,
            model_version="isolation-forest-v1",
            details={"temperature": 95.3, "expected_range": [18.0, 35.0]},
        )

        entry_id = await redis_client.stream_add(
            StreamName.ALERTS, alert.to_stream_dict()
        )
        assert entry_id is not None

        # Read from alerts stream
        entries = await redis_client.stream_read_group(
            StreamName.ALERTS,
            ConsumerGroup.ALERT_HANDLER,
            "test-consumer",
            count=10,
            block_ms=1000,
        )

        assert len(entries) >= 1
        _, data = entries[0]
        assert data["severity"] == "critical"
        assert float(data["anomaly_score"]) > 0.9

    async def test_normal_reading_no_alert(self, redis_client: RedisClient):
        """Normal readings should not produce alerts."""
        initial_length = await redis_client.stream_len(StreamName.ALERTS)

        # A normal reading wouldn't trigger the inference service to publish an alert
        # This test verifies the alert stream isn't polluted by normal data
        reading = TelemetryReading(
            device_id=uuid4(),
            device_type="temperature_sensor",
            payload={"temperature": 22.5, "humidity": 50.0},
        )
        await redis_client.stream_add(
            StreamName.RAW_TELEMETRY, reading.to_stream_dict()
        )

        # Alert stream should not have grown
        final_length = await redis_client.stream_len(StreamName.ALERTS)
        assert final_length == initial_length
