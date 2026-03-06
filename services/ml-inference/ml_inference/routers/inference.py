"""Inference API routes for synchronous prediction requests."""

import math
import time
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shared.models.telemetry import TelemetryReading
from shared.observability.metrics import INFERENCE_LATENCY

from ml_inference.services.inference_engine import InferenceEngine
from ml_inference.services.feature_extractor import FeatureExtractor

router = APIRouter(tags=["inference"])

# In-memory stats counters
_stats = {
    "inference_count": 0,
    "total_latency_ms": 0.0,
    "anomaly_count": 0,
}


class PredictionResponse(BaseModel):
    """Response for a single prediction request."""

    device_id: str
    anomaly_score: float = Field(..., ge=0.0, le=1.0)
    is_anomaly: bool
    latency_ms: float


class BatchPredictionResponse(BaseModel):
    """Response for a batch prediction request."""

    predictions: list[PredictionResponse]
    total_latency_ms: float


class InferenceStats(BaseModel):
    """Inference statistics."""

    inference_count: int
    avg_latency_ms: float
    anomaly_count: int


def _get_engine(request: Request) -> InferenceEngine:
    return request.app.state.engine


def _get_extractor(request: Request) -> FeatureExtractor:
    return request.app.state.feature_extractor


def _get_threshold(request: Request) -> float:
    return request.app.state.settings.anomaly_threshold


@router.post("/predict", response_model=PredictionResponse)
async def predict(
    reading: TelemetryReading,
    engine: Annotated[InferenceEngine, Depends(_get_engine)],
    extractor: Annotated[FeatureExtractor, Depends(_get_extractor)],
    request: Request,
):
    """Run synchronous inference on a single telemetry reading.

    Returns the anomaly score and whether it exceeds the configured threshold.
    """
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="No model loaded")

    threshold = _get_threshold(request)

    start = time.perf_counter()
    features = extractor.extract(reading)
    scores = engine.predict(features)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # IsolationForest decision scores: positive = normal, negative = anomaly
    raw = float(np.asarray(scores).flat[0])
    # Sigmoid to map to [0, 1]: negative scores → high anomaly score
    anomaly_score = max(0.0, min(1.0, 1.0 / (1.0 + math.exp(40.0 * raw))))
    is_anomaly = anomaly_score > threshold

    INFERENCE_LATENCY.labels(model_name=engine._model_name or "default").observe(
        elapsed_ms / 1000
    )

    _stats["inference_count"] += 1
    _stats["total_latency_ms"] += elapsed_ms
    if is_anomaly:
        _stats["anomaly_count"] += 1

    return PredictionResponse(
        device_id=str(reading.device_id),
        anomaly_score=anomaly_score,
        is_anomaly=is_anomaly,
        latency_ms=round(elapsed_ms, 3),
    )


@router.post("/predict/batch", response_model=BatchPredictionResponse)
async def predict_batch(
    readings: list[TelemetryReading],
    engine: Annotated[InferenceEngine, Depends(_get_engine)],
    extractor: Annotated[FeatureExtractor, Depends(_get_extractor)],
    request: Request,
):
    """Run batch inference on multiple telemetry readings."""
    if not engine.is_loaded:
        raise HTTPException(status_code=503, detail="No model loaded")

    if not readings:
        raise HTTPException(status_code=400, detail="Empty batch")

    threshold = _get_threshold(request)

    start = time.perf_counter()

    predictions = []
    for reading in readings:
        item_start = time.perf_counter()
        features = extractor.extract(reading)
        scores = engine.predict(features)
        item_elapsed_ms = (time.perf_counter() - item_start) * 1000

        raw = float(np.asarray(scores).flat[0])
        anomaly_score = max(0.0, min(1.0, 1.0 / (1.0 + math.exp(40.0 * raw))))
        is_anomaly = anomaly_score > threshold

        _stats["inference_count"] += 1
        _stats["total_latency_ms"] += item_elapsed_ms
        if is_anomaly:
            _stats["anomaly_count"] += 1

        predictions.append(
            PredictionResponse(
                device_id=str(reading.device_id),
                anomaly_score=anomaly_score,
                is_anomaly=is_anomaly,
                latency_ms=round(item_elapsed_ms, 3),
            )
        )

    total_elapsed_ms = (time.perf_counter() - start) * 1000

    INFERENCE_LATENCY.labels(model_name=engine._model_name or "default").observe(
        total_elapsed_ms / 1000
    )

    return BatchPredictionResponse(
        predictions=predictions,
        total_latency_ms=round(total_elapsed_ms, 3),
    )


@router.get("/inference/stats", response_model=InferenceStats)
async def inference_stats():
    """Return inference statistics: count, average latency, anomaly count."""
    count = _stats["inference_count"]
    avg_latency = (
        _stats["total_latency_ms"] / count if count > 0 else 0.0
    )
    return InferenceStats(
        inference_count=count,
        avg_latency_ms=round(avg_latency, 3),
        anomaly_count=_stats["anomaly_count"],
    )
