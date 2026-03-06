"""Model registry endpoints.

Provides CRUD for ML model versions that edge gateways pull from the cloud.
Models are stored in PostgreSQL; the actual model artifacts live at ``file_url``.
"""

from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.postgres import get_async_session

from cloud_api.config import Settings, get_settings
from cloud_api.services.model_registry import (
    register_model,
    list_models,
    get_model,
    get_latest_model,
    delete_model,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/models", tags=["models"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ModelCreate(BaseModel):
    """Request body to register a new model version."""

    name: str
    version: str
    framework: str = "onnx"
    description: str = ""
    file_url: str
    metrics: dict = Field(default_factory=dict)


class ModelResponse(BaseModel):
    """Full model representation returned to the client."""

    id: UUID
    name: str
    version: str
    framework: str
    description: str
    file_url: str
    metrics: dict
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelDeleteResponse(BaseModel):
    """Confirmation of soft-delete."""

    id: UUID
    deleted: bool


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def get_session(
    settings: Settings = Depends(get_settings),
) -> AsyncSession:
    """Yield a database session."""
    async for session in get_async_session(settings.postgres_dsn):
        yield session


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ModelResponse, status_code=201)
async def create_model(
    model: ModelCreate,
    session: AsyncSession = Depends(get_session),
) -> ModelResponse:
    """Upload / register a new model version."""
    logger.info("registering model", name=model.name, version=model.version)
    result = await register_model(model, session)
    return result


@router.get("", response_model=list[ModelResponse])
async def list_models_endpoint(
    name: str | None = Query(None, description="Filter by model name"),
    session: AsyncSession = Depends(get_session),
) -> list[ModelResponse]:
    """List all registered model versions, optionally filtered by name."""
    return await list_models(name_filter=name, session=session)


@router.get("/latest/{model_name}", response_model=ModelResponse)
async def latest_model(
    model_name: str,
    session: AsyncSession = Depends(get_session),
) -> ModelResponse:
    """Get the latest active version of a named model."""
    result = await get_latest_model(model_name, session)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No active model found with name '{model_name}'")
    return result


@router.get("/{model_id}", response_model=ModelResponse)
async def get_model_endpoint(
    model_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ModelResponse:
    """Get a specific model version by ID."""
    result = await get_model(model_id, session)
    if result is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return result


@router.delete("/{model_id}", response_model=ModelDeleteResponse)
async def delete_model_endpoint(
    model_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ModelDeleteResponse:
    """Soft-delete a model version (marks is_active=False)."""
    deleted = await delete_model(model_id, session)
    if not deleted:
        raise HTTPException(status_code=404, detail="Model not found")
    return ModelDeleteResponse(id=model_id, deleted=True)
