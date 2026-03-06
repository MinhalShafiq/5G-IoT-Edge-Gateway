"""Integration test: Full telemetry pipeline from ingestion to persistence."""

import json
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from shared.database.redis_client import RedisClient
from shared.streams.constants import StreamName, ConsumerGroup


@pytest.mark.asyncio
class TestTelemetryPipeline:
    """Test the data flow: HTTP POST -> Redis Stream -> persistence."""

    async def test_post_telemetry_returns_202(self, redis_client: RedisClient):
        """POST /api/v1/telemetry should accept a reading and return 202."""
        from data_ingestion.main import app
        from data_ingestion.config import Settings

        # Inject test dependencies into app state (lifespan doesn't run under ASGITransport)
        app.state.redis_client = redis_client
        app.state.settings = Settings()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reading = {
                "device_id": str(uuid4()),
                "device_type": "temperature_sensor",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {"temperature": 23.5, "humidity": 45.0},
                "metadata": {"firmware_version": "1.0.0"},
            }
            resp = await client.post("/api/v1/telemetry", json=reading)

        assert resp.status_code == 202
        data = resp.json()
        assert "stream_id" in data

    async def test_telemetry_appears_in_redis_stream(self, redis_client: RedisClient):
        """After posting telemetry, it should appear in the raw_telemetry stream."""
        device_id = str(uuid4())

        # Publish directly to stream (simulating ingestion service)
        from shared.models.telemetry import TelemetryReading

        reading = TelemetryReading(
            device_id=device_id,
            device_type="temperature_sensor",
            payload={"temperature": 25.0},
        )
        entry_id = await redis_client.stream_add(
            StreamName.RAW_TELEMETRY, reading.to_stream_dict()
        )

        assert entry_id is not None

        # Verify stream length
        length = await redis_client.stream_len(StreamName.RAW_TELEMETRY)
        assert length >= 1

    async def test_consumer_group_reads_telemetry(self, redis_client: RedisClient):
        """Consumer groups should be able to read from the telemetry stream."""
        # Ensure consumer group exists
        await redis_client.ensure_consumer_group(
            StreamName.RAW_TELEMETRY, ConsumerGroup.DATA_PERSISTENCE
        )

        # Add a reading
        from shared.models.telemetry import TelemetryReading

        reading = TelemetryReading(
            device_id=str(uuid4()),
            device_type="pressure_sensor",
            payload={"pressure": 1013.25, "flow_rate": 30.0},
        )
        await redis_client.stream_add(
            StreamName.RAW_TELEMETRY, reading.to_stream_dict()
        )

        # Read via consumer group
        entries = await redis_client.stream_read_group(
            StreamName.RAW_TELEMETRY,
            ConsumerGroup.DATA_PERSISTENCE,
            "test-consumer",
            count=10,
            block_ms=1000,
        )

        assert len(entries) >= 1
        entry_id, data = entries[0]
        assert data["device_type"] == "pressure_sensor"

        # Acknowledge
        acked = await redis_client.stream_ack(
            StreamName.RAW_TELEMETRY, ConsumerGroup.DATA_PERSISTENCE, entry_id
        )
        assert acked == 1
