"""Token-bucket / sliding-window rate limiter using Redis.

Uses a simple Redis ``INCR`` + ``EXPIRE`` pattern per client per window.
Different client types (device, user, admin) have different limits.
Responds with ``429 Too Many Requests`` and a ``Retry-After`` header when
the limit is exceeded.
"""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger("middleware.rate_limiter")

# Multiplier for the base rate-limit by role
ROLE_LIMIT_MULTIPLIERS: dict[str, float] = {
    "admin": 5.0,   # admins get 5x the base limit
    "user": 1.0,    # users get the base limit
    "device": 2.0,  # devices get 2x (typically automated, steady traffic)
}

# Paths excluded from rate limiting
RATE_LIMIT_EXCLUDED: set[str] = {"/health", "/ready", "/health/dependencies"}


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Sliding-window counter rate limiter backed by Redis."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if path in RATE_LIMIT_EXCLUDED:
            return await call_next(request)

        settings = request.app.state.settings
        redis_client = request.app.state.redis

        # Determine client identity
        client_id = self._get_client_id(request)
        role = self._get_client_role(request)

        # Calculate effective limit
        base_limit = settings.rate_limit_requests
        multiplier = ROLE_LIMIT_MULTIPLIERS.get(role, 1.0)
        effective_limit = int(base_limit * multiplier)
        window = settings.rate_limit_window_seconds

        # Current window key
        current_window = int(time.time()) // window
        key = f"ratelimit:{client_id}:{current_window}"

        try:
            # Atomic increment
            current_count = await redis_client.redis.incr(key)
            if current_count == 1:
                # First request in this window -- set expiry
                await redis_client.redis.expire(key, window + 1)

            if current_count > effective_limit:
                # Calculate how long until the window resets
                window_start = current_window * window
                retry_after = window_start + window - int(time.time())
                retry_after = max(retry_after, 1)

                logger.warning(
                    "rate_limit_exceeded",
                    client_id=client_id,
                    role=role,
                    current=current_count,
                    limit=effective_limit,
                )

                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "retry_after_seconds": retry_after,
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(effective_limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(window_start + window),
                    },
                )

            # Attach rate-limit info headers
            response: Response = await call_next(request)
            remaining = max(effective_limit - current_count, 0)
            response.headers["X-RateLimit-Limit"] = str(effective_limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(
                current_window * window + window
            )
            return response

        except Exception as exc:
            # If Redis is down, allow the request (fail-open) but log a warning
            logger.error("rate_limiter_redis_error", error=str(exc))
            return await call_next(request)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_client_id(request: Request) -> str:
        """Derive a stable client identifier from the request.

        Prefers the authenticated user subject; falls back to client IP.
        """
        user = getattr(request.state, "user", None)
        if user and user.get("sub"):
            return f"user:{user['sub']}"
        if request.client:
            return f"ip:{request.client.host}"
        return "ip:unknown"

    @staticmethod
    def _get_client_role(request: Request) -> str:
        """Return the authenticated role, defaulting to 'user'."""
        user = getattr(request.state, "user", None)
        if user:
            return user.get("role", "user")
        return "user"
