"""REST transport — publishes telemetry via HTTP POST to the data-ingestion service."""

import json
import logging
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)


class RestPublisher:
    """Publishes telemetry readings via HTTP POST."""

    def __init__(self, base_url: str = "http://localhost:8001"):
        self._base_url = base_url.rstrip("/")
        self._endpoint = f"{self._base_url}/api/v1/telemetry"
        self._connected = False

    def connect(self, host: str | None = None, port: int | None = None) -> None:
        """Set the target endpoint. Optionally override host/port."""
        if host and port:
            self._base_url = f"http://{host}:{port}"
            self._endpoint = f"{self._base_url}/api/v1/telemetry"
        self._connected = True
        logger.info("REST publisher configured: %s", self._endpoint)

    def publish(self, device_id: str, telemetry: dict) -> bool:
        """POST a telemetry reading as JSON to the ingestion service."""
        if not self._connected:
            logger.warning("REST publisher not connected")
            return False

        payload = json.dumps(telemetry).encode("utf-8")
        req = Request(
            self._endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201, 202):
                    return True
                logger.warning(
                    "Unexpected status %d for device %s", resp.status, device_id
                )
                return False
        except HTTPError as e:
            logger.error("HTTP error publishing %s: %s %s", device_id, e.code, e.reason)
            return False
        except URLError as e:
            logger.error("Connection error publishing %s: %s", device_id, e.reason)
            return False

    def disconnect(self) -> None:
        """No persistent connection to close for REST."""
        self._connected = False
        logger.info("REST publisher disconnected")
