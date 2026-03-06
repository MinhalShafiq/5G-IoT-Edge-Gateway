"""CoAP transport — publishes telemetry via CoAP PUT to the data-ingestion service."""

import json
import logging
import asyncio

logger = logging.getLogger(__name__)


class CoapPublisher:
    """Publishes telemetry readings via CoAP.

    Uses aiocoap for async CoAP client operations.
    Falls back to a no-op if aiocoap is not installed.
    """

    def __init__(self, base_uri: str = "coap://localhost:5683"):
        self._base_uri = base_uri.rstrip("/")
        self._endpoint = f"{self._base_uri}/telemetry"
        self._context = None
        self._available = True

        try:
            import aiocoap  # noqa: F401
        except ImportError:
            logger.warning(
                "aiocoap not installed — CoAP transport will be unavailable. "
                "Install with: pip install aiocoap"
            )
            self._available = False

    def connect(self, host: str | None = None, port: int | None = None) -> None:
        """Configure the CoAP target."""
        if host and port:
            self._base_uri = f"coap://{host}:{port}"
            self._endpoint = f"{self._base_uri}/telemetry"
        logger.info("CoAP publisher configured: %s", self._endpoint)

    def publish(self, device_id: str, telemetry: dict) -> bool:
        """Send a telemetry reading via CoAP POST (synchronous wrapper)."""
        if not self._available:
            logger.warning("CoAP not available, skipping publish for %s", device_id)
            return False

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule in the existing loop
                future = asyncio.ensure_future(
                    self._async_publish(device_id, telemetry)
                )
                # Can't block here; fire-and-forget in async context
                return True
            else:
                return loop.run_until_complete(
                    self._async_publish(device_id, telemetry)
                )
        except RuntimeError:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(
                self._async_publish(device_id, telemetry)
            )
            loop.close()
            return result

    async def _async_publish(self, device_id: str, telemetry: dict) -> bool:
        """Async CoAP POST."""
        import aiocoap

        payload = json.dumps(telemetry).encode("utf-8")
        request = aiocoap.Message(
            code=aiocoap.POST,
            uri=self._endpoint,
            payload=payload,
        )
        request.opt.content_format = 50  # application/json

        try:
            if self._context is None:
                self._context = await aiocoap.Context.create_client_context()

            response = await self._context.request(request).response

            if response.code.is_successful():
                return True

            logger.warning(
                "CoAP error for device %s: %s", device_id, response.code
            )
            return False
        except Exception as e:
            logger.error("CoAP publish failed for %s: %s", device_id, e)
            self._context = None  # Reset context on error
            return False

    def disconnect(self) -> None:
        """Clean up CoAP context."""
        if self._context is not None:
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    loop.run_until_complete(self._context.shutdown())
            except Exception:
                pass
            self._context = None
        logger.info("CoAP publisher disconnected")
