"""Model lifecycle management.

Handles loading, downloading, hot-swapping, and baseline model generation
for the ONNX inference engine.
"""

import os
import glob
from pathlib import Path

import httpx
import numpy as np

from shared.observability.logging_config import get_logger

from ml_inference.config import Settings
from ml_inference.services.inference_engine import InferenceEngine

logger = get_logger(__name__)


def load_initial_model(engine: InferenceEngine, settings: Settings) -> None:
    """Load the most recent .onnx model from the model directory.

    If no model files exist, generates a baseline IsolationForest model
    so the service can start and run inference immediately.

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
        logger.warning("no_onnx_models_found", model_dir=model_dir)
        logger.info("generating_baseline_model")
        baseline_path = generate_baseline_model(model_dir)
        if baseline_path:
            onnx_files = [baseline_path]
        else:
            logger.error("baseline_model_generation_failed")
            return

    model_path = onnx_files[0]
    model_name = Path(model_path).stem
    logger.info("loading_initial_model", path=model_path, name=model_name)
    engine.load_model(model_path, model_name=model_name, model_version="1.0.0")


def generate_baseline_model(model_dir: str) -> str | None:
    """Train a simple IsolationForest and export to ONNX as a fallback model.

    This creates a baseline anomaly detector trained on synthetic normal
    sensor data so the service can function even without a pre-trained model.

    Args:
        model_dir: Directory to save the generated .onnx file.

    Returns:
        Path to the generated model file, or None on failure.
    """
    try:
        from sklearn.ensemble import IsolationForest
        from skl2onnx import to_onnx

        n_features = 8
        rng = np.random.RandomState(42)

        # Generate synthetic "normal" data matching the feature extractor output.
        # The extractor produces: [val1, val2, win_mean, win_std, win_min, win_max, pad, pad]
        # Window stats are computed over sensor values only (excludes anomaly flag/bools).
        def _gen(n, v1_range, v2_range):
            v1 = rng.uniform(*v1_range, size=(n, 1))
            v2 = rng.uniform(*v2_range, size=(n, 1))
            win_mean = (v1 + v2) / 2.0
            win_std = np.abs(v1 - v2) / 2.0 * (0.8 + 0.4 * rng.rand(n, 1))
            win_min = np.minimum(v1, v2) - rng.rand(n, 1) * 0.5
            win_max = np.maximum(v1, v2) + rng.rand(n, 1) * 0.5
            pad = np.zeros((n, 2))
            return np.hstack([v1, v2, win_mean, win_std, win_min, win_max, pad])

        # Temperature: 18-28 °C, humidity: 40-60 %
        temp = _gen(2000, (18.0, 28.0), (40.0, 60.0))
        # Pressure: 1008-1018 hPa, flow: 10-50 L/min
        pres = _gen(2000, (1008.0, 1018.0), (10.0, 50.0))
        # Vibration: 0.5-2.0 mm/s, freq: 50-200 Hz
        vib = _gen(1000, (0.5, 2.0), (50.0, 200.0))

        normal_data = np.vstack([temp, pres, vib])
        rng.shuffle(normal_data)

        model = IsolationForest(
            contamination=0.05, random_state=42, n_estimators=100
        )
        model.fit(normal_data)

        onx = to_onnx(
            model,
            normal_data[:1].astype(np.float32),
            target_opset={"": 17, "ai.onnx.ml": 3},
        )

        output_path = os.path.join(model_dir, "baseline_anomaly_detector.onnx")
        with open(output_path, "wb") as f:
            f.write(onx.SerializeToString())

        logger.info("baseline_model_generated", path=output_path)
        return output_path

    except Exception as e:
        logger.error("baseline_model_generation_error", error=str(e))
        return None


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
        engine.load_model(
            new_model_path,
            model_name=model_name,
            model_version=_extract_version(new_model_path),
        )
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


def _extract_version(model_path: str) -> str:
    """Try to extract a version string from the model filename.

    Looks for patterns like 'model_v1.2.3.onnx'.
    Falls back to '1.0.0' if no version is found.
    """
    name = Path(model_path).stem
    parts = name.split("_v")
    if len(parts) > 1:
        return parts[-1]
    return "1.0.0"
