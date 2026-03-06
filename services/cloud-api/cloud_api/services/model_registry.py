"""Model registry service.

Manages ML model versions in PostgreSQL. Each model record stores metadata
and a ``file_url`` pointing to the actual artifact (e.g. an S3 / blob URL).
Edge gateways poll the ``/models/latest/{name}`` endpoint to discover new
model versions.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSON, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.postgres import Base

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM Table
# ---------------------------------------------------------------------------


class MLModel(Base):
    """PostgreSQL table for the ML model registry."""

    __tablename__ = "ml_models"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(100), nullable=False, index=True)
    version = Column(String(50), nullable=False)
    framework = Column(String(50), default="onnx")
    description = Column(Text, default="")
    file_url = Column(String(500), nullable=False)
    metrics = Column(JSON, default={})
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# Pydantic helpers (import from router schemas when used externally)
# ---------------------------------------------------------------------------

# We keep a thin wrapper here so the service layer never depends on FastAPI
# router schemas. The router converts between these and its own response
# models transparently via ``from_attributes``.


def _row_to_dict(row: MLModel) -> dict:
    """Convert an ORM row to a plain dict suitable for ModelResponse."""
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "framework": row.framework,
        "description": row.description or "",
        "file_url": row.file_url,
        "metrics": row.metrics or {},
        "is_active": row.is_active,
        "created_at": row.created_at,
    }


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def register_model(model, session: AsyncSession) -> dict:
    """Insert a new model version and return the created record.

    ``model`` is expected to be a Pydantic ``ModelCreate`` instance (or any
    object with the same attributes).
    """
    new_model = MLModel(
        id=uuid4(),
        name=model.name,
        version=model.version,
        framework=model.framework,
        description=model.description,
        file_url=model.file_url,
        metrics=model.metrics if hasattr(model, "metrics") else {},
    )
    session.add(new_model)
    await session.commit()
    await session.refresh(new_model)

    logger.info("model registered", name=new_model.name, version=new_model.version, id=str(new_model.id))
    return _row_to_dict(new_model)


async def list_models(
    name_filter: str | None,
    session: AsyncSession,
) -> list[dict]:
    """List model versions, optionally filtered by name."""
    query = select(MLModel).where(MLModel.is_active == True)  # noqa: E712

    if name_filter:
        query = query.where(MLModel.name == name_filter)

    query = query.order_by(MLModel.created_at.desc())
    result = await session.execute(query)
    rows = result.scalars().all()
    return [_row_to_dict(r) for r in rows]


async def get_model(model_id: UUID, session: AsyncSession) -> dict | None:
    """Fetch a single model by primary key."""
    result = await session.execute(
        select(MLModel).where(MLModel.id == model_id)
    )
    row = result.scalar_one_or_none()
    return _row_to_dict(row) if row else None


async def get_latest_model(model_name: str, session: AsyncSession) -> dict | None:
    """Return the most recently created active model version for a given name."""
    result = await session.execute(
        select(MLModel)
        .where(MLModel.name == model_name, MLModel.is_active == True)  # noqa: E712
        .order_by(MLModel.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return _row_to_dict(row) if row else None


async def delete_model(model_id: UUID, session: AsyncSession) -> bool:
    """Soft-delete a model by setting ``is_active = False``.

    Returns ``True`` if the model was found and deactivated, ``False``
    otherwise.
    """
    result = await session.execute(
        select(MLModel).where(MLModel.id == model_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False

    row.is_active = False
    await session.commit()
    logger.info("model soft-deleted", id=str(model_id))
    return True
