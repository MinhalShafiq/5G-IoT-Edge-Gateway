"""Locust load test for the IoT Edge Gateway.

Simulates thousands of IoT devices sending telemetry to the data-ingestion service.

Usage:
    locust -f tests/load/locustfile.py --host http://localhost:8001
"""

import json
import random
from datetime import datetime
from uuid import uuid4

from locust import HttpUser, between, task


class IoTDeviceUser(HttpUser):
    """Simulates an IoT device sending periodic telemetry."""

    wait_time = between(1, 5)

    def on_start(self):
        """Initialize device identity on spawn."""
        self.device_id = str(uuid4())
        self.device_type = random.choice([
            "temperature_sensor",
            "vibration_sensor",
            "pressure_sensor",
        ])
        self.reading_count = 0

    @task(weight=10)
    def send_telemetry(self):
        """Send a normal telemetry reading."""
        payload = self._generate_payload()
        reading = {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "timestamp": datetime.utcnow().isoformat(),
            "payload": payload,
            "metadata": {
                "firmware_version": "1.0.0",
                "signal_strength": random.randint(-90, -30),
            },
        }
        self.client.post(
            "/api/v1/telemetry",
            json=reading,
            name=f"/api/v1/telemetry [{self.device_type}]",
        )
        self.reading_count += 1

    @task(weight=1)
    def send_anomalous_telemetry(self):
        """Send an anomalous reading (~10% of traffic)."""
        payload = self._generate_anomaly_payload()
        reading = {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "timestamp": datetime.utcnow().isoformat(),
            "payload": payload,
            "metadata": {"firmware_version": "1.0.0"},
        }
        self.client.post(
            "/api/v1/telemetry",
            json=reading,
            name="/api/v1/telemetry [anomaly]",
        )

    @task(weight=1)
    def check_stats(self):
        """Query telemetry stats."""
        self.client.get("/api/v1/telemetry/stats")

    @task(weight=1)
    def health_check(self):
        """Hit the health endpoint."""
        self.client.get("/health")

    def _generate_payload(self) -> dict:
        match self.device_type:
            case "temperature_sensor":
                return {
                    "temperature": round(20 + random.gauss(0, 2), 2),
                    "humidity": round(50 + random.gauss(0, 5), 2),
                }
            case "vibration_sensor":
                return {
                    "rms_velocity": round(random.uniform(0.5, 2.0), 3),
                    "peak_frequency": round(random.uniform(50, 200), 1),
                }
            case "pressure_sensor":
                return {
                    "pressure_hpa": round(1013.25 + random.gauss(0, 3), 2),
                    "flow_rate": round(random.uniform(10, 50), 1),
                }
            case _:
                return {"value": random.random()}

    def _generate_anomaly_payload(self) -> dict:
        match self.device_type:
            case "temperature_sensor":
                return {
                    "temperature": round(random.uniform(80, 120), 2),
                    "humidity": round(random.uniform(0, 10), 2),
                }
            case "vibration_sensor":
                return {
                    "rms_velocity": round(random.uniform(10, 50), 3),
                    "peak_frequency": round(random.uniform(800, 2000), 1),
                }
            case "pressure_sensor":
                return {
                    "pressure_hpa": round(random.uniform(500, 800), 2),
                    "flow_rate": round(random.uniform(80, 150), 1),
                }
            case _:
                return {"value": random.random() * 100}
