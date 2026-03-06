"""Correlation ID middleware.

Reads an existing ``X-Correlation-ID`` header from the incoming request or
generates a new UUID-4 if one is not present.  The ID is attached to
``request.state.correlation_id``, added to every response header, and
bound to the structlog context so that all downstream log entries carry it.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

HEADER_NAME = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Ensure every request/response carries a correlation ID."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Read or generate
        correlation_id = request.headers.get(HEADER_NAME) or str(uuid.uuid4())

        # Attach to request state for downstream access
        request.state.correlation_id = correlation_id

        # Bind to structlog context so every log line includes it
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        try:
            response: Response = await call_next(request)
        finally:
            # Clear structlog context to avoid leaking between requests
            structlog.contextvars.unbind_contextvars("correlation_id")

        # Add to response headers
        response.headers[HEADER_NAME] = correlation_id
        return response
