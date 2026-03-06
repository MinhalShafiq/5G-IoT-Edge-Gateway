"""Reverse proxy router.

Forwards requests from the public API surface to internal micro-services,
preserving method, headers, body, and query parameters.  Adds correlation
and forwarding headers.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/api/v1", tags=["proxy"])
logger = structlog.get_logger(__name__)

# Mapping of path prefix -> (settings attribute for upstream URL, strip prefix)
ROUTE_MAP: dict[str, str] = {
    "devices": "upstream_device_manager",
    "telemetry": "upstream_data_ingestion",
    "inference": "upstream_ml_inference",
    "scheduler": "upstream_scheduler",
}

# Headers that should NOT be forwarded to upstream services
HOP_BY_HOP_HEADERS = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


async def _proxy(request: Request, upstream_base: str, path: str) -> Response:
    """Generic reverse-proxy helper.

    Forwards the request to *upstream_base/api/v1/<path>* and returns
    the upstream response verbatim to the caller.
    """
    http_client = request.app.state.http_client

    # Build target URL
    url = f"{upstream_base}/api/v1/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    # Forward headers (filter hop-by-hop)
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

    # Inject gateway headers
    correlation_id = getattr(request.state, "correlation_id", None)
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id

    client_host = request.client.host if request.client else "unknown"
    existing_forwarded = headers.get("X-Forwarded-For", "")
    headers["X-Forwarded-For"] = (
        f"{existing_forwarded}, {client_host}" if existing_forwarded else client_host
    )

    # Read body
    body = await request.body()

    logger.debug(
        "proxy_request",
        method=request.method,
        target_url=url,
        correlation_id=correlation_id,
    )

    upstream_response = await http_client.request(
        method=request.method,
        url=url,
        headers=headers,
        content=body if body else None,
    )

    # Build response, forwarding status, headers, and body
    excluded_response_headers = {"content-encoding", "content-length", "transfer-encoding"}
    response_headers = {
        k: v
        for k, v in upstream_response.headers.items()
        if k.lower() not in excluded_response_headers
    }

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


# ---- Route definitions ----
# We define a catch-all route for each upstream service prefix.

@router.api_route(
    "/devices/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Proxy to Device Manager",
)
async def proxy_devices(request: Request, path: str) -> Response:
    settings = request.app.state.settings
    return await _proxy(request, settings.upstream_device_manager, f"devices/{path}")


@router.api_route(
    "/telemetry/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Proxy to Data Ingestion",
)
async def proxy_telemetry(request: Request, path: str) -> Response:
    settings = request.app.state.settings
    return await _proxy(request, settings.upstream_data_ingestion, f"telemetry/{path}")


@router.api_route(
    "/inference/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Proxy to ML Inference",
)
async def proxy_inference(request: Request, path: str) -> Response:
    settings = request.app.state.settings
    return await _proxy(request, settings.upstream_ml_inference, f"inference/{path}")


@router.api_route(
    "/scheduler/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Proxy to Scheduler",
)
async def proxy_scheduler(request: Request, path: str) -> Response:
    settings = request.app.state.settings
    return await _proxy(request, settings.upstream_scheduler, f"scheduler/{path}")
