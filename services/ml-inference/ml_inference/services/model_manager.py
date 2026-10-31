"""Model lifecycle management.

Handles loading, downloading, hot-swapping, and baseline model generation
for the ONNX inference engine.
"""

import os
import glob
from pathlib import Path

import httpx

from shared.observability.logging_config import get_logger

from ml_inference.config import Settings
from ml_inference.services.inference_engine import InferenceEngine

logger = get_logger(__name__)


def load_initial_model(engine: InferenceEngine, settings: Settings) -> None:
    """Load the most recent .onnx model (and its .json sidecar) from the model directory.

    The image build trains the PyTorch autoencoder and places it in
    ``model_dir``, so a missing model is a deployment error.

    Args:
        engine: The inference engine to load the model into.
        settings: Service settings containing model_dir and other config.
    """
    model_dir = settings.model_dir
    os.makedirs(model_dir, exist_ok=True)

    onnx_files = sorted(
        glob.glob(os.path.join(model_dir, "*.onnx")),
        key=os.path.getmtime,
        reverse=True,
    )

    if not onnx_files:
        logger.error("no_onnx_models_found", model_dir=model_dir)
        return

    model_path = onnx_files[0]
    logger.info("loading_initial_model", path=model_path)
    engine.load_model(model_path)


async def check_for_updates(registry_url: str) -> dict | None:
    """Poll the model registry for a newer model version.

    Args:
        registry_url: URL of the model registry API.

    Returns:
        Model metadata dict if a newer version exists, None otherwise.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{registry_url}/latest")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.warning("registry_check_failed", error=str(e))
        return None


async def download_model(url: str, target_path: str) -> str:
    """Download an .onnx model file from the registry.

    Args:
        url: URL to download the model from.
        target_path: Local filesystem path to save the model.

    Returns:
        The target_path on success.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url)
        response.raise_for_status()

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(response.content)

    logger.info("model_downloaded", url=url, path=target_path)
    return target_path


def hot_swap(engine: InferenceEngine, new_model_path: str) -> None:
    """Blue-green model swap: load the new model, replacing the old session.

    If the new model fails to load, the old model remains active.

    Args:
        engine: The running inference engine.
        new_model_path: Path to the new .onnx model file.
    """
    model_name = Path(new_model_path).stem
    old_info = engine.get_info()

    logger.info(
        "hot_swap_starting",
        old_model=old_info["model_name"],
        new_model=model_name,
    )

    # Attempt to load the new model -- if this fails the old session
    # remains because load_model only overwrites on success.
    try:
        # Validate the new model by creating a temporary session first
        import onnxruntime as ort

        test_session = ort.InferenceSession(
            new_model_path, providers=["CPUExecutionProvider"]
        )
        # If validation passes, load into the engine
        del test_session
        engine.load_model(new_model_path)
        logger.info("hot_swap_complete", model=model_name)
    except Exception as e:
        logger.error(
            "hot_swap_failed",
            new_model=model_name,
            error=str(e),
        )
        raise


async def check_and_download(registry_url: str, model_dir: str) -> str | None:
    """Check for a newer model and download it if available.

    Args:
        registry_url: URL of the model registry API.
        model_dir: Directory to save the downloaded model.

    Returns:
        Path to the downloaded model, or None if no update available.
    """
    latest = await check_for_updates(registry_url)
    if latest is None:
        return None

    download_url = latest.get("download_url")
    version = latest.get("version", "unknown")
    name = latest.get("name", "model")

    if not download_url:
        return None

    target_path = os.path.join(model_dir, f"{name}_v{version}.onnx")

    # Skip download if this version already exists locally
    if os.path.exists(target_path):
        logger.info("model_already_exists", path=target_path)
        return None

    metadata_url = latest.get("metadata_url") or download_url.rsplit(".onnx", 1)[0] + ".json"
    await download_model(metadata_url, target_path.rsplit(".onnx", 1)[0] + ".json")
    return await download_model(download_url, target_path)


async def list_available(registry_url: str) -> list[dict]:
    """List available models from the model registry.

    Args:
        registry_url: URL of the model registry API.

    Returns:
        List of model metadata dicts.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(registry_url)
        response.raise_for_status()
        data = response.json()
        # Handle both list and paginated dict responses
        if isinstance(data, list):
            return data
        return data.get("models", [])
