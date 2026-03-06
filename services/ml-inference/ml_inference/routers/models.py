"""Model management API routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from shared.observability.logging_config import get_logger

from ml_inference.services.inference_engine import InferenceEngine
from ml_inference.services import model_manager

logger = get_logger(__name__)

router = APIRouter(prefix="/models", tags=["models"])


class ModelInfo(BaseModel):
    """Information about the currently loaded model."""

    model_name: str
    model_version: str
    loaded_at: str | None
    is_loaded: bool


class ModelAvailable(BaseModel):
    """An available model from the registry."""

    name: str
    version: str
    download_url: str
    created_at: str | None = None


class ReloadResponse(BaseModel):
    """Response for model reload operation."""

    status: str
    message: str
    model_info: ModelInfo | None = None


def _get_engine(request: Request) -> InferenceEngine:
    return request.app.state.engine


def _get_settings(request: Request):
    return request.app.state.settings


@router.get("/current", response_model=ModelInfo)
async def get_current_model(
    engine: Annotated[InferenceEngine, Depends(_get_engine)],
):
    """Return information about the currently loaded ONNX model."""
    info = engine.get_info()
    return ModelInfo(
        model_name=info["model_name"],
        model_version=info["model_version"],
        loaded_at=info["loaded_at"],
        is_loaded=engine.is_loaded,
    )


@router.post("/reload", response_model=ReloadResponse)
async def reload_model(
    engine: Annotated[InferenceEngine, Depends(_get_engine)],
    request: Request,
):
    """Trigger a model hot-swap from the model registry.

    Downloads the latest model version and performs a blue-green swap.
    """
    settings = _get_settings(request)

    try:
        # Check for a newer model in the registry
        new_model_path = await model_manager.check_and_download(
            registry_url=settings.model_registry_url,
            model_dir=settings.model_dir,
        )

        if new_model_path is None:
            return ReloadResponse(
                status="unchanged",
                message="No newer model available in registry",
                model_info=ModelInfo(
                    **engine.get_info(),
                    is_loaded=engine.is_loaded,
                ),
            )

        # Hot-swap to the new model
        model_manager.hot_swap(engine, new_model_path)

        info = engine.get_info()
        logger.info(
            "model_reloaded",
            model_name=info["model_name"],
            model_version=info["model_version"],
        )

        return ReloadResponse(
            status="reloaded",
            message=f"Model swapped to {info['model_name']} v{info['model_version']}",
            model_info=ModelInfo(**info, is_loaded=engine.is_loaded),
        )

    except Exception as e:
        logger.error("model_reload_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Model reload failed: {e}")


@router.get("/available", response_model=list[ModelAvailable])
async def list_available_models(request: Request):
    """List available models from the model registry."""
    settings = _get_settings(request)

    try:
        available = await model_manager.list_available(settings.model_registry_url)
        return [
            ModelAvailable(
                name=m["name"],
                version=m["version"],
                download_url=m.get("download_url", ""),
                created_at=m.get("created_at"),
            )
            for m in available
        ]
    except Exception as e:
        logger.error("registry_fetch_failed", error=str(e))
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach model registry: {e}",
        )
