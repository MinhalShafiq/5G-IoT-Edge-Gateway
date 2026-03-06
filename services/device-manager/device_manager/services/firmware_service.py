"""Firmware management service.

Handles firmware version registration, listing, and OTA rollout orchestration.
"""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger
from shared.streams.constants import StreamName

from device_manager.db.models import FirmwareVersion

logger = get_logger(__name__)


async def register_firmware(
    session: AsyncSession,
    version: str,
    device_type: str,
    checksum: str,
    file_url: str,
    release_notes: str | None = None,
) -> FirmwareVersion:
    """Register a new firmware version in the catalog.

    Raises ValueError if the version already exists for the given device type.
    """
    # Check for duplicate
    existing_query = select(FirmwareVersion).where(
        FirmwareVersion.version == version,
        FirmwareVersion.device_type == device_type,
    )
    existing_result = await session.execute(existing_query)
    if existing_result.scalar_one_or_none() is not None:
        raise ValueError(
            f"Firmware version {version} for device type {device_type} already exists."
        )

    firmware = FirmwareVersion(
        version=version,
        device_type=device_type,
        checksum=checksum,
        file_url=file_url,
        release_notes=release_notes,
    )
    session.add(firmware)
    await session.commit()
    await session.refresh(firmware)

    logger.info(
        "firmware_registered",
        firmware_id=str(firmware.id),
        version=version,
        device_type=device_type,
    )
    return firmware


async def list_firmware(
    session: AsyncSession,
    device_type: str | None = None,
) -> list[FirmwareVersion]:
    """List firmware versions, optionally filtered by device type."""
    query = select(FirmwareVersion).order_by(FirmwareVersion.created_at.desc())
    if device_type is not None:
        query = query.where(FirmwareVersion.device_type == device_type)

    result = await session.execute(query)
    return list(result.scalars().all())


async def start_rollout(
    session: AsyncSession,
    redis: RedisClient,
    firmware_id: uuid.UUID,
    target_devices: list[str],
) -> dict:
    """Initiate an OTA firmware rollout to a set of target devices.

    Publishes a rollout event to the device_events Redis Stream so that
    downstream services can coordinate the actual update delivery.

    Returns a rollout record with a unique rollout_id.
    """
    # Validate firmware exists
    result = await session.execute(
        select(FirmwareVersion).where(FirmwareVersion.id == firmware_id)
    )
    firmware = result.scalar_one_or_none()
    if firmware is None:
        raise ValueError(f"Firmware {firmware_id} not found.")

    rollout_id = str(uuid.uuid4())

    rollout_record = {
        "rollout_id": rollout_id,
        "firmware_id": str(firmware_id),
        "firmware_version": firmware.version,
        "device_type": firmware.device_type,
        "target_devices": target_devices,
        "status": "in_progress",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # Store rollout state in Redis for status tracking
    await redis.set(
        f"firmware_rollout:{rollout_id}",
        json.dumps(rollout_record),
        ex=86400,  # TTL 24 hours
    )

    # Publish rollout event to stream
    await redis.stream_add(StreamName.DEVICE_EVENTS, {
        "event_type": "firmware_rollout_started",
        "rollout_id": rollout_id,
        "firmware_id": str(firmware_id),
        "firmware_version": firmware.version,
        "device_type": firmware.device_type,
        "target_devices": json.dumps(target_devices),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    logger.info(
        "firmware_rollout_started",
        rollout_id=rollout_id,
        firmware_id=str(firmware_id),
        target_count=len(target_devices),
    )

    return rollout_record


async def get_rollout_status(redis: RedisClient, rollout_id: str) -> dict | None:
    """Retrieve the current status of a firmware rollout."""
    data = await redis.get(f"firmware_rollout:{rollout_id}")
    if data is None:
        return None
    return json.loads(data)
