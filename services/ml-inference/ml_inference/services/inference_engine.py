"""ONNX Runtime inference engine for anomaly detection.

Serves a PyTorch autoencoder exported to ONNX. The exported graph outputs the
per-sample reconstruction error directly; this engine converts it into an
anomaly score in [0, 1) using the threshold calibrated at training time.

Every model ``<name>.onnx`` ships with a sidecar ``<name>.json`` holding the
feature normalisation statistics and the calibrated error threshold.
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort

from shared.observability.logging_config import get_logger

logger = get_logger(__name__)


class InferenceEngine:
    """ONNX Runtime inference engine.

    Holds a single InferenceSession that can be hot-swapped at runtime
    without restarting the service.
    """

    def __init__(self):
        self._session: ort.InferenceSession | None = None
        self._input_name: str = ""
        self._model_name: str = ""
        self._model_version: str = ""
        self._loaded_at: datetime | None = None
        self._metadata: dict = {}
        self._error_threshold: float = 1.0

    def load_model(
        self,
        model_path: str,
        model_name: str = "",
        model_version: str = "",
    ) -> None:
        """Load an ONNX model and its metadata sidecar.

        Args:
            model_path: Filesystem path to the .onnx file.
            model_name: Human-readable model name for logging and metadata.
            model_version: Semantic version string of the model.
        """
        metadata_path = Path(model_path).with_suffix(".json")
        metadata = json.loads(metadata_path.read_text())

        session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )

        # Swap only after both files loaded successfully.
        self._session = session
        self._input_name = session.get_inputs()[0].name
        self._metadata = metadata
        self._error_threshold = float(metadata["error_threshold"])
        self._model_name = model_name or metadata.get("model_name", "")
        self._model_version = model_version or metadata.get("model_version", "")
        self._loaded_at = datetime.utcnow()
        logger.info(
            "model_loaded",
            model=self._model_name,
            version=self._model_version,
            path=model_path,
            error_threshold=self._error_threshold,
        )

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Return the raw reconstruction error for each row of ``features``."""
        if self._session is None:
            raise RuntimeError("No model loaded")
        (errors,) = self._session.run(
            None, {self._input_name: features.astype(np.float32, copy=False)}
        )
        return errors.reshape(-1)

    def score(self, features: np.ndarray) -> np.ndarray:
        """Return anomaly scores in [0, 1) for each row of ``features``.

        ``score = err / (err + threshold)``, so a reconstruction error equal to
        the calibrated threshold maps to exactly 0.5, and 9x the threshold
        maps to 0.9.
        """
        errors = self.predict(features)
        return errors / (errors + self._error_threshold)

    def predict_batch(self, batch: np.ndarray) -> np.ndarray:
        """Score a batch of feature rows. Alias of :meth:`score`."""
        return self.score(batch)

    @property
    def is_loaded(self) -> bool:
        """Whether a model is currently loaded."""
        return self._session is not None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def metadata(self) -> dict:
        """Metadata sidecar of the loaded model (feature stats, threshold, metrics)."""
        return self._metadata

    def get_info(self) -> dict:
        """Return metadata about the currently loaded model."""
        return {
            "model_name": self._model_name,
            "model_version": self._model_version,
            "loaded_at": str(self._loaded_at) if self._loaded_at else None,
        }
