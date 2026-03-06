# Increment 11 -- Distributed ML

## Goal

Real-time anomaly detection at the edge using ONNX Runtime, with models continuously updated from cloud training. This increment provides the complete ML inference pipeline: a background stream consumer reads telemetry from Redis, extracts features via a sliding window, runs ONNX inference, and publishes alerts when anomalies are detected. Models can be hot-swapped at runtime without downtime.

---

## Files Created

### ML Inference Service (`services/ml-inference/`)

This edge-side service is the real-time counterpart to the cloud ml-training service. It runs on edge gateways close to the sensor data sources, providing sub-millisecond anomaly detection with automatic model updates from the cloud.

---

### `pyproject.toml`

**Path:** `services/ml-inference/pyproject.toml`

Project definition for the ml-inference package. Dependencies:

- **fastapi** (>=0.104.0) -- HTTP framework for inference API and model management.
- **uvicorn[standard]** (>=0.24.0) -- ASGI server.
- **onnxruntime** (>=1.16.0) -- Microsoft ONNX Runtime for high-performance model inference.
- **scikit-learn** (>=1.3.0) -- Used for baseline IsolationForest model generation.
- **skl2onnx** (>=1.16.0) -- Converts scikit-learn models to ONNX format.
- **numpy** (>=1.26.0) -- Numerical computing for feature extraction.
- **redis** (>=5.0.0) -- Redis client for stream consumption.
- **pydantic-settings** (>=2.1.0) -- Settings management.
- **structlog** (>=23.2.0) -- Structured logging.
- **prometheus-client** (>=0.19.0) -- Metrics exposition.
- **httpx** (>=0.25.0) -- Async HTTP client for model registry polling.

Uses setuptools, includes `ml_inference*` packages. The shared library is referenced as a local path dependency at `../../shared`.

---

### `Dockerfile`

**Path:** `services/ml-inference/Dockerfile`

Docker build based on `python:3.11-slim`:

1. Installs `gcc` for native extension compilation.
2. Copies and installs the shared library, then the ml-inference service.
3. Creates `/app/model_store` directory for storing downloaded ONNX models.
4. Exposes port **8004**.
5. Runs uvicorn targeting `ml_inference.main:app` on port 8004.

Notably lighter than the ml-training Dockerfile -- no PyTorch or CUDA dependencies needed at the edge, only the lightweight ONNX Runtime.

---

### `ml_inference/__init__.py`

**Path:** `services/ml-inference/ml_inference/__init__.py`

Empty init file.

---

### `ml_inference/config.py`

**Path:** `services/ml-inference/ml_inference/config.py`

Configuration extending `BaseServiceSettings`:

| Setting | Default | Description |
|---|---|---|
| `service_name` | `"ml-inference"` | Service identifier |
| `http_port` | `8004` | FastAPI HTTP port |
| `model_dir` | `"/app/model_store"` | Directory for stored ONNX models |
| `anomaly_threshold` | `0.7` | Score threshold above which a reading is classified as anomalous |
| `consumer_group` | `"ml_inference"` | Redis Streams consumer group name |
| `consumer_name` | `"inference-worker-1"` | Individual consumer name within the group |
| `batch_size` | `32` | Number of stream entries to read per XREADGROUP call |
| `model_registry_url` | `"http://cloud-api:8080/api/v1/models"` | URL of the cloud model registry |
| `feature_window_size` | `10` | Number of recent readings per device for sliding window statistics |

The `anomaly_threshold` of 0.7 means that anomaly scores above 70% trigger an alert. Scores above 0.9 are classified as CRITICAL; between 0.7 and 0.9 as WARNING.

---

### `ml_inference/main.py`

**Path:** `services/ml-inference/ml_inference/main.py`

FastAPI application with lifespan management for the full inference pipeline.

**Startup sequence:**
1. Configures structured logging.
2. Initializes a `RedisClient` connection.
3. Creates an empty `InferenceEngine`.
4. Calls `model_manager.load_initial_model()` to load the most recent `.onnx` file from the model directory, or generate a baseline IsolationForest if none exists.
5. Creates a `FeatureExtractor` with the configured window size.
6. Ensures the `ml_inference` consumer group exists on the `raw_telemetry` Redis stream via `XGROUP CREATE`.
7. Creates an `AnomalyDetector` and launches it as an `asyncio.Task` that runs continuously in the background.
8. Stores all components on `app.state`.

**Shutdown sequence:**
1. Signals the `AnomalyDetector` to stop via `detector.stop()`.
2. Cancels the consumer task and awaits it (catching `CancelledError`).
3. Closes the Redis client.

**Routers mounted:**
- Shared health router at `GET /health`.
- Inference router at `POST /predict`, `POST /predict/batch`, `GET /inference/stats`.
- Models router at `GET /models/current`, `POST /models/reload`, `GET /models/available`.

**Design pattern:** Background task pattern. The anomaly detector runs as a long-lived asyncio task alongside the FastAPI request handlers, enabling concurrent stream consumption and API serving.

---

### `ml_inference/routers/__init__.py`

**Path:** `services/ml-inference/ml_inference/routers/__init__.py`

Empty init file.

---

### `ml_inference/routers/inference.py`

**Path:** `services/ml-inference/ml_inference/routers/inference.py`

REST API router for synchronous prediction requests. Uses FastAPI's `Depends()` for dependency injection of the `InferenceEngine`, `FeatureExtractor`, and anomaly threshold from `app.state`.

**Pydantic schemas:**
- **`PredictionResponse`** -- `device_id`, `anomaly_score` (0.0-1.0), `is_anomaly` (bool), `latency_ms`.
- **`BatchPredictionResponse`** -- `predictions` (list of PredictionResponse), `total_latency_ms`.
- **`InferenceStats`** -- `inference_count`, `avg_latency_ms`, `anomaly_count`.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/predict` | Single reading inference. Accepts a `TelemetryReading`, extracts features, runs ONNX inference, returns anomaly score and whether it exceeds the threshold. Records latency in the `INFERENCE_LATENCY` Prometheus histogram. Returns 503 if no model is loaded. |
| `POST` | `/predict/batch` | Batch inference on multiple readings. Iterates over each reading, extracting features and running inference individually. Returns per-reading results plus total latency. Returns 400 for empty batches. |
| `GET` | `/inference/stats` | Returns aggregate inference statistics: total count, average latency, and total anomaly count from an in-memory counter. |

The anomaly score is normalized to the [0, 1] range using `max(0.0, min(1.0, score))` to handle the varying output ranges of different model types (IsolationForest returns negative scores for inliers).

**Design pattern:** Dependency injection via FastAPI Depends. In-memory counters for lightweight performance tracking without external dependencies.

---

### `ml_inference/routers/models.py`

**Path:** `services/ml-inference/ml_inference/routers/models.py`

REST API router mounted at `/models` for model lifecycle management.

**Pydantic schemas:**
- **`ModelInfo`** -- `model_name`, `model_version`, `loaded_at`, `is_loaded`.
- **`ModelAvailable`** -- `name`, `version`, `download_url`, `created_at`.
- **`ReloadResponse`** -- `status` (unchanged/reloaded), `message`, `model_info`.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/models/current` | Returns metadata about the currently loaded ONNX model (name, version, load timestamp, loaded status). |
| `POST` | `/models/reload` | Triggers a model hot-swap: checks the cloud registry for a newer version, downloads it if available, validates it, and swaps it into the running engine. Returns `"unchanged"` if no newer version exists, `"reloaded"` on successful swap, or 500 on failure. |
| `GET` | `/models/available` | Lists all available models from the cloud registry. Returns 502 if the registry is unreachable. |

**Design pattern:** Blue-green deployment for models. The reload endpoint orchestrates the full check-download-validate-swap cycle.

---

### `ml_inference/services/__init__.py`

**Path:** `services/ml-inference/ml_inference/services/__init__.py`

Empty init file.

---

### `ml_inference/services/inference_engine.py`

**Path:** `services/ml-inference/ml_inference/services/inference_engine.py`

The `InferenceEngine` class wraps an `onnxruntime.InferenceSession` and provides the inference API.

**State:**
- `_session` -- The current ONNX Runtime InferenceSession, or `None` if no model is loaded.
- `_model_name`, `_model_version`, `_loaded_at` -- Metadata about the current model.

**Methods:**

- **`load_model(model_path, model_name, model_version)`** -- Creates a new `ort.InferenceSession` with `CPUExecutionProvider`. Stores the session reference and metadata. Logs the load event.

- **`predict(features)`** -- Runs inference:
  1. Gets the input name from `session.get_inputs()[0].name`.
  2. Casts the input to `float32`.
  3. Calls `session.run(None, {input_name: features})`.
  4. Returns the first output array.
  Raises `RuntimeError` if no model is loaded.

- **`predict_batch(batch)`** -- Delegates to `predict()`, which handles any batch size due to the dynamic batch axis configured during ONNX export.

- **`is_loaded`** (property) -- Returns `True` if a session exists.

- **`get_info()`** -- Returns a dict with `model_name`, `model_version`, `loaded_at`.

**Design pattern:** Wrapper/Adapter pattern. Provides a clean Python interface over the ONNX Runtime C++ engine. The session reference can be atomically swapped for hot-swap scenarios.

---

### `ml_inference/services/anomaly_detector.py`

**Path:** `services/ml-inference/ml_inference/services/anomaly_detector.py`

Background stream consumer that performs real-time anomaly detection. This is the core of the edge ML pipeline.

**`run()` main loop:**
```
while self._running:
    entries = await redis.stream_read_group(
        "raw_telemetry", "ml_inference", consumer_name, count=batch_size
    )
    for entry_id, data in entries:
        await self._process_entry(entry_id, data)
```

**`_process_entry(entry_id, data)`:**
1. Deserializes the stream entry into a `TelemetryReading` via `from_stream_dict()`.
2. Extracts features via `FeatureExtractor.extract()` (sliding window + statistics).
3. Runs ONNX inference via `InferenceEngine.predict()`, measuring latency with `time.perf_counter()`.
4. Records inference latency in the `INFERENCE_LATENCY` Prometheus histogram.
5. Normalizes the anomaly score to [0, 1].
6. If score > `anomaly_threshold`:
   - Determines severity: `CRITICAL` if score > 0.9, `WARNING` otherwise.
   - Creates an `Alert` object with device_id, score, model_version, severity, and the original reading payload.
   - Publishes the alert to the `alerts` Redis stream via `XADD`.
   - Increments the `ANOMALIES_DETECTED` Prometheus counter (labeled by device_type and severity).
7. Acknowledges the message via `XACK` regardless of outcome.
8. Increments the `STREAM_MESSAGES_CONSUMED` counter.
9. On processing error: logs the error and still ACKs the message to prevent infinite reprocessing (a dead-letter queue would be preferable in production).

**`stop()`:** Sets `_running = False` to signal the loop to exit.

**Design pattern:** Consumer group pattern with Redis Streams. XREADGROUP provides at-most-once delivery within the consumer group. XACK confirms processing. The continuous loop with error recovery ensures the detector is resilient to transient failures.

---

### `ml_inference/services/model_manager.py`

**Path:** `services/ml-inference/ml_inference/services/model_manager.py`

Model lifecycle management module providing functions for loading, downloading, and hot-swapping ONNX models.

**`load_initial_model(engine, settings)`:**
1. Creates the model directory if it does not exist.
2. Globs for `*.onnx` files sorted by modification time (newest first).
3. If no models found: calls `generate_baseline_model()` to create a fallback.
4. Loads the newest model into the engine with name extracted from the filename and version `"1.0.0"`.

**`generate_baseline_model(model_dir)`:**
1. Imports `IsolationForest` from scikit-learn and `to_onnx` from skl2onnx.
2. Generates 5000 synthetic normal sensor samples with 8 features. The centroids simulate typical sensor readings: `[25.0, 50.0, 1013.0, 1.0, 100.0, 25.0, 0.5, 1013.0]` (temperature, humidity, pressure, vibration, power, ambient temp, noise, barometric delta).
3. Trains an `IsolationForest` with `contamination=0.05` and 100 estimators.
4. Exports to ONNX via `to_onnx()` and saves to `baseline_anomaly_detector.onnx`.
5. Returns the file path, or `None` on failure.

**`check_for_updates(registry_url)`:**
Polls `{registry_url}/latest` via HTTP GET with a 10-second timeout. Returns the model metadata dict if a newer version exists, or `None`.

**`download_model(url, target_path)`:**
Downloads an `.onnx` file via HTTP GET with a 60-second timeout. Creates the target directory if needed. Returns the target path.

**`hot_swap(engine, new_model_path)`:**
Blue-green model swap:
1. Logs the swap attempt with old and new model names.
2. **Validates** the new model by creating a temporary `ort.InferenceSession`. If this fails, the old model remains active.
3. If validation passes: deletes the temporary session and loads the new model into the engine via `engine.load_model()`.
4. If validation fails: raises the exception, leaving the old model in place -- **zero downtime** either way.

**`check_and_download(registry_url, model_dir)`:**
Combined check-and-download:
1. Calls `check_for_updates()` to query the registry.
2. If a newer version exists: constructs a local path as `{name}_v{version}.onnx`.
3. Skips download if the file already exists locally.
4. Otherwise downloads and returns the path.

**`list_available(registry_url)`:**
Fetches all available models from the registry. Handles both list and paginated dict response formats.

**`_extract_version(model_path)`:**
Parses version strings from filenames matching the pattern `model_v1.2.3.onnx`. Falls back to `"1.0.0"`.

**Design pattern:** Blue-green deployment for ML models. The validation step prevents corrupted or incompatible models from taking down the inference service. Baseline model generation ensures the service can start even in a fresh deployment with no pre-trained models.

---

### `ml_inference/services/feature_extractor.py`

**Path:** `services/ml-inference/ml_inference/services/feature_extractor.py`

The `FeatureExtractor` class transforms raw telemetry readings into numeric feature vectors suitable for ONNX inference.

**Per-device sliding window:**
Maintains a dict `_windows: {device_id -> [recent_payloads]}` with a configurable window size (default 10 readings).

**`extract(reading)` method:**
1. Updates the sliding window for this device: appends the current payload, trims to `window_size`.
2. Extracts **current features**: all numeric values (int/float) from the reading's payload dict.
3. Computes **statistical features** from the sliding window:
   - Collects all numeric values from all payloads in the window.
   - Computes: `mean`, `std`, `min`, `max`.
   - If only one reading exists for this device (first reading), uses the current values as placeholders.
4. Returns a numpy array of shape `(1, n_features)` as `float32`.

The feature vector structure is: `[current_values..., window_mean, window_std, window_min, window_max]`.

**Auxiliary methods:**
- **`clear_window(device_id)`** -- Clears the window for a specific device.
- **`clear_all()`** -- Clears all windows.
- **`tracked_devices`** (property) -- Returns the number of devices currently tracked.

**Design pattern:** Sliding window feature engineering. By appending statistical features from recent history, the model gains temporal context that helps distinguish transient spikes (single anomalous readings) from persistent anomalies (sustained deviation from normal patterns).

---

### `ml_inference/models/__init__.py`

**Path:** `services/ml-inference/ml_inference/models/__init__.py`

Empty init file.

---

### `ml_inference/models/isolation_forest.py`

**Path:** `services/ml-inference/ml_inference/models/isolation_forest.py`

CLI script for training an IsolationForest anomaly detector and exporting to ONNX.

**Usage:**
```bash
python -m ml_inference.models.isolation_forest --output model_store/anomaly_detector.onnx
python -m ml_inference.models.isolation_forest --output model.onnx --samples 10000
```

**CLI arguments:** `--output` (default: `model_store/anomaly_detector.onnx`), `--samples` (default: 5000), `--features` (default: 8), `--contamination` (default: 0.05).

**`generate_training_data(n_samples, n_features)`:**
Generates synthetic normal sensor data with centroids representing typical IoT readings: `[25.0, 50.0, 1013.0, 1.0, 100.0, 25.0, 0.5, 1013.0]` with Gaussian noise (scale 0.5). Uses `RandomState(42)` for reproducibility.

**`train_and_export(output_path, n_samples, n_features, contamination)`:**
1. Generates synthetic data.
2. Trains an `IsolationForest` with 100 estimators and the specified contamination rate.
3. Exports to ONNX via `skl2onnx.to_onnx()` using a single sample for shape inference.
4. Writes the serialized ONNX bytes to disk.
5. Returns the trained model.

**Design pattern:** Scikit-learn to ONNX conversion pipeline. The IsolationForest is an unsupervised anomaly detection algorithm that learns the "normal" data distribution and flags outliers, making it ideal for IoT sensor data where labeled anomalies are rare.

---

### `ml_inference/models/export_onnx.py`

**Path:** `services/ml-inference/ml_inference/models/export_onnx.py`

Utility module for PyTorch to ONNX conversion.

**`export_pytorch_to_onnx(model, input_shape, output_path, opset_version=13)`:**
1. Sets the model to eval mode.
2. Creates a random dummy input tensor of the specified shape.
3. Calls `torch.onnx.export()` with:
   - Named I/O: `"input"` and `"output"`.
   - Dynamic batch axis on dimension 0.
   - Constant folding enabled.

**`verify_onnx_model(onnx_path, input_shape)`:**
1. Creates an ONNX Runtime InferenceSession.
2. Generates random test input.
3. Runs inference and prints input/output shapes and a sample of the output.

The CLI entry point prints usage instructions rather than running a conversion, as it requires the user to import their specific PyTorch model.

---

## Real-Time Anomaly Detection Pipeline

```
Redis Stream (raw_telemetry)
    |
    | XREADGROUP (consumer group: ml_inference, count: 32)
    v
AnomalyDetector._process_entry()
    |
    | Deserialize stream entry -> TelemetryReading
    v
FeatureExtractor.extract(reading)
    |
    | Per-device sliding window (10 readings)
    | Current numeric values + statistical features (mean, std, min, max)
    | -> numpy array of shape (1, n_features), dtype float32
    v
InferenceEngine.predict(features)
    |
    | ort.InferenceSession.run() with CPUExecutionProvider
    | -> anomaly_score (normalized to [0, 1])
    v
Score Evaluation
    |
    |-- score <= 0.7 --> XACK and continue (normal reading)
    |
    |-- 0.7 < score <= 0.9 --> Create Alert (severity: WARNING)
    |                          XADD to alerts stream
    |                          Increment ANOMALIES_DETECTED counter
    |                          XACK
    |
    |-- score > 0.9 --> Create Alert (severity: CRITICAL)
                        XADD to alerts stream
                        Increment ANOMALIES_DETECTED counter
                        XACK
```

---

## Model Hot-Swap (Blue-Green)

The hot-swap mechanism ensures zero-downtime model updates:

```
1. model_manager.check_for_updates()
   |
   | HTTP GET {registry_url}/latest
   | Returns model metadata (name, version, download_url)
   v
2. Is newer version available?
   |
   |-- No --> Return None, keep current model
   |
   |-- Yes --> model_manager.download_model()
               |
               | HTTP GET download_url -> save to model_dir/{name}_v{version}.onnx
               v
3. model_manager.hot_swap(engine, new_model_path)
   |
   | Step 1: Create temporary InferenceSession with new model
   |         (VALIDATION STEP)
   |
   |-- Validation FAILS --> Old model stays active
   |                        Log error
   |                        Raise exception
   |                        Zero downtime
   |
   |-- Validation PASSES --> Delete temp session
                             engine.load_model(new_model_path)
                             New session replaces old session
                             Log success
                             Zero downtime
```

The blue-green approach means that at any point during the swap:
- Either the old model is serving (if validation has not completed).
- Or the new model is serving (if validation succeeded and load_model was called).
- There is never a window where no model is loaded.

---

## Design Patterns Summary

| Pattern | Where Used | Purpose |
|---|---|---|
| Consumer group | `AnomalyDetector.run()` | Reliable stream consumption with XREADGROUP/XACK |
| Sliding window | `FeatureExtractor` | Temporal context for anomaly detection via per-device statistics |
| Blue-green deployment | `model_manager.hot_swap()` | Zero-downtime model updates with validation |
| Baseline fallback | `model_manager.generate_baseline_model()` | Ensures inference is available even without pre-trained models |
| Wrapper/Adapter | `InferenceEngine` | Clean Python interface over ONNX Runtime C++ engine |
| Background task | `main.py` lifespan | Long-running stream consumer alongside request handlers |
| Dependency injection | `routers/inference.py` | FastAPI Depends for engine and extractor access |
| In-memory counters | `routers/inference.py` | Lightweight inference statistics without external dependencies |
| Prometheus instrumentation | `AnomalyDetector` | ANOMALIES_DETECTED, STREAM_MESSAGES_CONSUMED, INFERENCE_LATENCY metrics |
