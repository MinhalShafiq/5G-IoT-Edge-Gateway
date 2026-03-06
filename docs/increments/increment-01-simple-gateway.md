# Increment 1 -- Simple IoT Gateway

## Goal

A single-node gateway that collects sensor data from IoT devices via MQTT and persists it to PostgreSQL through Redis Streams. This increment establishes the foundational data pipeline: simulated devices publish telemetry over MQTT, a data ingestion service bridges those messages into a Redis Stream, and a persistence worker drains the stream into PostgreSQL in efficient batches. All shared data models, database clients, observability primitives, and deployment manifests are created here to support every subsequent increment.

## Data Flow

```
IoT Simulator --> MQTT Broker (Mosquitto) --> Data Ingestion --> Redis Streams --> Data Persistence --> PostgreSQL
      |                                            ^
      |         (alternative HTTP path)            |
      +-------- POST /api/v1/telemetry ------------+
```

1. The **Simulator** generates synthetic telemetry (temperature, vibration, pressure) and publishes JSON messages to Mosquitto on topics matching `devices/{device_id}/telemetry`.
2. The **MQTT Broker** (Mosquitto) accepts the messages and delivers them to any subscribed client.
3. The **Data Ingestion** service subscribes to `devices/+/telemetry` via `aiomqtt`. For each message it parses the JSON payload, constructs a `TelemetryReading` Pydantic model, serializes it to a flat dict, and appends it to the `raw_telemetry` Redis Stream via `XADD`.
4. **Redis Streams** durably buffer the messages. Three consumer groups are pre-created on the `raw_telemetry` stream: `ml_inference`, `data_persistence`, and `alert_handler`. Each consumer group receives an independent copy of every message.
5. The **Data Persistence** worker reads from the `data_persistence` consumer group via `XREADGROUP`, accumulates records in a `BatchWriter`, and flushes them to PostgreSQL using a bulk `INSERT` when either the batch reaches 100 records or 5 seconds have elapsed.
6. **PostgreSQL** stores the telemetry in the `telemetry_readings` table with indexed columns for `device_id` and `timestamp`, enabling efficient time-range queries.

---

## Files Created

### Project Scaffolding

#### `pyproject.toml`
Root project metadata for the `iot-edge-gateway` monorepo. Declares the project name, version `0.1.0`, description "5G-based IoT Gateway for Edge ML", and requires Python >= 3.11. Uses `setuptools` as the build backend.

#### `Makefile`
Developer convenience commands for the entire project. Targets include:
- `make dev` -- start the local Docker Compose stack.
- `make up` -- build and start all services.
- `make down` -- stop all services.
- `make logs` -- tail logs from all containers.
- `make test` -- run pytest against `tests/`.
- `make test-unit` / `make test-integration` -- scoped test runs.
- `make lint` / `make format` -- run `ruff` for linting and auto-formatting.
- `make clean` -- remove `__pycache__`, `.pytest_cache`, and egg-info directories.
- `make build` -- build all Docker images.
- `make simulate` -- run the IoT simulator locally.
- `make protos` -- compile protobuf definitions via `scripts/generate-protos.sh`.

#### `.gitignore`
Standard ignore rules for Python bytecode, virtual environments, IDE files (`.vscode/`, `.idea/`), `.env` files, SQLite databases, Docker override files, ONNX model files (except test fixtures), logs, coverage reports, and OS artifacts (`.DS_Store`, `Thumbs.db`).

#### `.env.example`
Template for environment variables required by the stack:
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT` -- PostgreSQL connection parameters.
- `REDIS_URL` -- Redis connection string (default `redis://redis:6379/0`).
- `MQTT_BROKER_HOST`, `MQTT_BROKER_PORT` -- Mosquitto coordinates.
- `JWT_SECRET` -- shared secret for JWT token signing.
- `LOG_LEVEL` -- logging verbosity (default `INFO`).
- `ENVIRONMENT` -- deployment environment (default `development`).

---

### Shared Library (`shared/shared/`)

The shared library is a pip-installable Python package that every microservice depends on. It provides Pydantic models, database clients, observability setup, stream constants, and health-check utilities so that each service does not duplicate foundational code.

#### `shared/shared/__init__.py`
Package marker. Empty file that makes `shared` importable as a Python package.

#### `shared/shared/config.py`
Defines `BaseServiceSettings`, the central configuration class that every microservice inherits from. Built on `pydantic_settings.BaseSettings`, it automatically reads values from environment variables (with an optional `.env` file). Fields and their defaults:

| Field | Default | Purpose |
|---|---|---|
| `service_name` | `"unknown"` | Human-readable service identifier for logs and metrics |
| `environment` | `"development"` | Deployment environment |
| `log_level` | `"INFO"` | Structlog filter level |
| `redis_url` | `"redis://redis:6379/0"` | Redis connection string |
| `redis_max_connections` | `20` | Connection pool ceiling |
| `postgres_dsn` | `"postgresql+asyncpg://..."` | Async SQLAlchemy DSN using the asyncpg driver |
| `jwt_secret` | `"dev-secret-change-in-production"` | Token signing key |
| `jwt_algorithm` | `"HS256"` | JWT algorithm |
| `jwt_expiration_minutes` | `60` | Token TTL |
| `prometheus_port` | `9090` | Metrics exposition port |
| `enable_tracing` | `False` | Feature flag for distributed tracing |

The `model_config` dict sets `env_prefix=""`, `env_file=".env"`, and `extra="ignore"` so that unknown environment variables are silently discarded.

#### `shared/shared/models/__init__.py`
Re-exports the three core domain models and their associated enums for convenient single-line imports:
- `Device`, `DeviceCreate`, `DeviceStatus`, `DeviceType` from `models/device.py`
- `TelemetryReading` from `models/telemetry.py`
- `Alert`, `AlertSeverity` from `models/alert.py`

#### `shared/shared/models/device.py`
Device domain models:
- **`DeviceType`** (`StrEnum`) -- enumerates the sensor types supported by the gateway: `temperature_sensor`, `vibration_sensor`, `pressure_sensor`, `smart_meter`, `gps_tracker`.
- **`DeviceStatus`** (`StrEnum`) -- lifecycle states: `registered`, `provisioned`, `active`, `inactive`, `decommissioned`.
- **`DeviceCreate`** (Pydantic `BaseModel`) -- the schema for registering a new device. Fields: `name` (max 255 chars), `device_type`, `firmware_version` (default `"1.0.0"`), `metadata` (arbitrary dict).
- **`Device`** (Pydantic `BaseModel`) -- the full device representation. Adds a UUID `id` (auto-generated), `status` (default `REGISTERED`), `last_seen_at` (nullable datetime), and `created_at`/`updated_at` timestamps. Configured with `from_attributes=True` for ORM compatibility.

#### `shared/shared/models/telemetry.py`
Defines **`TelemetryReading`**, the canonical model for a single sensor reading:
- `device_id` (UUID) -- the source device.
- `device_type` (str) -- sensor category.
- `timestamp` (datetime) -- defaults to `datetime.utcnow()`.
- `payload` (dict) -- sensor-specific data (e.g. `{"temperature_celsius": 22.5}`).
- `metadata` (dict) -- device metadata such as firmware version and signal strength.

Two serialization methods enable round-tripping through Redis Streams:
- **`to_stream_dict()`** -- returns a `dict[str, str]` suitable for `XADD`. The `payload` and `metadata` fields are serialized to JSON strings; `device_id` is stringified; `timestamp` is ISO-formatted.
- **`from_stream_dict(cls, data)`** -- class method that reverses the process, parsing JSON strings back into dicts and reconstructing the model.

#### `shared/shared/models/alert.py`
Defines anomaly alert models:
- **`AlertSeverity`** (`StrEnum`) -- `info`, `warning`, `critical`.
- **`Alert`** (Pydantic `BaseModel`) -- fields: `alert_id` (UUID, auto-generated), `device_id` (UUID), `alert_type` (default `"anomaly_detected"`), `severity` (default `WARNING`), `timestamp`, `anomaly_score` (float 0.0-1.0), `model_version`, `details` (dict).
- **`to_stream_dict()`** -- serializes to a flat `dict[str, str]` for Redis `XADD`, mirroring the pattern from `TelemetryReading`.

#### `shared/shared/database/__init__.py`
Re-exports the database layer for clean imports:
- `get_async_engine`, `get_async_session`, `Base` from `postgres.py`
- `RedisClient` from `redis_client.py`

#### `shared/shared/database/postgres.py`
SQLAlchemy async engine and session management:
- **`Base`** -- declarative base class that all ORM models inherit from.
- **`get_async_engine(dsn, pool_size=10)`** -- creates (or returns) a singleton `AsyncEngine` using `create_async_engine` with the asyncpg driver. Configures `pool_size=10`, `max_overflow=20`, and `pool_pre_ping=True`.
- **`get_session_factory(engine)`** -- creates (or returns) a singleton `async_sessionmaker` that produces `AsyncSession` instances with `expire_on_commit=False`.
- **`get_async_session(dsn)`** -- async generator that yields an `AsyncSession` for use as a FastAPI dependency or async context manager.
- **`init_db(dsn)`** -- calls `Base.metadata.create_all` inside a connection to create all ORM tables. Called once at service startup.
- **`close_db()`** -- disposes the engine connection pool and resets the module-level singletons. Called on shutdown.

#### `shared/shared/database/redis_client.py`
**`RedisClient`** -- an async Redis client wrapper built on `redis.asyncio`. Constructor accepts a URL and `max_connections` to create a `ConnectionPool` with `decode_responses=True`. Key methods:

| Method | Redis Command | Purpose |
|---|---|---|
| `stream_add(stream, data, maxlen, approximate)` | `XADD` | Append an entry to a stream; auto-trims to `maxlen` |
| `ensure_consumer_group(stream, group, start_id)` | `XGROUP CREATE` | Idempotent consumer group creation; ignores `BUSYGROUP` errors |
| `stream_read_group(stream, group, consumer, count, block_ms)` | `XREADGROUP` | Read new messages for a consumer group; blocks up to `block_ms` milliseconds |
| `stream_ack(stream, group, *entry_ids)` | `XACK` | Acknowledge processed messages |
| `stream_pending(stream, group, count)` | `XPENDING` | List unacknowledged messages |
| `stream_len(stream)` | `XLEN` | Get stream length |
| `stream_trim(stream, maxlen)` | `XTRIM` | Manually trim a stream |
| `publish(channel, message)` | `PUBLISH` | Pub/Sub publish |
| `get(key)` / `set(key, value, ex, nx)` / `delete(*keys)` | `GET` / `SET` / `DEL` | Standard key-value operations; `set` supports `EX` (TTL) and `NX` (set-if-not-exists) |
| `ping()` | `PING` | Health check |
| `close()` | -- | Close the connection pool |

#### `shared/shared/observability/__init__.py`
Package marker for the observability module.

#### `shared/shared/observability/logging_config.py`
Structured JSON logging via `structlog`:
- **`setup_logging(service_name, log_level)`** -- configures structlog with processors for context-variable merging, log-level tagging, stack rendering, exception info, ISO timestamps, and JSON rendering. Also configures Python's stdlib `logging` to route through structlog. Binds the `service` context variable to all subsequent log entries.
- **`get_logger(name)`** -- returns a bound structlog logger instance.

#### `shared/shared/observability/metrics.py`
Pre-defined Prometheus metrics shared across all services:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `telemetry_received_total` | Counter | `device_type`, `protocol` | Total readings received |
| `telemetry_processed_total` | Counter | `device_type`, `status` | Total readings processed |
| `stream_messages_published_total` | Counter | `stream` | Messages published to Redis Streams |
| `stream_messages_consumed_total` | Counter | `stream`, `consumer_group` | Messages consumed from Redis Streams |
| `processing_latency_seconds` | Histogram | `service`, `operation` | Per-reading processing time (buckets: 1ms to 1s) |
| `inference_latency_seconds` | Histogram | `model_name` | ML inference time (buckets: 1ms to 0.5s) |
| `anomalies_detected_total` | Counter | `device_type`, `severity` | Anomalies detected by ML |
| `active_devices` | Gauge | `device_type` | Currently active devices |
| `service` | Info | -- | Service metadata (name, version, environment) |

#### `shared/shared/streams/__init__.py`
Package marker for the streams module.

#### `shared/shared/streams/constants.py`
Central constants for Redis Stream and Consumer Group names:
- **`StreamName`**: `RAW_TELEMETRY = "raw_telemetry"`, `ALERTS = "alerts"`, `DEVICE_EVENTS = "device_events"`, `INFERENCE_RESULTS = "inference_results"`.
- **`ConsumerGroup`**: `ML_INFERENCE = "ml_inference"`, `DATA_PERSISTENCE = "data_persistence"`, `ALERT_HANDLER = "alert_handler"`.

Using these constants ensures that all services reference the same stream and group names without risk of typos.

#### `shared/shared/utils/health.py`
**`create_health_router(service_name, version)`** -- factory function that returns a FastAPI `APIRouter` with two endpoints:
- `GET /health` -- returns `{"status": "ok", "service": "<name>", "version": "<version>"}`.
- `GET /ready` -- returns `{"status": "ready"}`.

Every FastAPI-based service includes this router so that Kubernetes liveness and readiness probes have standard endpoints to hit.

---

### MQTT Broker (`services/mqtt-broker/`)

#### `mosquitto.conf`
Mosquitto broker configuration for development:
- **Listener**: TCP on port 1883, MQTT protocol.
- **Authentication**: `allow_anonymous true` (development only; authentication will be enforced in a later increment).
- **ACL**: references `acl.conf` for topic-level access control.
- **Persistence**: enabled, data stored in `/mosquitto/data/`.
- **Logging**: stdout with timestamps in ISO format; logs errors, warnings, notices, and informational messages.
- **Limits**: 1 MB max message size, 10,000 max concurrent connections, 60-second max keepalive.

#### `acl.conf`
Topic-level access control rules:
- **Anonymous / device clients** -- may `write` to `devices/+/telemetry` and `devices/+/status`. The `+` wildcard represents the individual device identifier.
- **`gateway` user** -- may `read` the entire `devices/#` topic tree (used by internal services like data ingestion).
- **`admin` user** -- has full `readwrite` access to all topics (`#`).

#### `Dockerfile`
Based on `eclipse-mosquitto:2` (Alpine, ~12 MB). Copies `mosquitto.conf` and `acl.conf` into the container, creates writable data and log directories owned by the `mosquitto` user (UID 1883), and runs the broker with the custom config.

---

### Data Ingestion (`services/data-ingestion/`)

#### `pyproject.toml`
Package metadata for the `data-ingestion` service. Dependencies: `fastapi`, `uvicorn[standard]`, `aiomqtt`, `redis`, `pydantic-settings`, `structlog`, `prometheus-client`, `httpx`, and the `shared` library. Dev dependencies include `pytest`, `pytest-asyncio`, `pytest-cov`, and `httpx`.

#### `Dockerfile`
Based on `python:3.11-slim`. Installs `gcc` for native extensions, then copies and pip-installs the `shared` library followed by the `data-ingestion` service. Exposes port 8001 and runs `uvicorn data_ingestion.main:app` on `0.0.0.0:8001`.

#### `data_ingestion/config.py`
**`Settings`** extends `BaseServiceSettings` with ingestion-specific fields:
- `service_name = "data-ingestion"`
- `mqtt_broker_host = "mosquitto"`, `mqtt_broker_port = 1883`
- `mqtt_topics = ["devices/+/telemetry"]`
- `coap_port = 5683`
- `stream_max_len = 100_000` (Redis Stream auto-trim ceiling)
- `http_port = 8001`

#### `data_ingestion/main.py`
FastAPI application entry point with a `lifespan` context manager that orchestrates startup and shutdown:

**Startup:**
1. Loads `Settings` from environment variables.
2. Configures structured logging via `setup_logging()`.
3. Publishes service metadata to Prometheus via the `SERVICE_INFO` metric.
4. Creates a `RedisClient` and stores it on `app.state`.
5. Calls `ensure_consumer_group()` three times on the `raw_telemetry` stream to pre-create the `ml_inference`, `data_persistence`, and `alert_handler` consumer groups. This is idempotent -- if a group already exists the `BUSYGROUP` error is silently ignored.
6. Instantiates an `MQTTSubscriber` and launches it as a background `asyncio.Task`.
7. Registers the health router (providing `GET /health` and `GET /ready`) and the ingest router (under `/api/v1`).

**Shutdown:**
1. Signals the MQTT subscriber to stop.
2. Cancels the MQTT background task and awaits its completion.
3. Closes the Redis connection pool.

#### `data_ingestion/routers/ingest.py`
HTTP ingestion router providing an alternative to MQTT:
- **`POST /api/v1/telemetry`** -- accepts a `TelemetryReading` JSON body, passes it through `ingestion_service.process()`, and returns HTTP 202 with the Redis Stream entry ID and device ID.
- **`GET /api/v1/telemetry/stats`** -- returns the current length of the `raw_telemetry` stream (useful for monitoring backpressure).

#### `data_ingestion/services/mqtt_subscriber.py`
**`MQTTSubscriber`** -- a long-lived async task that bridges MQTT to Redis Streams:
- Connects to the MQTT broker using `aiomqtt.Client`.
- Subscribes to all topics listed in `settings.mqtt_topics` (default: `devices/+/telemetry`).
- For each incoming message: decodes the UTF-8 payload, parses JSON, extracts `device_id` from the topic path (`devices/{device_id}/telemetry`), fills in defaults for `device_type`, `timestamp`, and `payload` if missing, constructs a `TelemetryReading`, and calls `ingestion_service.process()`.
- On connection loss, reconnects with exponential backoff: starts at 1 second, doubles on each failure, caps at 60 seconds, and resets to 1 second on successful reconnection.
- Handles `CancelledError` for graceful shutdown and logs all errors and warnings.

#### `data_ingestion/services/ingestion_service.py`
Stateless `process()` function -- the core of the ingestion pipeline:
1. Records the start time with `time.monotonic()`.
2. Increments the `TELEMETRY_RECEIVED` Prometheus counter (labeled by `device_type` and `protocol`).
3. Calls `reading.to_stream_dict()` to serialize the Pydantic model to a flat dict.
4. Publishes to the `raw_telemetry` Redis Stream via `redis_client.stream_add()`, with approximate trimming to `stream_max_len`.
5. Increments the `STREAM_MESSAGES_PUBLISHED` counter.
6. Records the elapsed time in the `PROCESSING_LATENCY` histogram.
7. Returns the auto-generated Redis Stream entry ID.

---

### Data Persistence (`services/data-persistence/`)

#### `pyproject.toml`
Package metadata for the persistence worker. Dependencies: `sqlalchemy[asyncio]`, `asyncpg`, `redis`, `pydantic-settings`, `structlog`, `prometheus-client`.

#### `Dockerfile`
Based on `python:3.11-slim`. Installs `gcc` and `libpq-dev` (required by asyncpg), copies and installs the `shared` library and the `data-persistence` service. Runs `python -m data_persistence.main` -- no HTTP server needed.

#### `data_persistence/config.py`
**`Settings`** extends `BaseServiceSettings` with:
- `service_name = "data-persistence"`
- `batch_size = 100` -- number of readings to accumulate before flushing.
- `flush_interval_seconds = 5.0` -- maximum seconds between flushes even if the batch is not full.
- `consumer_name = "persistence-worker-1"` -- uniquely identifies this consumer within the `data_persistence` group.

A module-level `settings = Settings()` instance is created for convenience.

#### `data_persistence/main.py`
Standalone async entrypoint (no FastAPI):
1. Configures structured logging.
2. Creates a `RedisClient`.
3. Calls `init_db()` to ensure the `telemetry_readings` table exists in PostgreSQL (importing `data_persistence.db.models` registers the ORM model with `Base.metadata`).
4. Ensures the `data_persistence` consumer group exists on the `raw_telemetry` stream.
5. Sets up a `shutdown_event` (`asyncio.Event`) and registers `SIGTERM` / `SIGINT` handlers (with a Windows fallback for `signal.signal`).
6. Runs the `stream_to_postgres` worker until the shutdown event fires.
7. On shutdown: closes the Redis client and disposes the PostgreSQL engine.

#### `data_persistence/db/models.py`
**`TelemetryRecord`** -- SQLAlchemy ORM model mapping to the `telemetry_readings` table:

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | Primary key, default `uuid4()` |
| `device_id` | UUID | Indexed, not null |
| `device_type` | String(50) | Not null |
| `timestamp` | DateTime (TZ) | Indexed, not null |
| `payload` | JSON | Not null |
| `metadata` (column name) / `metadata_` (Python attr) | JSON | Default `{}` |
| `ingested_at` | DateTime (TZ) | Server default `now()` |

The indexes on `device_id` and `timestamp` support efficient queries by device and time range.

#### `data_persistence/workers/stream_to_postgres.py`
**`stream_to_postgres()`** -- the main worker loop:
1. Initializes a `BatchWriter` and an empty list of `pending_ids`.
2. Enters a `while not shutdown_event.is_set()` loop.
3. Calls `redis_client.stream_read_group()` with `block_ms=1000` -- blocks for up to 1 second, then returns to check the shutdown event and flush timer.
4. For each returned entry: deserializes via `TelemetryReading.from_stream_dict()`, converts to a `TelemetryRecord` ORM instance, accumulates it in the `BatchWriter`, appends the stream entry ID to `pending_ids`, and increments the `STREAM_MESSAGES_CONSUMED` counter.
5. **Poison messages**: if deserialization fails, the error is logged and the message is immediately ACKed to prevent infinite redelivery.
6. **Flush decision**: flushes when either `batch_writer.size >= batch_size` OR (`batch_writer.size > 0` AND `flush_interval_seconds` have elapsed since the last flush).
7. **Flush**: opens an `AsyncSession`, calls `batch_writer.flush(session)`, then ACKs all `pending_ids` with a single `XACK` call.
8. **Error handling**: if the PostgreSQL commit fails, `pending_ids` and the buffer are NOT cleared -- they will be retried on the next flush cycle.
9. **Graceful shutdown**: after the loop exits, any remaining buffered records are flushed and ACKed.

#### `data_persistence/services/batch_writer.py`
**`BatchWriter`** -- accumulates telemetry records and flushes them efficiently:
- `accumulate(record)` -- converts the `TelemetryRecord` ORM instance to a dict and appends it to an internal `_buffer` list.
- `flush(session)` -- performs a bulk insert using `sqlalchemy.insert(TelemetryRecord).values(...)` and commits the session. The `metadata_` Python attribute is remapped to the `metadata` column name. Clears the buffer only on success and returns the count of inserted rows.
- `size` property -- returns the current buffer length.

---

### Simulator (`simulator/`)

#### `pyproject.toml`
Package metadata for `iot-simulator`. Dependencies: `paho-mqtt>=2.0.0`, `pydantic>=2.0.0`, `pydantic-settings>=2.0.0`, `structlog>=23.0.0`. Targets Python >= 3.11 with `ruff` configured for line length 100.

#### `Dockerfile`
Based on `python:3.11-slim`. Copies `pyproject.toml` and the `simulator/` package, pip-installs the package, and runs `python -m simulator.main`.

#### `simulator/config.py`
**`SimulatorSettings`** (Pydantic `BaseSettings`):
- `transport = "mqtt"` -- selects the publishing protocol: `"mqtt"`, `"rest"`, or `"coap"`.
- `mqtt_broker_host = "mosquitto"`, `mqtt_broker_port = 1883`.
- `rest_endpoint_host = "localhost"`, `rest_endpoint_port = 8001`.
- `coap_host = "localhost"`, `coap_port = 5683`.
- `num_devices = 10` -- number of simulated devices.
- `publish_interval_seconds = 5.0` -- delay between publish cycles.
- `anomaly_rate = 0.05` -- 5% probability that any given reading is anomalous.
- `device_types = ["temperature_sensor", "vibration_sensor", "pressure_sensor"]`.

#### `simulator/main.py`
CLI entrypoint:
1. Configures structlog with a console renderer for human-readable output.
2. Loads `SimulatorSettings`.
3. Creates a device fleet by cycling through `device_types` and instantiating the appropriate sensor class for each device.
4. Selects a publisher based on `settings.transport` using a `match/case` statement: `MqttPublisher`, `RestPublisher`, or `CoapPublisher`.
5. Constructs a `FleetManager` and runs its async loop.
6. Registers `SIGINT`/`SIGTERM` handlers for graceful shutdown (with a Windows fallback).
7. On exit, disconnects the publisher and closes the event loop.

#### `simulator/devices/base_device.py`
**`BaseDevice`** (abstract base class):
- Constructor: `device_id`, `device_type`, `sampling_interval`.
- **`generate_reading()`** (abstract) -- returns a dict of normal sensor-specific data.
- **`generate_anomaly()`** (abstract) -- returns a dict of anomalous sensor-specific data.
- **`get_telemetry(anomaly=False)`** -- builds a full telemetry envelope: `device_id`, `device_type`, ISO-formatted UTC `timestamp`, the `payload` from either `generate_reading()` or `generate_anomaly()`, and `metadata` containing `firmware_version` and a random `signal_strength` (-90 to -30 dBm).

#### `simulator/devices/temperature_sensor.py`
**`TemperatureSensor`** -- simulates a temperature + humidity sensor:
- **Normal behavior**: temperature follows a sinusoidal daily pattern between 18-28 deg C with additive Gaussian noise (std=0.5). Humidity is centered at 50% with Gaussian noise (std=1.0).
- **Anomaly modes** (randomly selected):
  - **spike**: temperature jumps to 80-120 deg C.
  - **drift**: temperature gradually increases by `drift_rate * elapsed_steps`, capped at +20 deg C above the last normal reading.
  - **stuck**: sensor reports exactly the same value as the previous reading.

#### `simulator/devices/vibration_sensor.py`
**`VibrationSensor`** -- simulates a vibration/acceleration sensor:
- **Normal behavior**: RMS velocity centered at 1.25 mm/s (range 0.5-2.0 mm/s, std=0.1). Peak frequency centered at 125 Hz (range 50-200 Hz, std=5.0).
- **Anomaly modes**:
  - **bearing_failure**: RMS velocity spikes to 10-50 mm/s with normal frequency.
  - **erratic_burst**: RMS is amplified 5-15x via `point_anomaly()`, peak frequency jumps to 800-2000 Hz.

#### `simulator/devices/pressure_sensor.py`
**`PressureSensor`** -- simulates a barometric pressure + flow rate sensor:
- **Normal behavior**: pressure follows Brownian motion around 1013.25 hPa baseline (drift std=0.05, noise std=0.3, clamped to +/-5 hPa). Flow rate is uniform 10-50 L/min with Gaussian noise (std=1.0).
- **Anomaly modes**:
  - **pressure_drop**: sudden -200 hPa drop (e.g. seal failure).
  - **overpressure**: sudden +500 hPa spike (e.g. blockage).

#### `simulator/generators/normal_data.py`
Statistical data generators for realistic sensor readings:
- **`sinusoidal(amplitude, frequency, offset, time)`** -- returns `offset + amplitude * sin(2*pi*frequency*time)`.
- **`gaussian_noise(mean, std)`** -- single sample from N(mean, std^2) using `random.gauss()`.
- **`brownian_motion(current, step_std)`** -- advances a random walk by one Gaussian step.
- **`seasonal_pattern(time, daily_amp, yearly_amp)`** -- combines 24-hour and 8760-hour sinusoidal cycles for environmental modelling.

#### `simulator/generators/anomaly_data.py`
Anomaly injection functions:
- **`point_anomaly(base_value, multiplier_range)`** -- scales the base value by a random multiplier (default 3-10x) with a random sign.
- **`drift_anomaly(base_value, drift_rate, elapsed_steps)`** -- linear drift: `base_value + drift_rate * elapsed_steps`.
- **`stuck_sensor(last_value)`** -- returns `last_value` unchanged.
- **`noise_burst(base_value, noise_multiplier)`** -- adds random uniform noise scaled by `noise_multiplier` (default 20x).

#### `simulator/transport/mqtt_publisher.py`
**`MqttPublisher`** -- uses paho-mqtt v2 API (`CallbackAPIVersion.VERSION2`, MQTTv5):
- `connect(host, port)` -- connects to the broker with keepalive=60 and starts the network loop.
- `publish(device_id, telemetry)` -- serializes the telemetry dict to JSON and publishes to `devices/{device_id}/telemetry` at QoS 1. Logs errors on failed publishes.
- `disconnect()` -- stops the network loop and cleanly disconnects.
- Callbacks: `_on_connect` sets `_connected=True` on successful connection; `_on_disconnect` clears it.

#### `simulator/scenarios/factory_floor.py`
Pre-configured factory-floor scenario function `create_factory_floor_fleet()`:
- 20 `TemperatureSensor` instances (`temp-000` through `temp-019`).
- 15 `VibrationSensor` instances (`vib-000` through `vib-014`).
- 15 `PressureSensor` instances (`pres-000` through `pres-014`).
- All with 5-second publish intervals.
- Returns a list of 50 `BaseDevice` instances.

#### `simulator/fleet_manager.py`
**`FleetManager`** -- orchestrates the full simulated fleet:
- `run()` -- enters an infinite loop: each cycle calls `_publish_cycle()` which uses `asyncio.gather()` to publish all device readings concurrently. The blocking paho-mqtt `publish()` call is offloaded to a thread executor. Sleeps for the remainder of the configured interval.
- `_publish_device(device)` -- rolls a random number against `anomaly_rate` to decide normal vs. anomalous, calls `device.get_telemetry()`, publishes via the executor.
- `_log_statistics()` -- background task that logs total published, total anomalies, anomaly percentage, and device count every 30 seconds.
- `stop()` -- sets `_running=False` to exit the loop after the current cycle.

---

### Deployment

#### `docker-compose.yml`
Docker Compose v3.9 stack with health checks on all infrastructure services:

| Service | Image | Ports | Notes |
|---|---|---|---|
| `redis` | `redis:7-alpine` | 6379 | AOF persistence, 256MB max memory, LRU eviction |
| `postgres` | `postgres:16-alpine` | 5432 | User `iot_gateway`, DB `iot_gateway` |
| `mosquitto` | Built from `services/mqtt-broker/` | 1883 | Custom config, persistent data |
| `data-ingestion` | Built from `services/data-ingestion/Dockerfile` | 8001 | Depends on Redis (healthy) and Mosquitto (healthy) |
| `data-persistence` | Built from `services/data-persistence/Dockerfile` | -- | Depends on Redis (healthy) and PostgreSQL (healthy) |
| `simulator` | Built from `simulator/` | -- | Profile `simulate` (must be explicitly activated) |

The simulator uses the `simulate` profile so it does not start automatically with `docker-compose up`.

#### `deploy/k3s/namespace.yaml`
Creates the `iot-gateway` Kubernetes namespace with the label `app.kubernetes.io/part-of: iot-edge-gateway`.

#### `deploy/k3s/configmaps/common-config.yaml`
A ConfigMap named `common-config` in the `iot-gateway` namespace. Contains shared environment variables: `ENVIRONMENT=production`, `LOG_LEVEL=INFO`, `REDIS_URL=redis://redis:6379/0`, `MQTT_BROKER_HOST=mosquitto`, `MQTT_BROKER_PORT=1883`.

#### `deploy/k3s/secrets/db-credentials.yaml`
A Secret (type `Opaque`) named `db-credentials` with `stringData` entries for `POSTGRES_USER`, `POSTGRES_PASSWORD`, the full `POSTGRES_DSN`, and `JWT_SECRET`.

#### `deploy/k3s/infrastructure/redis/`
- **`deployment.yaml`** -- single-replica Redis 7-alpine Deployment with resource limits (100m-500m CPU, 128Mi-512Mi memory), AOF persistence, readiness/liveness probes using `redis-cli ping`, and a PVC mount at `/data`.
- **`service.yaml`** -- ClusterIP Service exposing port 6379.
- **`pvc.yaml`** -- 1Gi ReadWriteOnce PersistentVolumeClaim for Redis data.

#### `deploy/k3s/infrastructure/postgres/`
- **`statefulset.yaml`** -- single-replica PostgreSQL 16-alpine StatefulSet. Credentials are injected from the `db-credentials` Secret. Resource limits: 200m-1000m CPU, 256Mi-1Gi memory. Readiness/liveness probes use `pg_isready`. A 5Gi volume claim template provides persistent storage at `/var/lib/postgresql/data`.
- **`service.yaml`** -- ClusterIP Service exposing port 5432.

#### `deploy/k3s/infrastructure/mosquitto/`
- **`deployment.yaml`** -- single-replica Deployment using the custom `iot-gateway/mqtt-broker:latest` image. Resource limits: 100m-500m CPU, 64Mi-256Mi memory. TCP socket probes on port 1883 for readiness and liveness. Uses an `emptyDir` volume for ephemeral data.
- **`service.yaml`** -- ClusterIP Service exposing port 1883.

#### `deploy/k3s/services/data-ingestion/`
- **`deployment.yaml`** -- 2-replica Deployment. Environment injected from `common-config` ConfigMap and `db-credentials` Secret. Resource limits: 200m-1000m CPU, 128Mi-512Mi memory. HTTP readiness/liveness probes on `/health` port 8001.
- **`service.yaml`** -- ClusterIP Service exposing port 8001.
- **`hpa.yaml`** -- HorizontalPodAutoscaler (autoscaling/v2): min 2, max 10 replicas. Scales on CPU average utilization > 70% or memory average utilization > 80%.

#### `deploy/k3s/services/data-persistence/`
- **`deployment.yaml`** -- 2-replica Deployment. Environment from ConfigMap and Secret, plus explicit `BATCH_SIZE=100` and `FLUSH_INTERVAL_SECONDS=5.0`. Resource limits: 100m-500m CPU, 128Mi-256Mi memory.
- **`service.yaml`** -- ClusterIP Service exposing port 8002.

#### `deploy/k3s/kustomization.yaml`
Kustomize base that references all resource files in the correct order: namespace, ConfigMap, Secret, infrastructure (Redis PVC + Deployment + Service, PostgreSQL StatefulSet + Service, Mosquitto Deployment + Service), application services (data-ingestion Deployment + Service + HPA, data-persistence Deployment + Service). Applies the common label `app.kubernetes.io/part-of: iot-edge-gateway` to all resources.

#### `deploy/docker/python-base.Dockerfile`
A shared base image for all Python services. Based on `python:3.11-slim`, installs `build-essential`, and upgrades pip/setuptools/wheel. Services can `FROM` this image to skip redundant setup.

---

## How It Works Together

1. **Infrastructure starts**: Docker Compose (or Kubernetes) brings up Redis, PostgreSQL, and Mosquitto. Health checks ensure each service is ready before dependents start.

2. **Data Ingestion initializes**: the FastAPI application starts, connects to Redis, and pre-creates all three consumer groups (`ml_inference`, `data_persistence`, `alert_handler`) on the `raw_telemetry` stream. It then spawns the MQTT subscriber as a background asyncio task.

3. **MQTT Subscriber connects**: using `aiomqtt`, the subscriber connects to Mosquitto and subscribes to `devices/+/telemetry`. It enters an infinite message loop, parsing each incoming JSON payload into a `TelemetryReading` model.

4. **Simulator publishes**: the simulator creates a fleet of devices (e.g. 10 by default, or 50 in the factory-floor scenario), connects a `MqttPublisher` to Mosquitto, and enters a publish loop. Every 5 seconds, each device generates a reading (5% chance anomalous) and publishes it as JSON to `devices/{device_id}/telemetry` at QoS 1.

5. **Ingestion pipeline processes**: for each message (whether from MQTT or the HTTP POST endpoint), `ingestion_service.process()` serializes the reading to a flat dict and appends it to the `raw_telemetry` Redis Stream via `XADD`. Prometheus counters and histograms are updated.

6. **Data Persistence consumes**: the standalone worker reads from the `raw_telemetry` stream as the `data_persistence` consumer group. It deserializes each entry back into a `TelemetryReading`, converts it to a `TelemetryRecord` ORM instance, and accumulates it in the `BatchWriter`. When the batch is full (100 records) or 5 seconds have elapsed, the writer performs a single bulk `INSERT` into PostgreSQL and ACKs all processed stream entries.

7. **Data is now queryable**: telemetry readings are persisted in the `telemetry_readings` table with indexed `device_id` and `timestamp` columns, ready for time-series queries, analytics dashboards, or ML feature extraction in later increments.

8. **Graceful shutdown**: on `SIGTERM` or `SIGINT`, the data ingestion service stops the MQTT subscriber and closes Redis. The data persistence worker flushes any remaining buffered records to PostgreSQL, ACKs them, then closes both Redis and the database connection pool.
