"""Repository layer for Device CRUD operations.

All functions accept an async SQLAlchemy session and return ORM model instances.
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from device_manager.db.models import Device, DeviceStatusEnum


async def list_devices(
    session: AsyncSession,
    device_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Device], int]:
    """Return a paginated list of devices with optional filters.

    Returns:
        A tuple of (devices, total_count).
    """
    query = select(Device)
    count_query = select(func.count(Device.id))

    if device_type is not None:
        query = query.where(Device.device_type == device_type)
        count_query = count_query.where(Device.device_type == device_type)

    if status is not None:
        query = query.where(Device.status == status)
        count_query = count_query.where(Device.status == status)

    # Total count (before pagination)
    total_result = await session.execute(count_query)
    total_count = total_result.scalar_one()

    # Fetch page
    query = query.order_by(Device.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    devices = list(result.scalars().all())

    return devices, total_count


async def get_device(session: AsyncSession, device_id: UUID) -> Device | None:
    """Get a single device by its UUID primary key."""
    result = await session.execute(select(Device).where(Device.id == device_id))
    return result.scalar_one_or_none()


async def create_device(session: AsyncSession, device_data: dict) -> Device:
    """Insert a new device row and return the ORM instance."""
    device = Device(**device_data)
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return device


async def update_device(
    session: AsyncSession,
    device_id: UUID,
    updates: dict,
) -> Device | None:
    """Apply partial updates to an existing device.

    Returns the updated device, or None if the device was not found.
    """
    stmt = (
        update(Device)
        .where(Device.id == device_id)
        .values(**updates)
        .returning(Device)
    )
    result = await session.execute(stmt)
    await session.commit()

    device = result.scalar_one_or_none()
    if device is not None:
        await session.refresh(device)
    return device


async def delete_device(session: AsyncSession, device_id: UUID) -> bool:
    """Soft-delete a device by setting its status to DECOMMISSIONED.

    Returns True if the device existed and was updated, False otherwise.
    """
    stmt = (
        update(Device)
        .where(Device.id == device_id)
        .values(status=DeviceStatusEnum.DECOMMISSIONED)
        .returning(Device.id)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.scalar_one_or_none() is not None


async def get_stats(session: AsyncSession) -> dict:
    """Return device counts grouped by type and by status.

    Returns:
        {
            "by_type": {"temperature_sensor": 10, ...},
            "by_status": {"active": 5, "registered": 3, ...},
            "total": 18,
        }
    """
    # Count by type
    type_query = select(
        Device.device_type, func.count(Device.id)
    ).group_by(Device.device_type)
    type_result = await session.execute(type_query)
    by_type = {row[0]: row[1] for row in type_result.all()}

    # Count by status
    status_query = select(
        Device.status, func.count(Device.id)
    ).group_by(Device.status)
    status_result = await session.execute(status_query)
    by_status = {row[0].value if hasattr(row[0], "value") else row[0]: row[1] for row in status_result.all()}

    total = sum(by_type.values())

    return {"by_type": by_type, "by_status": by_status, "total": total}
