"""Device CRUD router.

Provides full lifecycle management of IoT devices: list, get, create,
update, soft-delete, and fleet statistics.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from shared.database.postgres import get_async_session
from shared.models.device import DeviceCreate, DeviceStatus, DeviceType

from device_manager.services import device_service

router = APIRouter(prefix="/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DeviceResponse(BaseModel):
    """Serialised device returned by the API."""

    id: UUID
    name: str
    device_type: str
    status: str
    firmware_version: str
    last_seen_at: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class DeviceListResponse(BaseModel):
    devices: list[DeviceResponse]
    total: int
    limit: int
    offset: int


class DeviceUpdate(BaseModel):
    """Partial update schema."""

    name: str | None = None
    device_type: DeviceType | None = None
    status: DeviceStatus | None = None
    firmware_version: str | None = None
    metadata: dict | None = None


class DeviceStatsResponse(BaseModel):
    by_type: dict[str, int]
    by_status: dict[str, int]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _device_to_response(device) -> DeviceResponse:
    """Convert an ORM Device to the response schema."""
    return DeviceResponse(
        id=device.id,
        name=device.name,
        device_type=device.device_type,
        status=device.status.value if hasattr(device.status, "value") else device.status,
        firmware_version=device.firmware_version,
        last_seen_at=device.last_seen_at.isoformat() if device.last_seen_at else None,
        metadata=device.metadata_ if hasattr(device, "metadata_") else device.metadata,
        created_at=device.created_at.isoformat(),
        updated_at=device.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints — NOTE: /stats must come before /{device_id} to avoid conflict
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=DeviceStatsResponse)
async def get_device_stats(request: Request):
    """Return aggregate device counts by type and by status."""
    settings = request.app.state.settings
    async for session in get_async_session(settings.postgres_dsn):
        stats = await device_service.get_stats(session)
        return DeviceStatsResponse(**stats)


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    request: Request,
    device_type: str | None = Query(None, description="Filter by device type"),
    status: str | None = Query(None, description="Filter by device status"),
    limit: int = Query(50, ge=1, le=500, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
):
    """List all registered devices with pagination and optional filters."""
    settings = request.app.state.settings
    async for session in get_async_session(settings.postgres_dsn):
        devices, total = await device_service.list_devices(
            session, device_type=device_type, status=status, limit=limit, offset=offset
        )
        return DeviceListResponse(
            devices=[_device_to_response(d) for d in devices],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: UUID, request: Request):
    """Retrieve a single device by its UUID."""
    settings = request.app.state.settings
    async for session in get_async_session(settings.postgres_dsn):
        device = await device_service.get_device(session, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found.")
        return _device_to_response(device)


@router.post("", response_model=DeviceResponse, status_code=201)
async def create_device(body: DeviceCreate, request: Request):
    """Register a new IoT device."""
    settings = request.app.state.settings
    redis = request.app.state.redis_client
    async for session in get_async_session(settings.postgres_dsn):
        device = await device_service.create_device(
            session,
            redis,
            {
                "name": body.name,
                "device_type": body.device_type.value,
                "firmware_version": body.firmware_version,
                "metadata_": body.metadata,
            },
        )
        return _device_to_response(device)


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: UUID, body: DeviceUpdate, request: Request):
    """Update fields on an existing device."""
    settings = request.app.state.settings
    redis = request.app.state.redis_client
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    # Convert enum values to their string representations for the ORM
    if "device_type" in updates and updates["device_type"] is not None:
        updates["device_type"] = updates["device_type"].value
    if "status" in updates and updates["status"] is not None:
        updates["status"] = updates["status"].value

    # Map pydantic 'metadata' field to the ORM column name 'metadata_'
    if "metadata" in updates:
        updates["metadata_"] = updates.pop("metadata")

    async for session in get_async_session(settings.postgres_dsn):
        try:
            device = await device_service.update_device(
                session, redis, device_id, updates
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        if device is None:
            raise HTTPException(status_code=404, detail="Device not found.")
        return _device_to_response(device)


@router.delete("/{device_id}", status_code=200)
async def delete_device(device_id: UUID, request: Request):
    """Soft-delete a device (set status to decommissioned)."""
    settings = request.app.state.settings
    redis = request.app.state.redis_client
    async for session in get_async_session(settings.postgres_dsn):
        try:
            success = await device_service.delete_device(session, redis, device_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        if not success:
            raise HTTPException(status_code=404, detail="Device not found.")
        return {"detail": "Device decommissioned."}
