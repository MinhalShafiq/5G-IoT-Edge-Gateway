"""CoAP transport — publishes telemetry via CoAP POST to the data-ingestion service."""

import asyncio
import json
import logging
import threading

import aiocoap

logger = logging.getLogger(__name__)

# How many CoAP requests may be in flight at once. aiocoap handles
# retransmission; this only bounds memory and socket pressure.
_MAX_IN_FLIGHT = 256
_REQUEST_TIMEOUT_SECONDS = 10.0


class CoapPublisher:
    """Publishes telemetry readings via CoAP.

    ``publish`` is called from worker threads (the fleet manager offloads it
    with ``run_in_executor``), while aiocoap needs a single long-lived event
    loop. The publisher owns that loop on a dedicated background thread and
    submits each request to it with ``run_coroutine_threadsafe``.
    """

    def __init__(self, base_uri: str = "coap://localhost:5683"):
        self._endpoint = f"{base_uri.rstrip('/')}/telemetry"
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="coap-loop", daemon=True
        )
        self._context: aiocoap.Context | None = None
        self._semaphore: asyncio.Semaphore | None = None

    def connect(self, host: str | None = None, port: int | None = None) -> None:
        """Start the event loop thread and create the CoAP client context."""
        if host and port:
            self._endpoint = f"coap://{host}:{port}/telemetry"
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._init(), self._loop).result()
        logger.info("CoAP publisher configured: %s", self._endpoint)

    async def _init(self) -> None:
        self._context = await aiocoap.Context.create_client_context()
        self._semaphore = asyncio.Semaphore(_MAX_IN_FLIGHT)

    def publish(self, device_id: str, telemetry: dict) -> bool:
        """Send a telemetry reading via CoAP POST, blocking until acknowledged."""
        future = asyncio.run_coroutine_threadsafe(
            self._async_publish(device_id, telemetry), self._loop
        )
        try:
            return future.result(timeout=_REQUEST_TIMEOUT_SECONDS + 1)
        except Exception as e:
            logger.error("CoAP publish failed for %s: %s", device_id, e)
            return False

    async def _async_publish(self, device_id: str, telemetry: dict) -> bool:
        request = aiocoap.Message(
            code=aiocoap.POST,
            uri=self._endpoint,
            payload=json.dumps(telemetry).encode("utf-8"),
            content_format=50,  # application/json
        )
        async with self._semaphore:
            response = await asyncio.wait_for(
                self._context.request(request).response, _REQUEST_TIMEOUT_SECONDS
            )
        if response.code.is_successful():
            return True
        logger.warning("CoAP error for device %s: %s", device_id, response.code)
        return False

    def disconnect(self) -> None:
        """Shut down the CoAP context and stop the loop thread."""
        if self._context is not None:
            asyncio.run_coroutine_threadsafe(
                self._context.shutdown(), self._loop
            ).result(timeout=5)
            self._context = None
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        logger.info("CoAP publisher disconnected")
