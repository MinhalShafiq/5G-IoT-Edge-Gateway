"""Health check router for the Batch Analytics service."""

from shared.utils.health import create_health_router

router = create_health_router("batch-analytics")
