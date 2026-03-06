"""Request logging middleware.

Logs every HTTP request/response pair with method, path, status code,
latency, correlation ID, client IP, and user-agent using structlog.
Also updates Prometheus counters and latency histograms.
"""

from __future__ import annotations

import time

import structlog
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("middleware.request_logger")

# Prometheus metrics scoped to the gateway
HTTP_REQUEST_COUNT = Counter(
    "gateway_http_requests_total",
    "Total HTTP requests handled by the gateway",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_LATENCY = Histogram(
    "gateway_http_request_latency_seconds",
    "Request latency in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Log every request/response and emit Prometheus metrics."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()

        response: Response = await call_next(request)

        latency = time.perf_counter() - start
        latency_ms = round(latency * 1000, 2)

        # Normalise path for Prometheus labels (avoid cardinality explosion)
        path_label = self._normalise_path(request.url.path)
        method = request.method
        status_code = response.status_code

        # Prometheus
        HTTP_REQUEST_COUNT.labels(
            method=method,
            path=path_label,
            status_code=str(status_code),
        ).inc()
        HTTP_REQUEST_LATENCY.labels(method=method, path=path_label).observe(latency)

        # Structured log
        correlation_id = getattr(request.state, "correlation_id", None)
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")

        logger.info(
            "http_request",
            method=method,
            path=request.url.path,
            status_code=status_code,
            latency_ms=latency_ms,
            correlation_id=correlation_id,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        return response

    @staticmethod
    def _normalise_path(path: str) -> str:
        """Collapse dynamic path segments to reduce Prometheus label cardinality.

        For example ``/api/v1/devices/abc-123`` becomes ``/api/v1/devices/:id``.
        """
        parts = path.strip("/").split("/")
        normalised: list[str] = []
        for part in parts:
            # Heuristic: if the segment looks like an ID, replace it
            if len(part) > 20 or (
                any(c.isdigit() for c in part) and any(c == "-" for c in part)
            ):
                normalised.append(":id")
            else:
                normalised.append(part)
        return "/" + "/".join(normalised)
