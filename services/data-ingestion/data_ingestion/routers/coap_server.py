"""CoAP server — UDP telemetry ingestion for constrained devices (RFC 7252)."""

import aiocoap
import aiocoap.resource as resource
from shared.models.telemetry import TelemetryReading
from shared.observability.logging_config import get_logger
from data_ingestion.services import ingestion_service
from data_ingestion.codecs.cbor_codec import CborCodec
from data_ingestion.codecs.json_codec import JsonCodec

logger = get_logger(__name__)

# CoAP content-format identifiers
CONTENT_FORMAT_JSON = 50
CONTENT_FORMAT_CBOR = 60


class TelemetryResource(resource.Resource):
    """CoAP resource that accepts telemetry readings via PUT/POST."""

    def __init__(self, redis_client, settings):
        super().__init__()
        self.redis_client = redis_client
        self.settings = settings

    async def render_post(self, request):
        """Handle POST requests containing telemetry data."""
        try:
            payload = request.payload
            if not payload:
                return aiocoap.Message(
                    code=aiocoap.numbers.codes.Code.BAD_REQUEST,
                    payload=b"Empty payload",
                )

            # Determine content format from the request option
            content_format = request.opt.content_format

            if content_format == CONTENT_FORMAT_CBOR:
                data = CborCodec.decode(payload)
            else:
                # Default to JSON
                data = JsonCodec.decode(payload)

            reading = TelemetryReading(**data)

            logger.debug(
                "coap_telemetry_received",
                device_id=str(reading.device_id),
            )

            await ingestion_service.process(
                reading=reading,
                redis_client=self.redis_client,
                protocol="coap",
                stream_max_len=self.settings.stream_max_len,
            )

            return aiocoap.Message(
                code=aiocoap.numbers.codes.Code.CREATED,
                payload=b"Created",
            )

        except Exception as e:
            logger.warning("coap_telemetry_rejected", error=str(e))
            return aiocoap.Message(
                code=aiocoap.numbers.codes.Code.BAD_REQUEST,
                payload=str(e).encode("utf-8"),
            )

    async def render_put(self, request):
        """Handle PUT requests by delegating to POST handler."""
        return await self.render_post(request)


async def create_coap_server(settings, redis_client):
    """Build and start the CoAP server with the telemetry resource tree.

    Resources:
        /telemetry          -- accepts telemetry readings (PUT/POST)
        /.well-known/core   -- CoAP discovery
    """
    root = resource.Site()

    # CoAP discovery endpoint
    root.add_resource(
        [".well-known", "core"],
        resource.WKCResource(root.get_resources_as_linkheader),
    )

    # Telemetry ingestion endpoint
    root.add_resource(
        ["telemetry"],
        TelemetryResource(redis_client, settings),
    )

    bind_address = ("::", settings.coap_port)
    logger.info("coap_server_starting", bind=bind_address)

    context = await aiocoap.Context.create_server_context(root, bind=bind_address)
    return context
