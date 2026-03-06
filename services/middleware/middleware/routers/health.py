"""Health check endpoints.

Reuses the shared health router and adds a /health/dependencies endpoint
that verifies connectivity to Redis and all upstream services.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

from shared.utils.health import create_health_router

logger = structlog.get_logger(__name__)

# Create the standard /health and /ready endpoints from shared library
_shared_health = create_health_router(service_name="middleware", version="0.1.0")

router = APIRouter(tags=["health"])
# Include the shared routes
router.include_router(_shared_health)


@router.get("/health/dependencies")
async def health_dependencies(request: Request) -> dict:
    """Deep health check that verifies Redis and upstream service connectivity."""
    results: dict[str, dict[str, str]] = {}
    settings = request.app.state.settings

    # Check Redis
    try:
        redis_client = request.app.state.redis
        pong = await redis_client.ping()
        results["redis"] = {"status": "ok" if pong else "degraded"}
    except Exception as exc:
        logger.warning("health_redis_failed", error=str(exc))
        results["redis"] = {"status": "error", "detail": str(exc)}

    # Check upstream services
    http_client = request.app.state.http_client
    upstreams = {
        "device-manager": settings.upstream_device_manager,
        "data-ingestion": settings.upstream_data_ingestion,
        "ml-inference": settings.upstream_ml_inference,
        "scheduler": settings.upstream_scheduler,
    }

    for name, base_url in upstreams.items():
        try:
            resp = await http_client.get(f"{base_url}/health", timeout=3.0)
            if resp.status_code == 200:
                results[name] = {"status": "ok"}
            else:
                results[name] = {
                    "status": "degraded",
                    "detail": f"HTTP {resp.status_code}",
                }
        except Exception as exc:
            logger.debug("health_upstream_unreachable", service=name, error=str(exc))
            results[name] = {"status": "unreachable", "detail": str(exc)}

    # Overall status
    all_ok = all(dep["status"] == "ok" for dep in results.values())
    any_error = any(dep["status"] in ("error", "unreachable") for dep in results.values())

    if all_ok:
        overall = "ok"
    elif any_error:
        overall = "degraded"
    else:
        overall = "degraded"

    return {"status": overall, "dependencies": results}
