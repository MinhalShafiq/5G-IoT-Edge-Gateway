"""Authentication middleware.

Validates every incoming request (except explicitly excluded paths) by
checking the ``Authorization`` header for either a Bearer JWT token or
an ``ApiKey`` key.  On success the decoded identity is placed into
``request.state.user``.
"""

from __future__ import annotations

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared.auth.jwt_handler import JWTError, decode_token

logger = structlog.get_logger("middleware.authentication")

# Paths that do not require authentication
EXCLUDED_PATHS: set[str] = {
    "/health",
    "/ready",
    "/health/dependencies",
    "/auth/login",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# Path prefixes that are excluded
EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Enforce authentication on all non-excluded routes."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip authentication for excluded paths
        if path in EXCLUDED_PATHS or path.startswith(EXCLUDED_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return self._unauthorized("Missing Authorization header")

        # --- Bearer JWT ---
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            user = self._validate_jwt(token, request)
            if user is None:
                return self._unauthorized("Invalid or expired JWT token")
            request.state.user = user
            return await call_next(request)

        # --- API Key ---
        if auth_header.startswith("ApiKey "):
            api_key = auth_header[7:]
            user = await self._validate_api_key(api_key, request)
            if user is None:
                return self._unauthorized("Invalid API key")
            request.state.user = user
            return await call_next(request)

        return self._unauthorized(
            "Authorization header must start with 'Bearer' or 'ApiKey'"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_jwt(token: str, request: Request) -> dict | None:
        """Decode a JWT and return the payload, or None on failure."""
        settings = request.app.state.settings
        try:
            payload = decode_token(
                token,
                secret=settings.jwt_secret,
                algorithm=settings.jwt_algorithm,
            )
            return {
                "sub": payload.get("sub"),
                "role": payload.get("role", "user"),
                "auth_method": "jwt",
            }
        except JWTError as exc:
            logger.debug("jwt_validation_failed", error=str(exc))
            return None

    @staticmethod
    async def _validate_api_key(api_key: str, request: Request) -> dict | None:
        """Look up an API key in Redis and return identity, or None."""
        redis_client = request.app.state.redis
        try:
            import json

            data = await redis_client.get(f"apikey:{api_key}")
            if data is None:
                return None
            meta = json.loads(data)
            return {
                "sub": meta.get("name", "api-key-user"),
                "role": meta.get("role", "device"),
                "auth_method": "api_key",
                "key_id": meta.get("key_id"),
            }
        except Exception as exc:
            logger.warning("api_key_validation_error", error=str(exc))
            return None

    @staticmethod
    def _unauthorized(detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": detail},
        )
