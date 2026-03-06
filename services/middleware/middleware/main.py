"""FastAPI application entry-point for the Middleware / API Gateway.

Initialises Redis, logging, middleware layers (in order), and routers.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging

from middleware.config import get_settings
from middleware.middleware_layers.correlation_id import CorrelationIdMiddleware
from middleware.middleware_layers.error_handler import ErrorHandlerMiddleware
from middleware.middleware_layers.request_logger import RequestLoggerMiddleware
from middleware.middleware_layers.authentication import AuthenticationMiddleware
from middleware.middleware_layers.rate_limiter import RateLimiterMiddleware
from middleware.routers import auth, health, proxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown resources."""
    settings = get_settings()

    # Logging
    setup_logging(settings.service_name, settings.log_level)

    # Redis
    redis_client = RedisClient(
        url=settings.redis_url,
        max_connections=settings.redis_max_connections,
    )
    app.state.redis = redis_client
    app.state.settings = settings

    # httpx async client for reverse-proxying
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=40),
    )

    yield

    # Shutdown
    await app.state.http_client.aclose()
    await redis_client.close()


app = FastAPI(
    title="IoT Edge Gateway - API Gateway",
    description="Middleware service handling auth, rate limiting, logging, and proxying.",
    version="0.1.0",
    lifespan=lifespan,
)

# ---- Middleware layers (outermost first) ----
# Execution order on REQUEST:  error_handler -> correlation_id -> request_logger -> authentication -> rate_limiter
# Execution order on RESPONSE: rate_limiter -> authentication -> request_logger -> correlation_id -> error_handler
# Starlette adds middleware in *reverse* order, so the last .add_middleware is outermost.

app.add_middleware(RateLimiterMiddleware)
app.add_middleware(AuthenticationMiddleware)
app.add_middleware(RequestLoggerMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(ErrorHandlerMiddleware)

# ---- Routers ----
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(proxy.router)
