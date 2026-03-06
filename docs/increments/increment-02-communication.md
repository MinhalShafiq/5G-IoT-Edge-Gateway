# Increment 2 -- Communication Protocols

## Goal

Add multi-protocol support to the IoT Edge Gateway: REST and CoAP endpoints for data ingestion, protobuf definitions for structured inter-service communication, gRPC for cloud communication, and multi-format codecs (JSON, CBOR, Protobuf). This increment also introduces the Cloud API service -- the cloud-side counterpart that receives telemetry batches from edge gateways, serves as a model registry, and provides analytics APIs.

## Data Flow

```
IoT Device ----MQTT----> Data Ingestion -----> Redis Streams
IoT Device ----REST----> Data Ingestion -----> Redis Streams
IoT Device ----CoAP----> Data Ingestion -----> Redis Streams

Edge Gateway ---gRPC (batch/stream)--> Cloud API --> PostgreSQL
Cloud API <--- REST (query/models/analytics) --- Dashboard / Client
```

After this increment, devices can publish telemetry using any of three protocols (MQTT, REST, CoAP), and the cloud API can receive telemetry batches over both REST and gRPC from edge gateways.

---

## Files Created

### Protobuf Definitions (`proto/gateway/v1/`)

The `.proto` files define the wire-format messages and gRPC service contracts shared between edge gateways and the cloud. All use `syntax = "proto3"` under the `gateway.v1` package.

#### `device.proto`
Device management messages and service:
- **Enums**: `DeviceType` (UNSPECIFIED, TEMPERATURE_SENSOR, VIBRATION_SENSOR, PRESSURE_SENSOR, SMART_METER, GPS_TRACKER) and `DeviceStatus` (UNSPECIFIED, REGISTERED, PROVISIONED, ACTIVE, INACTIVE, DECOMMISSIONED).
- **Messages**: `Device` (id, name, device_type, status, firmware_version, last_seen_at, metadata map, created_at, updated_at), `RegisterDeviceRequest`/`Response`, `GetDeviceRequest`/`Response`, `ListDevicesRequest`/`Response` (with pagination: page_size, page_token, total_count, and device_type/status filters), `UpdateDeviceStatusRequest`/`Response`.
- **Service**: `DeviceService` with four RPCs: `RegisterDevice`, `GetDevice`, `ListDevices`, `UpdateDeviceStatus`.

#### `telemetry.proto`
Telemetry data messages and service:
- **Messages**: `TelemetryReading` (device_id, device_type, timestamp, payload as `google.protobuf.Struct`, metadata map), `TelemetryBatch` (repeated readings, source_gateway_id, batch_timestamp), `SendTelemetryRequest`/`Response` (accepted_count, rejected_count, errors list), `StreamTelemetryRequest`/`Response` (accepted flag, stream_entry_id).
- **Service**: `TelemetryService` with two RPCs:
  - `SendTelemetry` -- unary RPC that accepts a batch of readings.
  - `StreamTelemetry` -- client-streaming RPC where the edge gateway streams individual readings in real time.

#### `coordination.proto`
Cluster coordination messages (used in Increment 3):
- **Enums**: `NodeStatus` (UNSPECIFIED, JOINING, ALIVE, SUSPECT, DEAD, LEAVING).
- **Messages**: `HeartbeatRequest`/`Response`, `ResourceMetrics` (cpu_usage_percent, memory_usage_percent, memory_available_mb, disk_usage_percent, active_connections, inference_latency_ms), `JoinRequest`/`Response` (with capabilities list and member list), `ClusterNode`, `ConfigUpdate`/`ConfigAck`, `ClusterState`, `Empty`.
- **Service**: `CoordinationService` with five RPCs: `Heartbeat`, `JoinCluster`, `LeaveCluster`, `SyncConfig` (bidirectional streaming), `GetClusterState`.

#### `scheduling.proto`
Resource scheduling messages (for edge-cloud task placement):
- **Enums**: `TaskPriority` (UNSPECIFIED, LOW, MEDIUM, HIGH, CRITICAL), `PlacementTarget` (UNSPECIFIED, EDGE_LOCAL, EDGE_REMOTE, CLOUD).
- **Messages**: `InferenceTask` (task_id, model_name, model_version, input_size_bytes, priority, max_latency_ms, requesting_node_id, created_at), `TaskPlacement` (task_id, target, target_node_id, target_endpoint, estimated_latency_ms, reason), `ResourceReport` (node_id, node_address, cpu/memory/gpu usage, pending_tasks, avg_inference_latency_ms, loaded_models list, reported_at), `SchedulerStatus`.
- **Service**: `SchedulingService` with three RPCs: `SubmitTask`, `ReportResources`, `GetSchedulerStatus`.

---

### Proto Compilation Script (`scripts/generate-protos.sh`)

A Bash script that compiles all `.proto` files into Python gRPC stubs:
1. Resolves the project root and sets `PROTO_DIR` to `proto/` and `OUT_DIR` to `shared/shared/proto_gen/`.
2. Creates the output directory.
3. Iterates over every `.proto` file in `proto/gateway/v1/` and invokes `python -m grpc_tools.protoc` with `--python_out`, `--pyi_out`, and `--grpc_python_out` flags.
4. Creates `__init__.py` files at `proto_gen/`, `proto_gen/gateway/`, and `proto_gen/gateway/v1/` to make the output importable as Python packages.
5. Run via `make protos` or `bash scripts/generate-protos.sh`.

---

### CoAP Server (`services/data-ingestion/data_ingestion/routers/coap_server.py`)

Adds CoAP (Constrained Application Protocol) support to the data ingestion service, targeting resource-constrained IoT devices that cannot use HTTP or MQTT.

**`TelemetryResource`** -- extends `aiocoap.resource.Resource`:
- **`render_post(request)`** -- handles POST requests:
  1. Checks for a non-empty payload.
  2. Inspects the `content_format` option: if `60` (CBOR), decodes with `CborCodec`; otherwise defaults to `JsonCodec`.
  3. Constructs a `TelemetryReading` from the decoded dict.
  4. Calls `ingestion_service.process()` to publish to Redis Streams.
  5. Returns CoAP `2.01 Created` on success or `4.00 Bad Request` on error.
- **`render_put(request)`** -- delegates to `render_post()` for symmetry.

**`create_coap_server(settings, redis_client)`** -- builds the aiocoap resource tree:
- `/.well-known/core` -- standard CoAP discovery endpoint (returns link-format descriptions).
- `/telemetry` -- the `TelemetryResource` instance.
- Binds to `[::]:{settings.coap_port}` (default 5683).
- Returns the aiocoap `Context` for lifecycle management.

---

### Codecs (`services/data-ingestion/data_ingestion/codecs/`)

Multi-format serialization codecs, each exposing a consistent `encode(data) -> bytes` / `decode(raw) -> dict` interface.

#### `__init__.py`
Package marker.

#### `json_codec.py`
**`JsonCodec`** -- `content_type = "application/json"`:
- `encode(data)` -- `json.dumps(data).encode("utf-8")`.
- `decode(raw)` -- `json.loads(raw.decode("utf-8"))`.

#### `cbor_codec.py`
**`CborCodec`** -- `content_type = "application/cbor"`:
- `encode(data)` -- `cbor2.dumps(data)`. CBOR is a binary format that produces smaller payloads than JSON, making it ideal for bandwidth-constrained IoT networks.
- `decode(raw)` -- `cbor2.loads(raw)`.

#### `protobuf_codec.py`
**`ProtobufCodec`** -- `content_type = "application/protobuf"`:
- Currently a **placeholder** that falls back to JSON serialization. The `encode()` and `decode()` methods both use JSON under the hood, with TODO comments indicating that they should be replaced with generated protobuf stubs after running `scripts/generate-protos.sh`.

---

### Validators (`services/data-ingestion/data_ingestion/validators/`)

#### `__init__.py`
Package marker.

#### `telemetry_validator.py`
**`validate_telemetry(reading)`** -- performs semantic validation on a `TelemetryReading` beyond Pydantic's structural checks:
1. **Timestamp freshness**: rejects readings with timestamps more than 5 minutes in the future (clock skew protection) or more than 24 hours in the past (stale data rejection).
2. **Non-empty payload**: ensures the `payload` dict contains at least one key.
3. **Valid UUID**: verifies that `device_id` is a valid UUID string.

Raises `ValidationError` (a custom exception defined in the same module) on any failure.

---

### Simulator Transports

#### `simulator/transport/rest_client.py`
**`RestPublisher`** -- publishes telemetry via HTTP POST using Python's stdlib `urllib`:
- `connect(host, port)` -- configures the target URL to `http://{host}:{port}/api/v1/telemetry`.
- `publish(device_id, telemetry)` -- JSON-encodes the telemetry dict and issues a POST request with `Content-Type: application/json` and a 10-second timeout. Returns `True` for status codes 200, 201, or 202.
- `disconnect()` -- sets `_connected=False` (no persistent connection to close).

Error handling: catches `HTTPError` (server errors) and `URLError` (connection errors) separately, logging each with the device ID.

#### `simulator/transport/coap_client.py`
**`CoapPublisher`** -- publishes telemetry via CoAP POST using `aiocoap`:
- `connect(host, port)` -- configures the target URI to `coap://{host}:{port}/telemetry`.
- `publish(device_id, telemetry)` -- synchronous wrapper around `_async_publish()`. Detects whether an event loop is already running and either schedules a fire-and-forget coroutine or creates a new loop.
- `_async_publish(device_id, telemetry)` -- creates a `aiocoap.Message` with `code=POST`, JSON payload, and `content_format=50` (application/json). Lazily creates a client context on first use and resets it on error.
- `disconnect()` -- shuts down the aiocoap context if active.

The class gracefully degrades if `aiocoap` is not installed, logging a warning and returning `False` from `publish()`.

#### Updated `simulator/config.py`
Added fields to `SimulatorSettings`:
- `transport: str = "mqtt"` -- selects the protocol (`mqtt`, `rest`, or `coap`).
- `rest_endpoint_host`, `rest_endpoint_port` -- REST target coordinates.
- `coap_host`, `coap_port` -- CoAP target coordinates.

#### Updated `simulator/main.py`
Added a `match/case` block that selects the publisher implementation based on `settings.transport`:
- `"mqtt"` -> `MqttPublisher` connected to `mqtt_broker_host:mqtt_broker_port`.
- `"rest"` -> `RestPublisher` connected to `rest_endpoint_host:rest_endpoint_port`.
- `"coap"` -> `CoapPublisher` connected to `coap_host:coap_port`.

Imports for `RestPublisher` and `CoapPublisher` were added alongside the existing `MqttPublisher`.

---

### Cloud API (`services/cloud-api/`)

The Cloud API is a cloud-side service that receives telemetry from edge gateways, manages ML model versions, provides device fleet views, and serves analytics. It exposes both a REST API (FastAPI on port 8080) and a gRPC server (port 50051).

#### `pyproject.toml`
Package metadata for `cloud-api`. Dependencies include `fastapi`, `uvicorn[standard]`, `grpcio`, `grpcio-tools`, `redis`, `sqlalchemy[asyncio]`, `asyncpg`, `pydantic-settings`, `structlog`, `prometheus-client`, and `httpx`. Defines a console script entry point `cloud-api = "cloud_api.main:run"`.

#### `Dockerfile`
Based on `python:3.11-slim`. Sets `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`. Installs `gcc` and `libpq-dev` for asyncpg and grpcio native extensions. Copies and installs the `shared` library, then the `cloud-api` service. Exposes port 8080 and runs `uvicorn cloud_api.main:app --host 0.0.0.0 --port 8080`.

#### `cloud_api/__init__.py`
Package marker.

#### `cloud_api/config.py`
**`Settings`** extends `BaseServiceSettings` with cloud-specific fields:
- `service_name = "cloud-api"`
- `http_port = 8080`
- `grpc_port = 50051`
- `model_store_path = "/models"` -- filesystem path for model artifacts.
- `edge_gateway_urls: list[str] = []` -- list of edge gateway endpoints for proxied queries.

**`get_settings()`** -- factory function returning a cached `Settings` instance (used as a FastAPI dependency).

#### `cloud_api/main.py`
FastAPI application with a `lifespan` context manager:

**Startup:**
1. Loads `Settings` and configures structured logging.
2. Creates a `RedisClient` and stores it on `app.state.redis`.
3. Calls `init_db()` to create PostgreSQL tables (including `telemetry_readings` and `ml_models`).
4. Starts the gRPC server as a background `asyncio.Task`.

**Shutdown:**
1. Stops the gRPC server with a 5-second grace period.
2. Closes the Redis client.
3. Closes the PostgreSQL connection pool.

**`create_app()`** -- factory that builds the FastAPI app and mounts all routers under `/api/v1/`:
- `health.router` (at root)
- `telemetry.router` (at `/api/v1/telemetry`)
- `models.router` (at `/api/v1/models`)
- `devices.router` (at `/api/v1/devices`)
- `analytics.router` (at `/api/v1/analytics`)

**`run()`** -- console script entry point that starts uvicorn with auto-reload in development.

#### `cloud_api/routers/__init__.py`
Package marker.

#### `cloud_api/routers/health.py`
Creates a health router using the shared `create_health_router("cloud-api")` factory. Provides `GET /health` and `GET /ready`.

#### `cloud_api/routers/telemetry.py`
Telemetry ingestion and query endpoints:

- **`POST /api/v1/telemetry/batch`** (202 Accepted) -- receives a `TelemetryBatch` containing a list of `TelemetryReading` objects and a `gateway_id`. Calls `store_batch()` to bulk-insert into PostgreSQL. Returns `{"accepted": N, "message": "batch accepted"}`.
- **`GET /api/v1/telemetry/query`** -- paginated historical query with optional filters: `device_id` (UUID), `device_type` (str), `start_time` / `end_time` (ISO 8601), `limit` (1-1000, default 100), `offset`. Returns `{"total": N, "limit": L, "offset": O, "readings": [...]}`.
- **`GET /api/v1/telemetry/stats`** -- aggregate statistics: total reading count, count by device type, earliest and latest reading timestamps.

Schemas: `TelemetryBatch`, `BatchAcceptedResponse`, `TelemetryQueryResponse`, `TelemetryStatsResponse`.

#### `cloud_api/routers/models.py`
ML model registry CRUD endpoints:

- **`POST /api/v1/models`** (201 Created) -- registers a new model version. Body: `ModelCreate` with `name`, `version`, `framework` (default `"onnx"`), `description`, `file_url`, `metrics` (dict). Returns the full `ModelResponse`.
- **`GET /api/v1/models`** -- lists all active model versions, optionally filtered by `name`.
- **`GET /api/v1/models/latest/{model_name}`** -- returns the most recently created active version for the given model name. Returns 404 if none found.
- **`GET /api/v1/models/{model_id}`** -- fetches a specific model by UUID. Returns 404 if not found.
- **`DELETE /api/v1/models/{model_id}`** -- soft-deletes a model by setting `is_active=False`. Returns `{"id": "...", "deleted": true}`.

Schemas: `ModelCreate`, `ModelResponse`, `ModelDeleteResponse`.

#### `cloud_api/routers/devices.py`
Device fleet endpoints (cloud-aggregated view):

- **`GET /api/v1/devices`** -- returns a paginated list of `DeviceSummary` objects (device_id, name, device_type, status, last_seen_at). Supports `device_type`, `status`, `limit`, and `offset` query parameters. Reads from a `devices` table; gracefully returns an empty list if the table does not exist yet.
- **`GET /api/v1/devices/stats`** -- returns fleet-level statistics: `total_devices`, count `by_type`, count `by_status`.

#### `cloud_api/routers/analytics.py`
Analytics and anomaly summary endpoints:

- **`GET /api/v1/analytics/anomaly-summary`** -- returns anomaly counts for a time window (default: last 24 hours). Broken down `by_device_type` and `by_severity`. Queries the `alerts` table.
- **`GET /api/v1/analytics/device-health`** -- computes a health score (0.0-1.0) for each device based on `1.0 - (anomaly_count / total_readings)` over the last 24 hours.
- **`POST /api/v1/analytics/query`** -- flexible analytics query accepting `metric` (`telemetry_count` or `anomaly_count`), `device_type`, `device_id`, `start_time`, `end_time`, `group_by`, and `limit`. Returns aggregated results from the appropriate table.

Schemas: `AnomalySummaryResponse`, `DeviceHealthEntry`, `DeviceHealthResponse`, `AnalyticsQuery`, `AnalyticsQueryResponse`.

#### `cloud_api/services/__init__.py`
Package marker.

#### `cloud_api/services/telemetry_service.py`
Telemetry business logic backed by the **`TelemetryRow`** ORM model:

**`TelemetryRow`** -- SQLAlchemy ORM model for the `telemetry_readings` table (mirrors the edge persistence table): `id` (UUID PK), `device_id` (UUID, indexed), `device_type` (String, indexed), `timestamp` (DateTime, indexed), `payload` (JSON), `metadata` (JSON).

Service functions:
- **`store_batch(readings, session)`** -- maps a list of `TelemetryReading` Pydantic models to `TelemetryRow` ORM instances, calls `session.add_all()`, commits, and returns the count.
- **`query_telemetry(device_id, start_time, end_time, device_type, limit, offset, session)`** -- builds a dynamic SQLAlchemy query with optional filters, returns `(results_list, total_count)` for pagination.
- **`get_stats(session)`** -- returns aggregate stats: total readings, readings by device type, earliest/latest timestamps.

#### `cloud_api/services/model_registry.py`
ML model registry backed by the **`MLModel`** ORM model:

**`MLModel`** -- SQLAlchemy ORM model for the `ml_models` table: `id` (UUID PK), `name` (String, indexed), `version` (String), `framework` (String, default `"onnx"`), `description` (Text), `file_url` (String), `metrics` (JSON), `is_active` (Boolean, default `True`), `created_at` (server default `now()`).

Service functions:
- **`register_model(model, session)`** -- inserts a new `MLModel` row and returns it as a dict.
- **`list_models(name_filter, session)`** -- queries active models, optionally filtered by name, ordered by `created_at` descending.
- **`get_model(model_id, session)`** -- fetches a single model by UUID.
- **`get_latest_model(model_name, session)`** -- returns the newest active version for a given name.
- **`delete_model(model_id, session)`** -- soft-deletes by setting `is_active=False`.

#### `cloud_api/services/analytics_service.py`
Analytics business logic:
- **`anomaly_summary(start_time, end_time, session)`** -- queries the `alerts` table for total anomaly count, count by device type, and count by severity within the time window. Returns a dict.
- **`device_health_scores(session)`** -- computes per-device health scores over the last 24 hours: joins reading counts from `telemetry_readings` with anomaly counts from `alerts`, calculates `health_score = 1.0 - (anomalies / total_readings)`, and returns a sorted list of device health entries.

Both functions gracefully handle missing tables (common during initial deployment) by catching exceptions and returning empty/zeroed results.

#### `cloud_api/grpc_server/__init__.py`
Package marker.

#### `cloud_api/grpc_server/server.py`
gRPC server lifecycle management:
- **`start_grpc_server(settings, redis_client)`** -- creates an async `grpc.aio.server()`, instantiates the `TelemetryServicer`, adds an insecure port on `[::]:{grpc_port}`, starts the server, and blocks via `wait_for_termination()`. Designed to be wrapped in `asyncio.create_task()`.
- **`stop_grpc_server(grace_period=5.0)`** -- gracefully stops the server with the given grace period.

A module-level `_server` variable allows `stop_grpc_server()` to reach the running server. The proto stub registration is currently a placeholder -- once stubs are generated, `add_TelemetryServiceServicer_to_server()` will wire up the servicer.

#### `cloud_api/grpc_server/telemetry_servicer.py`
**`TelemetryServicer`** -- implements the `TelemetryService` gRPC RPCs:

- **`SendTelemetry(request, context)`** -- unary RPC: iterates over `request.readings`, converts each to a `TelemetryReading` Pydantic model, calls `store_batch()` to persist to PostgreSQL, and returns the accepted count. Currently uses `getattr()` for field access since proto stubs are not yet generated.
- **`StreamTelemetry(request_iterator, context)`** -- client-streaming RPC: iterates over the incoming stream, batches readings, stores each micro-batch to PostgreSQL, and returns the total accepted count when the stream closes.

Both methods include error logging and are designed to be updated with proper proto message types once stubs are compiled.

---

## How It Works Together

1. **Multi-protocol ingestion**: IoT devices can now choose the protocol best suited to their capabilities:
   - **MQTT** (via Mosquitto) -- for devices with persistent TCP connections and publish/subscribe semantics.
   - **REST** (via `POST /api/v1/telemetry`) -- for HTTP-capable devices or integration testing.
   - **CoAP** (via `POST coap://host:5683/telemetry`) -- for constrained devices on lossy networks; supports both JSON and CBOR content formats.

2. **Codec negotiation**: the CoAP server inspects the `content_format` option to select the appropriate codec (JSON or CBOR). The protobuf codec is available as a placeholder for future use once stubs are generated.

3. **Validation**: the `telemetry_validator` provides semantic checks (timestamp freshness, non-empty payload, valid UUID) that go beyond Pydantic's structural validation, protecting downstream consumers from malformed data.

4. **Edge-to-cloud communication**: the Cloud API service runs a gRPC server alongside its REST API. Edge gateways can send telemetry batches via `SendTelemetry` (unary) or stream readings in real time via `StreamTelemetry` (client-streaming). The cloud persists received data to its own PostgreSQL instance.

5. **Model registry**: the cloud API provides a full CRUD interface for ML model versions. Edge gateways poll `GET /api/v1/models/latest/{model_name}` to discover new model versions for automatic deployment.

6. **Analytics**: the cloud API aggregates telemetry and alert data to provide anomaly summaries, device health scores, and flexible query capabilities for dashboards and reporting.

7. **Simulator flexibility**: the simulator can now be configured to use any of the three transport protocols by setting the `TRANSPORT` environment variable, enabling end-to-end testing of each ingestion path.
