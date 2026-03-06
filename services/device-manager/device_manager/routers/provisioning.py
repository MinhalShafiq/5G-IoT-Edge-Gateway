"""Provisioning workflow router.

Handles device provisioning requests: creation, approval, and status checks.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.database.postgres import get_async_session

from device_manager.services import provision_service

router = APIRouter(prefix="/provision", tags=["provisioning"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ProvisioningRequestBody(BaseModel):
    """Body for requesting device provisioning."""

    device_id: UUID
    credentials: dict | None = None  # optional metadata/creds sent by device


class ProvisioningApproveBody(BaseModel):
    """Body for approving a provisioning request."""

    approved_by: str


class ProvisioningResponse(BaseModel):
    """Provisioning request state returned by the API."""

    id: UUID
    device_id: UUID
    status: str
    requested_at: str
    approved_at: str | None = None
    approved_by: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prov_to_response(prov) -> ProvisioningResponse:
    return ProvisioningResponse(
        id=prov.id,
        device_id=prov.device_id,
        status=prov.status.value if hasattr(prov.status, "value") else prov.status,
        requested_at=prov.requested_at.isoformat(),
        approved_at=prov.approved_at.isoformat() if prov.approved_at else None,
        approved_by=prov.approved_by,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/request", response_model=ProvisioningResponse, status_code=201)
async def request_provisioning(body: ProvisioningRequestBody, request: Request):
    """Device sends a provisioning request."""
    settings = request.app.state.settings
    redis = request.app.state.redis_client
    async for session in get_async_session(settings.postgres_dsn):
        try:
            prov = await provision_service.request_provisioning(
                session, redis, body.device_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _prov_to_response(prov)


@router.post("/approve/{request_id}", response_model=ProvisioningResponse)
async def approve_provisioning(
    request_id: UUID,
    body: ProvisioningApproveBody,
    request: Request,
):
    """Admin approves a pending provisioning request."""
    settings = request.app.state.settings
    redis = request.app.state.redis_client
    async for session in get_async_session(settings.postgres_dsn):
        try:
            prov = await provision_service.approve_provisioning(
                session, redis, request_id, body.approved_by
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _prov_to_response(prov)


@router.get("/status/{device_id}", response_model=ProvisioningResponse | None)
async def get_provisioning_status(device_id: UUID, request: Request):
    """Check the latest provisioning status for a device."""
    settings = request.app.state.settings
    async for session in get_async_session(settings.postgres_dsn):
        prov = await provision_service.get_provisioning_status(session, device_id)
        if prov is None:
            raise HTTPException(
                status_code=404,
                detail="No provisioning request found for this device.",
            )
        return _prov_to_response(prov)
