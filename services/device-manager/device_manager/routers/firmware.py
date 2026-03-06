"""Firmware management router.

Provides endpoints for registering firmware versions, listing them,
starting OTA rollouts, and checking rollout status.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from shared.database.postgres import get_async_session

from device_manager.services import firmware_service

router = APIRouter(prefix="/firmware", tags=["firmware"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class FirmwareCreateBody(BaseModel):
    """Body for registering a new firmware version."""

    version: str
    device_type: str
    checksum: str
    file_url: str
    release_notes: str | None = None


class FirmwareResponse(BaseModel):
    """Firmware version returned by the API."""

    id: UUID
    version: str
    device_type: str
    checksum: str
    file_url: str
    release_notes: str | None = None
    created_at: str


class RolloutRequestBody(BaseModel):
    """Body for starting a firmware rollout."""

    firmware_id: UUID
    target_devices: list[str]


class RolloutResponse(BaseModel):
    """Firmware rollout status."""

    rollout_id: str
    firmware_id: str
    firmware_version: str
    device_type: str
    target_devices: list[str]
    status: str
    started_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _firmware_to_response(fw) -> FirmwareResponse:
    return FirmwareResponse(
        id=fw.id,
        version=fw.version,
        device_type=fw.device_type,
        checksum=fw.checksum,
        file_url=fw.file_url,
        release_notes=fw.release_notes,
        created_at=fw.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=FirmwareResponse, status_code=201)
async def register_firmware(body: FirmwareCreateBody, request: Request):
    """Register a new firmware version in the catalog."""
    settings = request.app.state.settings
    async for session in get_async_session(settings.postgres_dsn):
        try:
            fw = await firmware_service.register_firmware(
                session,
                version=body.version,
                device_type=body.device_type,
                checksum=body.checksum,
                file_url=body.file_url,
                release_notes=body.release_notes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _firmware_to_response(fw)


@router.get("", response_model=list[FirmwareResponse])
async def list_firmware(
    request: Request,
    device_type: str | None = Query(None, description="Filter by device type"),
):
    """List available firmware versions, optionally filtered by device type."""
    settings = request.app.state.settings
    async for session in get_async_session(settings.postgres_dsn):
        versions = await firmware_service.list_firmware(session, device_type=device_type)
        return [_firmware_to_response(fw) for fw in versions]


@router.post("/rollout", response_model=RolloutResponse, status_code=201)
async def start_rollout(body: RolloutRequestBody, request: Request):
    """Start an OTA firmware rollout to specified devices."""
    settings = request.app.state.settings
    redis = request.app.state.redis_client
    async for session in get_async_session(settings.postgres_dsn):
        try:
            rollout = await firmware_service.start_rollout(
                session, redis, body.firmware_id, body.target_devices
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return RolloutResponse(**rollout)


@router.get("/rollout/{rollout_id}", response_model=RolloutResponse)
async def get_rollout_status(rollout_id: str, request: Request):
    """Check the current status of a firmware rollout."""
    redis = request.app.state.redis_client
    rollout = await firmware_service.get_rollout_status(redis, rollout_id)
    if rollout is None:
        raise HTTPException(status_code=404, detail="Rollout not found.")
    return RolloutResponse(**rollout)
