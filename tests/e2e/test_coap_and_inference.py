"""End-to-end tests for CoAP ingestion and the served anomaly model.

Requires the stack running (docker compose up); skipped otherwise.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import aiocoap
import httpx
import pytest

DATA_INGESTION_URL = os.environ.get("DATA_INGESTION_URL", "http://localhost:8001")
ML_INFERENCE_URL = os.environ.get("ML_INFERENCE_URL", "http://localhost:8004")
COAP_HOST = os.environ.get("COAP_HOST", "localhost")

pytestmark = pytest.mark.e2e


def _reading(device_type: str, payload: dict) -> dict:
    return {
        "device_id": str(uuid4()),
        "device_type": device_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "metadata": {},
    }


async def _coap_received(client: httpx.AsyncClient) -> float:
    """Sum telemetry_received_total over all series with protocol="coap"."""
    text = (await client.get(f"{DATA_INGESTION_URL}/metrics")).text
    return sum(
        float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line.startswith("telemetry_received_total{") and 'protocol="coap"' in line
    )


@pytest.mark.asyncio
async def test_coap_post_is_ingested():
    reading = _reading(
        "temperature_sensor", {"temperature_celsius": 22.1, "humidity_percent": 49.0}
    )
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            before = await _coap_received(client)
        except httpx.ConnectError:
            pytest.skip("data-ingestion not running")

        context = await aiocoap.Context.create_client_context()
        try:
            request = aiocoap.Message(
                code=aiocoap.POST,
                uri=f"coap://{COAP_HOST}:5683/telemetry",
                payload=json.dumps(reading).encode(),
                content_format=50,
            )
            response = await asyncio.wait_for(context.request(request).response, 10)
        finally:
            await context.shutdown()

        assert response.code == aiocoap.CREATED
        assert await _coap_received(client) == before + 1


@pytest.mark.asyncio
async def test_served_model_is_pytorch_autoencoder_and_flags_spike():
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            info = (await client.get(f"{ML_INFERENCE_URL}/models/current")).json()
        except httpx.ConnectError:
            pytest.skip("ml-inference not reachable")
        assert info["is_loaded"]
        assert info["model_name"] == "anomaly-autoencoder"

        normal = await client.post(
            f"{ML_INFERENCE_URL}/predict",
            json=_reading(
                "vibration_sensor", {"rms_velocity_mm_s": 1.24, "peak_frequency_hz": 126.0}
            ),
        )
        spike = await client.post(
            f"{ML_INFERENCE_URL}/predict",
            json=_reading(
                "vibration_sensor", {"rms_velocity_mm_s": 35.0, "peak_frequency_hz": 125.0}
            ),
        )
        assert normal.json()["is_anomaly"] is False
        assert spike.json()["is_anomaly"] is True
        assert spike.json()["anomaly_score"] > normal.json()["anomaly_score"]
