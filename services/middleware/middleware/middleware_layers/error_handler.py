"""Global error handler middleware.

Catches unhandled exceptions, logs the full traceback, and returns a
structured JSON error response with the correlation ID.
"""

from __future__ import annotations

import traceback

import structlog
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger("middleware.error_handler")

# Map well-known exception types to HTTP status codes
EXCEPTION_STATUS_MAP: dict[type, int] = {
    ValueError: 400,
    KeyError: 400,
    PermissionError: 403,
    FileNotFoundError: 404,
    NotImplementedError: 501,
    TimeoutError: 504,
}


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Return structured JSON error responses for unhandled exceptions."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except RequestValidationError as exc:
            return self._error_response(
                request=request,
                status_code=422,
                error="Validation Error",
                message=str(exc),
            )
        except Exception as exc:
            # Determine status code from known exception types
            status_code = 500
            for exc_type, code in EXCEPTION_STATUS_MAP.items():
                if isinstance(exc, exc_type):
                    status_code = code
                    break

            # Log with full traceback
            correlation_id = getattr(request.state, "correlation_id", None)
            logger.error(
                "unhandled_exception",
                exc_type=type(exc).__name__,
                exc_message=str(exc),
                correlation_id=correlation_id,
                path=request.url.path,
                method=request.method,
                traceback=traceback.format_exc(),
            )

            error_name = type(exc).__name__
            return self._error_response(
                request=request,
                status_code=status_code,
                error=error_name,
                message=str(exc) if status_code < 500 else "Internal server error",
            )

    @staticmethod
    def _error_response(
        request: Request,
        status_code: int,
        error: str,
        message: str,
    ) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None)
        return JSONResponse(
            status_code=status_code,
            content={
                "error": error,
                "message": message,
                "correlation_id": correlation_id,
                "status_code": status_code,
            },
        )
