"""Health check helpers for FastAPI services."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "unknown"
    version: str = "0.1.0"


def create_health_router(service_name: str, version: str = "0.1.0") -> APIRouter:
    """Create a health check router with service-specific metadata."""
    health_router = APIRouter(tags=["health"])

    @health_router.get("/health")
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service=service_name, version=version)

    @health_router.get("/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    return health_router
