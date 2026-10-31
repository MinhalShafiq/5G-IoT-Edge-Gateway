# ---------------------------------------------------------------------------
# Stage 1: train the PyTorch autoencoder and export it to ONNX.
# torch lives only in this stage; the runtime image ships ONNX Runtime alone.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS trainer

ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
WORKDIR /build

RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
    && pip install --no-cache-dir onnx==1.17.0

COPY shared/ /build/shared/
RUN pip install --no-cache-dir /build/shared/

COPY simulator/ /build/simulator/
RUN pip install --no-cache-dir /build/simulator/

COPY services/ml-inference/ /build/ml-inference/
RUN pip install --no-cache-dir /build/ml-inference/

# Only the architecture + training script are needed, not the full training service.
COPY services/ml-training/ml_training/ /build/ml-training/ml_training/
ENV PYTHONPATH=/build/ml-training

RUN python -m ml_training.training_scripts.train_edge_autoencoder --output-dir /models

# ---------------------------------------------------------------------------
# Stage 2: inference service (ONNX Runtime, no torch)
# ---------------------------------------------------------------------------
FROM python:3.11-slim

ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
WORKDIR /app

# Copy and install shared library first (better layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared

# Copy and install service
COPY services/ml-inference/ /app/service/
RUN pip install --no-cache-dir /app/service

# Trained model + metadata sidecar (feature stats, calibrated threshold, test metrics)
COPY --from=trainer /models/ /app/model_store/

EXPOSE 8004

CMD ["uvicorn", "ml_inference.main:app", "--host", "0.0.0.0", "--port", "8004"]
