"""ONNX Runtime inference engine for anomaly detection.

Wraps an onnxruntime.InferenceSession and provides predict/predict_batch
methods that operate on numpy arrays.
"""

from datetime import datetime

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
        self._model_name: str = ""
        self._model_version: str = ""
        self._loaded_at: datetime | None = None

    def load_model(
        self,
        model_path: str,
        model_name: str = "",
        model_version: str = "",
    ) -> None:
        """Load an ONNX model into an InferenceSession.

        Args:
            model_path: Filesystem path to the .onnx file.
            model_name: Human-readable model name for logging and metadata.
            model_version: Semantic version string of the model.
        """
        self._session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
        self._model_name = model_name
        self._model_version = model_version
        self._loaded_at = datetime.utcnow()
        logger.info(
            "model_loaded",
            model=model_name,
            version=model_version,
            path=model_path,
        )

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Run inference on a numpy array.

        Args:
            features: Input feature array, shape (1, n_features) or (batch, n_features).

        Returns:
            Model output array (anomaly scores or predictions).
        """
        if self._session is None:
            raise RuntimeError("No model loaded")
        input_name = self._session.get_inputs()[0].name
        results = self._session.run(
            None, {input_name: features.astype(np.float32)}
        )
        # Prefer decision scores (output[1]) over labels (output[0]) for
        # continuous anomaly scoring when available (e.g. IsolationForest).
        if len(results) > 1:
            return results[1]
        return results[0]

    def predict_batch(self, batch: np.ndarray) -> np.ndarray:
        """Run batch inference. Delegates to predict which handles any shape.

        Args:
            batch: Input array with shape (batch_size, n_features).

        Returns:
            Model output array with one result per batch item.
        """
        return self.predict(batch)

    @property
    def is_loaded(self) -> bool:
        """Whether a model is currently loaded."""
        return self._session is not None

    def get_info(self) -> dict:
        """Return metadata about the currently loaded model."""
        return {
            "model_name": self._model_name,
            "model_version": self._model_version,
            "loaded_at": str(self._loaded_at) if self._loaded_at else None,
        }
