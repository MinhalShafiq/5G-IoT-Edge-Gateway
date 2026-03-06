# Increment 9 -- Datacenter & Cloud

## Goal

Provide cloud infrastructure for batch analytics, heavier ML training, and a model registry. The cloud-api receives data from edge gateways while the ml-training service trains PyTorch models and exports them to ONNX format for deployment back to the edge.

---

## Files Created

### ML Training Service (`services/ml-training/`)

This service is the cloud-side counterpart of the edge ml-inference service. It trains PyTorch neural network models on accumulated sensor data from PostgreSQL and exports them as ONNX files that can be pushed to the model registry for edge gateway consumption.

---

### `pyproject.toml`

**Path:** `services/ml-training/pyproject.toml`

Project definition for the ml-training package. Declares the following runtime dependencies:

- **fastapi** (>=0.104.0) -- Async HTTP framework for the training job management API.
- **uvicorn[standard]** (>=0.24.0) -- ASGI server with libuv event loop.
- **torch** (>=2.1.0) -- PyTorch deep learning framework for model training.
- **numpy** (>=1.26.0) -- Numerical computing for data manipulation and feature extraction.
- **sqlalchemy[asyncio]** (>=2.0.23) -- Async ORM for PostgreSQL data loading.
- **asyncpg** (>=0.29.0) -- High-performance PostgreSQL driver.
- **redis** (>=5.0.0) -- Redis client for stream integration.
- **pydantic-settings** (>=2.1.0) -- Settings management via environment variables.
- **structlog** (>=23.2.0) -- Structured logging.
- **prometheus-client** (>=0.19.0) -- Prometheus metrics exposition.
- **httpx** (>=0.25.0) -- Async HTTP client for model registry communication.

The package uses setuptools as its build backend and exposes a console script entry point `ml-training` that calls `ml_training.main:run`. The shared library is installed as a path dependency from `../../shared`.

---

### `Dockerfile`

**Path:** `services/ml-training/Dockerfile`

Multi-stage Docker build based on `python:3.11-slim`. The build process:

1. Sets `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1` to prevent `.pyc` files and enable immediate log output.
2. Installs system dependencies: `gcc` and `libpq-dev` (required by asyncpg and torch C extensions).
3. Copies and installs the `shared/` library first to leverage Docker layer caching -- the shared library changes less frequently than service code.
4. Copies and installs the `services/ml-training/` package.
5. Creates the `/models` directory for ONNX model output.
6. Sets the working directory to `/app/services/ml-training` and exposes port **8006**.
7. Runs uvicorn targeting `ml_training.main:app` on port 8006.

---

### `ml_training/__init__.py`

**Path:** `services/ml-training/ml_training/__init__.py`

Module docstring only. Marks the `ml_training` directory as a Python package.

---

### `ml_training/config.py`

**Path:** `services/ml-training/ml_training/config.py`

Configuration module that extends the shared `BaseServiceSettings` class using pydantic-settings. Key settings:

| Setting | Default | Description |
|---|---|---|
| `service_name` | `"ml-training"` | Identifies this service in logs and metrics |
| `http_port` | `8006` | Port for the FastAPI HTTP server |
| `model_output_dir` | `"/models"` | Directory where trained ONNX models are saved |
| `model_registry_url` | `"http://cloud-api:8080/api/v1/models"` | URL of the cloud model registry API |
| `training_batch_size` | `64` | Default mini-batch size for training |
| `training_epochs` | `50` | Default number of training epochs |
| `learning_rate` | `0.001` | Default Adam optimizer learning rate |
| `device` | Auto-detected | `"cuda"` if `torch.cuda.is_available()` else `"cpu"` |

The `device` field auto-detects GPU availability at import time, enabling transparent CUDA acceleration when a GPU is present.

**Design pattern:** Pydantic Settings with environment variable override. All fields can be overridden via environment variables (e.g., `TRAINING_EPOCHS=100`).

---

### `ml_training/main.py`

**Path:** `services/ml-training/ml_training/main.py`

FastAPI application entry point with lifespan management. The `lifespan` async context manager handles:

**Startup sequence:**
1. Configures structured logging via `setup_logging()`.
2. Initializes a `RedisClient` connection for stream integration.
3. Initializes PostgreSQL via `init_db()`, creating tables if missing.
4. Stores `Settings` on `app.state` for dependency injection.
5. Creates and wires together the four service components:
   - `ExperimentTracker` -- in-memory metric storage
   - `TelemetryDataLoader` -- PostgreSQL data reader
   - `ModelExporter` -- ONNX export and registry integration
   - `Trainer` -- orchestrates the training lifecycle

**Shutdown sequence:**
1. Closes the Redis client connection.
2. Closes the PostgreSQL connection pool.

The `create_app()` factory function mounts three routers under `/api/v1`: health, training_jobs, and experiments. A `run()` function provides the console script entry point with hot-reload in development mode.

**Design pattern:** Lifespan context manager for resource management (no deprecated `@app.on_event` handlers). Dependency injection via `app.state`.

---

### `ml_training/routers/__init__.py`

**Path:** `services/ml-training/ml_training/routers/__init__.py`

Module docstring only. Marks the routers directory as a Python package.

---

### `ml_training/routers/training_jobs.py`

**Path:** `services/ml-training/ml_training/routers/training_jobs.py`

REST API router mounted at `/api/v1/jobs` for managing training jobs. Defines three Pydantic schemas:

- **`TrainingJobCreate`** -- Request body for creating a job: `model_type` (default `"autoencoder"`; also supports `"isolation_forest"` and `"lstm"`), `epochs` (default 50), `batch_size` (default 64), `learning_rate` (default 0.001), and `data_query` (dict for filtering training data by device_type, start_time, end_time, and limit).
- **`TrainingJob`** -- Full job representation: `job_id`, `model_type`, `status` (pending/running/completed/failed/cancelled), `epochs_completed`, `total_epochs`, `train_loss`, `val_loss`, `model_path`, `created_at`, `completed_at`, `error_message`.
- **`TrainingJobSummary`** -- Abbreviated version for list endpoints.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/jobs` | Create a new training job. Returns 201 immediately with the job record in `pending` status. Training runs asynchronously in a thread pool. |
| `GET` | `/api/v1/jobs` | List all training jobs with summary info, newest first. |
| `GET` | `/api/v1/jobs/{job_id}` | Get detailed info for a specific job including per-epoch loss metrics. Returns 404 if not found. |
| `DELETE` | `/api/v1/jobs/{job_id}` | Cancel a running or pending job. Returns 404 if job is not found or already completed. |

**Design pattern:** Asynchronous job dispatch. The POST endpoint returns immediately with a job ID while training proceeds in the background. Clients poll the GET endpoint to monitor progress.

---

### `ml_training/routers/experiments.py`

**Path:** `services/ml-training/ml_training/routers/experiments.py`

REST API router mounted at `/api/v1/experiments` for browsing training experiments and their per-epoch metrics. Defines three Pydantic schemas:

- **`EpochMetric`** -- Single observation: `epoch`, `metric_name`, `value`.
- **`ExperimentSummary`** -- Summary with `job_id`, `model_type`, `config`, `status`, `best_train_loss`, `best_val_loss`, `created_at`. Best losses are computed by finding the minimum across all recorded epoch metrics.
- **`ExperimentMetrics`** -- Detailed per-epoch metrics list with `total_epochs_logged` count.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/experiments` | List all experiments with their best train/val loss. |
| `GET` | `/api/v1/experiments/{experiment_id}/metrics` | Get per-epoch train_loss and val_loss for an experiment. Returns 404 if not found. |

The experiment tracker is accessed from `request.app.state.experiment_tracker`.

---

### `ml_training/routers/health.py`

**Path:** `services/ml-training/ml_training/routers/health.py`

Health check router created via the shared `create_health_router("ml-training")` factory. Exposes a `GET /health` endpoint that returns `{"status": "ok", "service": "ml-training"}`.

**Design pattern:** Shared health router factory for consistent health check endpoints across all services.

---

### `ml_training/services/__init__.py`

**Path:** `services/ml-training/ml_training/services/__init__.py`

Empty init file marking the services directory as a Python package.

---

### `ml_training/services/trainer.py`

**Path:** `services/ml-training/ml_training/services/trainer.py`

Core training engine that orchestrates the full lifecycle of training jobs. The `Trainer` class maintains:

- `_jobs` -- Dict of `job_id -> TrainingJob` for status tracking.
- `_cancelled` -- Set of job IDs marked for cancellation.
- `_device` -- PyTorch device (CPU or CUDA).

**Key methods:**

- **`start_training(job_config)`** -- Async method that:
  1. Generates a UUID job_id and creates a `TrainingJob` record in `pending` status.
  2. Creates an experiment entry in the `ExperimentTracker`.
  3. Launches an async coroutine that first loads data from PostgreSQL via `TelemetryDataLoader`, then dispatches the blocking `_train()` method to `loop.run_in_executor()`.
  4. Falls back to synthetic random data (1000 train, 200 val samples with 8 features) if PostgreSQL loading fails.
  5. Returns the job record immediately.

- **`_train(job_record, job_config, x_train, x_val)`** -- Blocking training loop (runs in thread pool):
  1. Sets job status to `running`.
  2. Creates the model via `_create_model()` and moves it to the configured device.
  3. Sets up an Adam optimizer and MSE loss criterion.
  4. Converts numpy arrays to PyTorch tensors on the target device.
  5. Runs the epoch loop with mini-batch gradient descent, shuffling indices each epoch.
  6. For LSTM models, unsqueezes input to `(batch, 1, features)` for single-step sequences.
  7. Computes validation loss in `model.eval()` mode with `torch.no_grad()`.
  8. Logs `train_loss` and `val_loss` to the `ExperimentTracker` every epoch.
  9. Checks for cancellation between epochs via the `_cancelled` set.
  10. On completion: exports to ONNX via `ModelExporter`, updates status to `completed`.
  11. On failure: sets status to `failed` with the error message and traceback.

- **`list_jobs()`** -- Returns all jobs sorted newest first.
- **`get_job(job_id)`** -- Returns a single job by ID.
- **`cancel_job(job_id)`** -- Adds the job_id to `_cancelled`, sets status to `cancelled`, and updates the experiment tracker.
- **`_create_model(model_type, input_dim)`** -- Factory method that instantiates `Autoencoder` or `LSTMPredictor` based on the `model_type` string.

**Design pattern:** Thread-pool executor for CPU-bound work. The async event loop delegates blocking PyTorch training to a background thread, preventing the FastAPI server from becoming unresponsive. Cooperative cancellation via a shared `_cancelled` set checked at epoch boundaries.

---

### `ml_training/services/data_loader.py`

**Path:** `services/ml-training/ml_training/services/data_loader.py`

The `TelemetryDataLoader` class loads telemetry data from PostgreSQL and converts it into numpy arrays suitable for model training.

**`load_training_data(device_type, start_time, end_time, limit)`:**
1. Creates an async SQLAlchemy engine connected to the PostgreSQL DSN.
2. Builds a dynamic SQL query against the `telemetry_readings` table with optional `WHERE` filters for `device_type`, `start_time`, and `end_time`.
3. Fetches up to `limit` rows (default 50,000) ordered by timestamp descending.
4. Calls `_extract_features()` to convert JSON payloads into a numeric feature matrix.
5. Splits the data 80/20 into training and validation sets.
6. Ensures both splits have at least one sample.
7. Returns `(x_train, x_val)` as `float32` numpy arrays.

**`_extract_features(rows)`:**
1. Iterates over each row's `payload` column, parsing JSON strings if needed.
2. Extracts all numeric values (int/float, excluding booleans) from the payload dict. Handles nested dicts (e.g., `{"temperature": {"value": 22.5}}`).
3. Pads or truncates each feature vector to a consistent width (the maximum observed width, capped at 16).
4. Normalizes the feature matrix to zero-mean, unit-variance (z-score normalization) to improve training convergence.

**Design pattern:** Data pipeline with normalization. The loader bridges the gap between raw JSON sensor payloads and the fixed-width numeric tensors that neural networks require.

---

### `ml_training/services/model_exporter.py`

**Path:** `services/ml-training/ml_training/services/model_exporter.py`

The `ModelExporter` class handles ONNX conversion and model registry integration.

**`export_to_onnx(model, input_shape, output_path, opset_version=13)`:**
1. Sets the model to eval mode.
2. Creates a dummy input tensor on the same device as the model.
3. Calls `torch.onnx.export()` with:
   - `export_params=True` -- embeds trained weights in the ONNX file.
   - `do_constant_folding=True` -- optimizes constant expressions.
   - Named inputs/outputs: `"input"` and `"output"`.
   - **Dynamic batch axis** on dimension 0 for both input and output, enabling variable batch sizes at inference time.
4. Logs the exported file size and returns the output path.

**`register_model(name, version, framework, file_path, metrics, description)`:**
1. Constructs a JSON payload with model metadata (name, version, framework, file_url, description, metrics).
2. Sends an async HTTP POST to the model registry URL via httpx with a 30-second timeout.
3. Returns the registry response dict on success, or `None` on failure.
4. Handles `HTTPStatusError` and `RequestError` with structured error logging.

**Design pattern:** ONNX as the interchange format. PyTorch models are exported to a framework-agnostic format that the edge ml-inference service can run via ONNX Runtime without needing PyTorch installed.

---

### `ml_training/services/experiment_tracker.py`

**Path:** `services/ml-training/ml_training/services/experiment_tracker.py`

Thread-safe in-memory experiment tracking. The `ExperimentTracker` class stores per-epoch metrics, hyperparameters, and final results for each training job. It uses a `threading.Lock` because the training loop runs in a background thread while the API reads happen on the async event loop thread.

**Methods:**

- **`create_experiment(job_id, model_type, config)`** -- Creates a new experiment entry with status `"pending"`, an empty metrics list, and a UTC creation timestamp. Returns the experiment dict.
- **`log_metric(job_id, epoch, metric_name, value)`** -- Appends a metric observation `{epoch, metric_name, value, timestamp}` to the experiment's metrics list. Silently warns if the experiment is unknown.
- **`update_status(job_id, status)`** -- Updates the status field (running/completed/failed/cancelled).
- **`get_experiment(job_id)`** -- Returns a shallow copy of the experiment dict (with a copied metrics list to avoid mutations outside the lock), or `None` if not found.
- **`list_experiments()`** -- Returns all experiments sorted newest-first, each with a copied metrics list.

**Design pattern:** Monitor pattern with threading.Lock for thread safety. Defensive copying on reads prevents external mutation of internal state.

---

### `ml_training/architectures/__init__.py`

**Path:** `services/ml-training/ml_training/architectures/__init__.py`

Empty init file marking the architectures directory as a Python package.

---

### `ml_training/architectures/autoencoder.py`

**Path:** `services/ml-training/ml_training/architectures/autoencoder.py`

Defines the `Autoencoder` class, a symmetric autoencoder `nn.Module` for reconstruction-based anomaly detection.

**Architecture:**
```
Encoder: input_dim -> Linear(16) -> ReLU -> Linear(encoding_dim) -> ReLU
Decoder: encoding_dim -> Linear(16) -> ReLU -> Linear(input_dim)
```

Default parameters: `input_dim=8`, `encoding_dim=4`.

**Methods:**

- **`forward(x)`** -- Full encode-decode pass. Input shape `(batch_size, input_dim)`, output shape `(batch_size, input_dim)`.
- **`encode(x)`** -- Encoder only, returns the latent representation. Useful for feature embedding extraction.
- **`get_reconstruction_error(x)`** -- Computes per-sample MSE: `mean((x - reconstructed)^2, dim=1)`. Returns a tensor of shape `(batch_size,)`. Higher reconstruction error indicates anomaly because the model has only seen normal data during training.

**Design pattern:** Bottleneck autoencoder for unsupervised anomaly detection. Normal data compresses well through the bottleneck; anomalous data produces high reconstruction error because the model never learned to represent those patterns.

---

### `ml_training/architectures/lstm_predictor.py`

**Path:** `services/ml-training/ml_training/architectures/lstm_predictor.py`

Defines the `LSTMPredictor` class, a multi-layer LSTM `nn.Module` for time-series forecasting.

**Architecture:**
- Multi-layer LSTM with `batch_first=True`, configurable `hidden_dim`, `num_layers`, and `dropout` (only applied when `num_layers > 1`).
- Fully-connected output layer: `Linear(hidden_dim, output_dim)`.

Default parameters: `input_dim=8`, `hidden_dim=32`, `num_layers=2`, `output_dim=8`, `dropout=0.1`.

**Methods:**

- **`forward(x)`** -- Processes the input sequence `(batch_size, seq_len, input_dim)` through the LSTM, takes the last timestep's hidden output, and projects it through the FC layer to produce a prediction of shape `(batch_size, output_dim)`.
- **`predict_sequence(x, n_steps=1)`** -- Auto-regressive multi-step prediction. For each future step:
  1. Runs `forward()` to get the next prediction.
  2. Shifts the input window: drops the oldest timestep, appends the prediction.
  3. Repeats for `n_steps`.
  Returns shape `(batch_size, n_steps, output_dim)`.

**Design pattern:** Sequence-to-one prediction with auto-regressive extension. The model predicts the next sensor reading from a historical sequence; anomalies manifest as large deviations between predicted and actual values.

---

### `ml_training/training_scripts/__init__.py`

**Path:** `services/ml-training/ml_training/training_scripts/__init__.py`

Empty init file marking the training_scripts directory as a Python package.

---

### `ml_training/training_scripts/train_anomaly.py`

**Path:** `services/ml-training/ml_training/training_scripts/train_anomaly.py`

CLI script for standalone anomaly model training outside the FastAPI service.

**Usage:**
```bash
python -m ml_training.training_scripts.train_anomaly --model autoencoder --epochs 50
python -m ml_training.training_scripts.train_anomaly --model lstm --epochs 100 --lr 0.0005
```

**CLI arguments:**
- `--model` -- Architecture: `autoencoder` or `lstm` (default: autoencoder).
- `--epochs` -- Number of training epochs (default: 50).
- `--batch-size` -- Mini-batch size (default: 64).
- `--lr` -- Learning rate (default: 0.001).
- `--device-type` -- Filter training data by IoT device type.
- `--output-dir` -- Model output directory (default: from settings).
- `--register` -- Flag to register the model with the cloud registry after training.
- `--version` -- Model version string for registry (default: `"1.0.0"`).

**Flow:**
1. Configures structured logging for CLI output.
2. Attempts to load training data from PostgreSQL via `TelemetryDataLoader`. Falls back to synthetic random data (2000 train, 400 val, 8 features) on failure.
3. Creates the model via `create_model()`.
4. Runs the training loop with Adam optimizer, MSE loss, mini-batch gradient descent, and per-epoch validation.
5. Tracks `best_val_loss` and logs progress every 10 epochs.
6. Exports the trained model to ONNX via `ModelExporter.export_to_onnx()` with appropriate input shapes (LSTM gets 3D input, autoencoder gets 2D).
7. If `--register` is set, calls `register_model()` to POST metadata to the cloud-api model registry.

---

### `ml_training/training_scripts/evaluate.py`

**Path:** `services/ml-training/ml_training/training_scripts/evaluate.py`

CLI script for evaluating trained anomaly detection models.

**Usage:**
```bash
python -m ml_training.training_scripts.evaluate --model-path /models/anomaly-autoencoder-v1.0.0.onnx
python -m ml_training.training_scripts.evaluate --model-path /models/model.onnx --threshold 0.05
```

**CLI arguments:**
- `--model-path` (required) -- Path to the model file (ONNX or PyTorch .pt).
- `--model-type` -- Architecture type: `autoencoder` or `lstm` (default: autoencoder).
- `--input-dim` -- Number of input features (default: 8).
- `--threshold` -- Anomaly threshold for reconstruction error. If not set, uses `mean + 2*std` of the reconstruction error on the test set.
- `--device-type` -- Filter test data by device type.
- `--anomaly-ratio` -- Expected fraction of anomalies for synthetic label generation (default: 0.05).
- `--n-samples` -- Number of test samples (default: 5000).

**Flow:**
1. Loads the model (PyTorch .pt via `load_pytorch_model()`, or creates an equivalent fresh model for ONNX evaluation).
2. Loads test data from PostgreSQL or generates synthetic data with injected anomalies (shifted mean and higher variance).
3. Computes per-sample reconstruction error via `compute_reconstruction_error()`.
4. Applies the threshold to classify samples as normal (0) or anomaly (1).
5. Computes classification metrics via `compute_metrics()`:
   - **Precision** -- TP / (TP + FP)
   - **Recall** -- TP / (TP + FN)
   - **F1 Score** -- Harmonic mean of precision and recall
   - **Accuracy** -- (TP + TN) / total
   - Full **confusion matrix** (TP, FP, FN, TN)
6. Prints a formatted evaluation report to stdout.

---

## How the ML Pipeline Works

```
1. Sensor data accumulates in PostgreSQL
   (via data-persistence service consuming from Redis Streams)
        |
        v
2. User submits training job via POST /api/v1/jobs
   (specifying model_type, epochs, batch_size, learning_rate, data_query)
        |
        v
3. Trainer loads data from PostgreSQL telemetry_readings table
   - Extracts numeric features from JSON payloads
   - Normalizes to zero-mean, unit-variance
   - Splits 80/20 train/validation
   - Falls back to synthetic data if DB unavailable
        |
        v
4. Training runs in thread-pool executor (non-blocking)
   - Adam optimizer + MSE loss
   - Mini-batch gradient descent with shuffled indices
   - Per-epoch validation and metric logging
   - Cooperative cancellation check between epochs
        |
        v
5. On completion: exports to ONNX with dynamic batch axis
   - Registers model with cloud-api model registry
        |
        v
6. Edge ml-inference service polls registry
   - Downloads new ONNX model
   - Performs blue-green hot-swap
   - Continues real-time inference with updated model
```

---

## Design Patterns Summary

| Pattern | Where Used | Purpose |
|---|---|---|
| Thread-pool executor | `Trainer._train()` | Prevents blocking PyTorch training from stalling the async event loop |
| Factory method | `Trainer._create_model()` | Instantiates different architectures by name |
| ONNX interchange format | `ModelExporter` | Framework-agnostic model serialization for edge deployment |
| Monitor pattern | `ExperimentTracker` | Thread-safe in-memory state with `threading.Lock` |
| Pydantic settings | `config.py` | Type-safe configuration with environment variable override |
| Lifespan context manager | `main.py` | Clean resource setup and teardown |
| Cooperative cancellation | `Trainer._train()` | Cancellation via shared set, checked at epoch boundaries |
| Dynamic batch axis | `export_to_onnx()` | Enables variable batch sizes at inference time |
