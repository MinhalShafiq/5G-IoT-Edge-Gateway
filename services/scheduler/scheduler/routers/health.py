"""Health check router for the Resource Scheduler."""

from shared.utils.health import create_health_router

router = create_health_router("scheduler")
