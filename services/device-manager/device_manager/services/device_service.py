"""Business logic layer for device lifecycle management.

Orchestrates repository calls, emits events to Redis Streams,
updates Prometheus metrics, and enforces business rules.
"""

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger
from shared.observability.metrics import ACTIVE_DEVICES
from shared.streams.constants import StreamName

from device_manager.db.models import DeviceStatusEnum
from device_manager.repositories import device_repo

logger = get_logger(__name__)


async def _emit_device_event(
    redis: RedisClient,
    event_type: str,
    device_id: str,
    payload: dict | None = None,
) -> None:
    """Publish a device lifecycle event to the device_events Redis Stream."""
    data = {
        "event_type": event_type,
        "device_id": str(device_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if payload:
        data["payload"] = json.dumps(payload)

    await redis.stream_add(StreamName.DEVICE_EVENTS, data)
    logger.info("device_event_emitted", event_type=event_type, device_id=str(device_id))


async def list_devices(
    session: AsyncSession,
    device_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list, int]:
    """List devices with optional filters and pagination."""
    return await device_repo.list_devices(
        session, device_type=device_type, status=status, limit=limit, offset=offset
    )


async def get_device(session: AsyncSession, device_id: UUID):
    """Retrieve a single device by ID."""
    return await device_repo.get_device(session, device_id)


async def create_device(
    session: AsyncSession,
    redis: RedisClient,
    device_data: dict,
):
    """Register a new device, emit a creation event, and update metrics."""
    device = await device_repo.create_device(session, device_data)

    # Emit event
    await _emit_device_event(redis, "device_created", str(device.id), {
        "name": device.name,
        "device_type": device.device_type,
    })

    # Update Prometheus gauge
    ACTIVE_DEVICES.labels(device_type=device.device_type).inc(0)

    logger.info("device_created", device_id=str(device.id), name=device.name)
    return device


async def update_device(
    session: AsyncSession,
    redis: RedisClient,
    device_id: UUID,
    updates: dict,
):
    """Update device fields and emit an update event.

    Business rules:
    - Cannot update a decommissioned device.
    """
    existing = await device_repo.get_device(session, device_id)
    if existing is None:
        return None

    if existing.status == DeviceStatusEnum.DECOMMISSIONED:
        raise ValueError("Cannot update a decommissioned device.")

    device = await device_repo.update_device(session, device_id, updates)
    if device is None:
        return None

    # Emit event
    await _emit_device_event(redis, "device_updated", str(device.id), updates)

    # If status changed to active, increment gauge; if changed away, decrement
    new_status = updates.get("status")
    if new_status is not None:
        if new_status == DeviceStatusEnum.ACTIVE.value or new_status == DeviceStatusEnum.ACTIVE:
            ACTIVE_DEVICES.labels(device_type=device.device_type).inc()
        elif existing.status == DeviceStatusEnum.ACTIVE:
            ACTIVE_DEVICES.labels(device_type=device.device_type).dec()

    logger.info("device_updated", device_id=str(device_id))
    return device


async def delete_device(
    session: AsyncSession,
    redis: RedisClient,
    device_id: UUID,
) -> bool:
    """Soft-delete (decommission) a device.

    Business rules:
    - An ACTIVE device must be set to INACTIVE before decommissioning.
    """
    existing = await device_repo.get_device(session, device_id)
    if existing is None:
        return False

    if existing.status == DeviceStatusEnum.ACTIVE:
        raise ValueError(
            "Cannot decommission an active device. Set status to INACTIVE first."
        )

    if existing.status == DeviceStatusEnum.DECOMMISSIONED:
        raise ValueError("Device is already decommissioned.")

    success = await device_repo.delete_device(session, device_id)
    if success:
        await _emit_device_event(redis, "device_decommissioned", str(device_id))

        # Decrement gauge if device was previously counted
        if existing.status == DeviceStatusEnum.ACTIVE:
            ACTIVE_DEVICES.labels(device_type=existing.device_type).dec()

        logger.info("device_decommissioned", device_id=str(device_id))

    return success


async def get_stats(session: AsyncSession) -> dict:
    """Return aggregate statistics about the device fleet."""
    return await device_repo.get_stats(session)
