"""End-to-end test: Full pipeline from simulator to edge to cloud.

Requires all services running (docker-compose up).
"""

import json
import os
from datetime import datetime
from uuid import uuid4

import httpx
import pytest

# Service endpoints — env vars allow override when running inside Docker
DATA_INGESTION_URL = os.environ.get("DATA_INGESTION_URL", "http://localhost:8001")
CLOUD_API_URL = os.environ.get("CLOUD_API_URL", "http://localhost:8080")

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
class TestFullPipeline:
    """End-to-end: simulator -> ingestion -> Redis -> persistence -> cloud."""

    async def test_telemetry_ingestion_via_rest(self):
        """Post telemetry via REST and verify it's accepted."""
        async with httpx.AsyncClient(timeout=10) as client:
            reading = {
                "device_id": str(uuid4()),
                "device_type": "temperature_sensor",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {"temperature": 23.5, "humidity": 45.0},
                "metadata": {"firmware_version": "1.0.0"},
            }
            resp = await client.post(
                f"{DATA_INGESTION_URL}/api/v1/telemetry", json=reading
            )
            assert resp.status_code == 202

    async def test_telemetry_stats_endpoint(self):
        """The stats endpoint should return stream information."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DATA_INGESTION_URL}/api/v1/telemetry/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert "stream_length" in data

    async def test_health_checks(self):
        """All service health endpoints should return ok."""
        endpoints = [
            f"{DATA_INGESTION_URL}/health",
        ]
        async with httpx.AsyncClient(timeout=10) as client:
            for endpoint in endpoints:
                try:
                    resp = await client.get(endpoint)
                    assert resp.status_code == 200
                    assert resp.json()["status"] == "ok"
                except httpx.ConnectError:
                    pytest.skip(f"Service not running: {endpoint}")

    async def test_batch_telemetry_to_cloud(self):
        """Send a batch of readings to the cloud API."""
        async with httpx.AsyncClient(timeout=10) as client:
            batch = {
                "readings": [
                    {
                        "device_id": str(uuid4()),
                        "device_type": "temperature_sensor",
                        "timestamp": datetime.utcnow().isoformat(),
                        "payload": {"temperature": 22.0 + i},
                        "metadata": {},
                    }
                    for i in range(10)
                ]
            }
            try:
                resp = await client.post(
                    f"{CLOUD_API_URL}/api/v1/telemetry/batch", json=batch
                )
                assert resp.status_code == 202
                data = resp.json()
                assert data["accepted_count"] == 10
            except httpx.ConnectError:
                pytest.skip("Cloud API not running")
