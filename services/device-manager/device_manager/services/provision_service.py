"""Provisioning workflow service.

Manages the lifecycle of device provisioning requests:
pending -> approved/rejected, with device status transitions.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger
from shared.streams.constants import StreamName

from device_manager.db.models import (
    Device,
    DeviceStatusEnum,
    ProvisioningRequest,
    ProvisioningStatusEnum,
)

logger = get_logger(__name__)


async def request_provisioning(
    session: AsyncSession,
    redis: RedisClient,
    device_id: UUID,
) -> ProvisioningRequest:
    """Create a new provisioning request for a device.

    The device must exist and be in REGISTERED status.
    """
    # Validate device exists
    result = await session.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device is None:
        raise ValueError(f"Device {device_id} not found.")

    if device.status != DeviceStatusEnum.REGISTERED:
        raise ValueError(
            f"Device must be in REGISTERED status to request provisioning. "
            f"Current status: {device.status.value}"
        )

    # Check for existing pending request
    existing_query = select(ProvisioningRequest).where(
        ProvisioningRequest.device_id == device_id,
        ProvisioningRequest.status == ProvisioningStatusEnum.PENDING,
    )
    existing_result = await session.execute(existing_query)
    if existing_result.scalar_one_or_none() is not None:
        raise ValueError(
            f"Device {device_id} already has a pending provisioning request."
        )

    request = ProvisioningRequest(
        device_id=device_id,
        status=ProvisioningStatusEnum.PENDING,
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)

    # Emit provisioning event
    await redis.stream_add(StreamName.DEVICE_EVENTS, {
        "event_type": "provisioning_requested",
        "device_id": str(device_id),
        "request_id": str(request.id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    logger.info(
        "provisioning_requested",
        device_id=str(device_id),
        request_id=str(request.id),
    )
    return request


async def approve_provisioning(
    session: AsyncSession,
    redis: RedisClient,
    request_id: UUID,
    approved_by: str,
) -> ProvisioningRequest:
    """Approve a pending provisioning request and update device status.

    Sets the provisioning request to APPROVED and transitions the device
    to PROVISIONED status.
    """
    result = await session.execute(
        select(ProvisioningRequest).where(ProvisioningRequest.id == request_id)
    )
    prov_request = result.scalar_one_or_none()
    if prov_request is None:
        raise ValueError(f"Provisioning request {request_id} not found.")

    if prov_request.status != ProvisioningStatusEnum.PENDING:
        raise ValueError(
            f"Provisioning request is not pending. Current status: {prov_request.status.value}"
        )

    # Update provisioning request
    prov_request.status = ProvisioningStatusEnum.APPROVED
    prov_request.approved_at = datetime.now(timezone.utc)
    prov_request.approved_by = approved_by

    # Update device status to PROVISIONED
    device_result = await session.execute(
        select(Device).where(Device.id == prov_request.device_id)
    )
    device = device_result.scalar_one_or_none()
    if device is not None:
        device.status = DeviceStatusEnum.PROVISIONED

    await session.commit()
    await session.refresh(prov_request)

    # Emit provisioning approved event
    await redis.stream_add(StreamName.DEVICE_EVENTS, {
        "event_type": "provisioning_approved",
        "device_id": str(prov_request.device_id),
        "request_id": str(request_id),
        "approved_by": approved_by,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    logger.info(
        "provisioning_approved",
        request_id=str(request_id),
        device_id=str(prov_request.device_id),
        approved_by=approved_by,
    )
    return prov_request


async def get_provisioning_status(
    session: AsyncSession,
    device_id: UUID,
) -> ProvisioningRequest | None:
    """Return the most recent provisioning request for a device."""
    query = (
        select(ProvisioningRequest)
        .where(ProvisioningRequest.device_id == device_id)
        .order_by(ProvisioningRequest.requested_at.desc())
        .limit(1)
    )
    result = await session.execute(query)
    return result.scalar_one_or_none()
