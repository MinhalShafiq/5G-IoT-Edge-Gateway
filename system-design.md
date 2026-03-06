# 5G IoT Edge Gateway for Edge ML -- System Design Document

**Version:** 0.1.0
**Last Updated:** 2026-02-27
**Status:** Living Document

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Flow](#3-data-flow)
4. [Communication Protocols](#4-communication-protocols)
5. [Data Storage](#5-data-storage)
6. [ML Pipeline](#6-ml-pipeline)
7. [Cluster Coordination](#7-cluster-coordination)
8. [Resource Scheduling (Edge-Cloud Continuum)](#8-resource-scheduling-edge-cloud-continuum)
9. [Middleware / API Gateway](#9-middleware--api-gateway)
10. [Observability](#10-observability)
11. [Key Design Decisions](#11-key-design-decisions)
12. [Security Considerations](#12-security-considerations)

---

## 1. Project Overview

### 1.1 What the System Does

The 5G IoT Edge Gateway is a distributed, microservices-based platform that sits at the network edge -- physically close to IoT devices -- and provides real-time telemetry ingestion, anomaly detection, device management, and cloud synchronization. It aggregates sensor data from thousands of heterogeneous IoT devices over multiple protocols (MQTT, CoAP, REST), runs Edge ML inference using ONNX Runtime for sub-100ms anomaly detection, and orchestrates a fleet of edge nodes via a custom coordination protocol. The system bridges the gap between constrained IoT devices and cloud-based analytics by processing data locally at the edge while selectively forwarding aggregates, alerts, and batched telemetry to the cloud datacenter.

### 1.2 Target Use Cases

| Use Case | Description |
|---|---|
| **Smart Factory** | Monitors temperature, vibration, and pressure sensors on manufacturing equipment. Detects anomalous vibration patterns that precede bearing failures, enabling predictive maintenance and reducing unplanned downtime. The device simulator includes a `factory_floor` scenario with correlated sensor data. |
| **Fleet Tracking** | Ingests GPS, OBD-II, and environmental data from vehicle-mounted IoT devices over 5G. Edge inference detects route deviations and abnormal fuel consumption in real-time while vehicles are in transit, without relying on intermittent cloud connectivity. |
| **Smart Building** | Collects data from smart meters, HVAC sensors, and occupancy detectors. Edge ML identifies energy waste patterns and triggers automated responses (e.g., HVAC scheduling adjustments) with latencies measured in milliseconds rather than the seconds required for cloud round-trips. |

### 1.3 Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.11 |
| **Web Framework** | FastAPI (async, OpenAPI/Swagger auto-generated) |
| **Inter-service RPC** | gRPC with Protobuf (4 schema files: `telemetry.proto`, `device.proto`, `coordination.proto`, `scheduling.proto`) |
| **Messaging** | MQTT v3.1.1 via Eclipse Mosquitto broker; aiomqtt async client |
| **Constrained Devices** | CoAP over UDP (aiocoap), CBOR binary encoding |
| **Data Bus** | Redis 7 Streams with consumer groups |
| **Database** | PostgreSQL 16 via SQLAlchemy 2.0 async (asyncpg driver) |
| **Edge ML Inference** | ONNX Runtime (CPUExecutionProvider) |
| **ML Training** | PyTorch (Autoencoder, LSTM Predictor); scikit-learn (IsolationForest baseline) |
| **Batch Analytics** | PySpark (local driver mode for edge; cluster mode for cloud) |
| **Containerization** | Docker (multi-stage builds from `python:3.11-slim`) |
| **Orchestration** | K3s (lightweight Kubernetes for edge); Kustomize for manifest management |
| **Observability** | Prometheus, Grafana, Alertmanager, structlog |
| **Configuration** | Pydantic Settings (env vars + `.env` files) |

---

## 2. Architecture Overview

### 2.1 Full System Diagram

```
 +=========================================================================+
 |                         CLOUD / TELCO DATACENTER                        |
 |                                                                         |
 |  +------------------+   +------------------+   +--------------------+   |
 |  |   cloud-api      |   |   ml-training    |   |  batch-analytics   |   |
 |  |  FastAPI :8080   |   |  FastAPI :8006   |   |   FastAPI :8007    |   |
 |  |  gRPC   :50051   |   |  PyTorch         |   |   PySpark          |   |
 |  |  Model Registry  |   |  ONNX Export     |   |   Sensor Agg       |   |
 |  +--------+---------+   +--------+---------+   +--------+-----------+   |
 |           |                       |                      |              |
 |           +----------+------------+----------------------+              |
 |                      |                                                  |
 |              [ PostgreSQL 16 ]                                          |
 |              [ Cloud Redis   ]                                          |
 +==========================|==========================================+===+
                            |
                   5G / WAN Backhaul
                   (gRPC streaming)
                            |
 +==========================|==============================================+
 |                      EDGE CLUSTER (K3s)                                 |
 |                                                                         |
 |  +------------------+        +-------------------+                      |
 |  |   middleware      |<------>|   coordination    |                      |
 |  |  API Gateway      |       |  gRPC   :50052    |                      |
 |  |  FastAPI :8000    |       |  Health :8003     |                      |
 |  |  JWT + Rate Limit |       |  Leader Election  |                      |
 |  +--------+---------+       |  Gossip Protocol  |                      |
 |           |                  |  Config Sync      |                      |
 |           v                  |  OTA Coordinator  |                      |
 |  +--------+---------+       +-------------------+                      |
 |  |  data-ingestion   |               ^                                  |
 |  |  FastAPI :8001    |               |                                  |
 |  |  MQTT sub         |       +-------+----------+                      |
 |  |  CoAP   :5683     |       |    scheduler      |                      |
 |  +--------+---------+       |  FastAPI :8005    |                      |
 |           |                  |  gRPC   :50053    |                      |
 |           v                  |  Adaptive Sched   |                      |
 |    [ Redis 7 Streams ]       +-------------------+                      |
 |    raw_telemetry stream                                                 |
 |           |                                                             |
 |     +-----+------+                                                      |
 |     |            |                                                      |
 |     v            v                                                      |
 |  +--+----------+ +--+-----------+   +------------------+                |
 |  | ml-inference | | data-persist.|   | device-manager   |                |
 |  | FastAPI:8004 | | async worker |   | FastAPI :8002    |                |
 |  | ONNX Runtime | | batch writer |   | CRUD + Provision |                |
 |  +------+------+ +--------------+   +------------------+                |
 |         |                                                               |
 |         v                                                               |
 |  [ alerts stream ]                                                      |
 |         |                                                               |
 |         +---> coordination ---> cloud-api (gRPC)                        |
 |                                                                         |
 |  +------------------+       +-------------------+                       |
 |  |  mqtt-broker      |       |   PostgreSQL 16   |                      |
 |  |  Mosquitto :1883  |       |   (edge replica)  |                      |
 |  +------------------+       +-------------------+                       |
 |                                                                         |
 +=========================================================================+

 +=========================================================================+
 |                         IoT DEVICE LAYER                                |
 |                                                                         |
 |  [Temp Sensor] [Vibration Sensor] [Pressure Sensor] [GPS Tracker] ...   |
 |      |               |                  |                |              |
 |      +-------+-------+------------------+                |              |
 |              |                                            |              |
 |         MQTT :1883                                   CoAP :5683         |
 |     devices/{id}/telemetry                         /telemetry           |
 |                                                                         |
 |  +------------------+                                                   |
 |  |    simulator      |  (MQTT, REST, CoAP publisher)                    |
 |  |  Fleet of virtual |                                                  |
 |  |  sensors          |                                                  |
 |  +------------------+                                                   |
 +=========================================================================+
```

### 2.2 Two-Tier Architecture

**Tier 1: Edge Cluster (K3s)**

The edge cluster runs on lightweight hardware (ARM64 or x86 appliances) deployed at customer premises, factory floors, or 5G base stations. It uses K3s -- a CNCF-certified lightweight Kubernetes distribution -- to orchestrate containers with minimal resource overhead (~512 MB RAM for the control plane). All latency-sensitive services run here: data ingestion, ML inference, device management, coordination, scheduling, and the API gateway. The edge cluster operates autonomously when disconnected from the cloud, queuing telemetry in Redis Streams and draining the backlog when connectivity resumes.

**Tier 2: Cloud / Telco Datacenter**

The cloud tier hosts compute-intensive and storage-heavy workloads: model training (PyTorch on GPU), batch analytics (PySpark), the model registry, and long-term telemetry storage. Communication between edge and cloud uses gRPC streaming over the 5G backhaul, with the `TelemetryService.StreamTelemetry` RPC providing real-time edge-to-cloud data transfer and `SendTelemetry` handling batch uploads.

### 2.3 Service Inventory

| # | Service | Runtime | Ports | Deployment Tier | Description |
|---|---|---|---|---|---|
| 1 | **mqtt-broker** | Eclipse Mosquitto | `1883` (MQTT) | Edge | MQTT v3.1.1 broker for device telemetry ingestion |
| 2 | **data-ingestion** | FastAPI + aiomqtt + aiocoap | `8001` (HTTP), `5683` (CoAP/UDP) | Edge | Multi-protocol telemetry receiver; publishes to `raw_telemetry` Redis Stream |
| 3 | **data-persistence** | Standalone async worker (no HTTP) | -- | Edge | Consumes `raw_telemetry` stream; batch-writes to PostgreSQL (configurable batch size, flush interval) |
| 4 | **device-manager** | FastAPI | `8002` (HTTP) | Edge | Device CRUD, provisioning workflow (pending/approved/rejected), firmware management, device groups |
| 5 | **ml-inference** | FastAPI + ONNX Runtime | `8004` (HTTP) | Edge | Real-time anomaly detection; consumes `raw_telemetry`, publishes to `alerts` stream |
| 6 | **coordination** | gRPC + FastAPI | `50052` (gRPC), `8003` (HTTP health) | Edge | Leader election, SWIM gossip, config sync, OTA firmware rollout |
| 7 | **scheduler** | FastAPI + gRPC | `8005` (HTTP), `50053` (gRPC) | Edge | Edge-cloud task placement; adaptive scheduling with EMA latency feedback |
| 8 | **middleware** | FastAPI | `8000` (HTTP) | Edge | API gateway: JWT/API-key auth, rate limiting, correlation IDs, reverse proxy |
| 9 | **cloud-api** | FastAPI + gRPC | `8080` (HTTP), `50051` (gRPC) | Cloud | Cloud-side API; model registry, telemetry aggregation, analytics queries |
| 10 | **ml-training** | FastAPI + PyTorch | `8006` (HTTP) | Cloud | Model training (Autoencoder, LSTM), ONNX export, registry integration |
| 11 | **batch-analytics** | FastAPI + PySpark | `8007` (HTTP) | Cloud | Batch Spark jobs: sensor aggregation, anomaly reports, device health, trend analysis |
| 12 | **simulator** | MQTT/REST/CoAP publisher | -- | Dev/Test | Generates synthetic telemetry from virtual fleets of temperature, vibration, and pressure sensors |

---

## 3. Data Flow

### 3.1 Telemetry Ingestion Pipeline

```
+-------------+     +-------------+     +-----------------+     +------------------+
|  IoT Device |---->|  MQTT Broker |---->|  Data Ingestion |---->|  Redis Streams   |
| (or Sim)    |     |  :1883      |     |  :8001          |     |  raw_telemetry   |
+------+------+     +-------------+     +-------+---------+     +--------+---------+
       |                                        |                         |
       |  CoAP :5683 (CBOR/JSON) ---------------+                   +----+----+
       |  REST :8001/api/v1/telemetry ----------+                   |         |
       |                                                            v         v
       |                                                    +-------+---+ +---+--------+
       |                                                    | ML        | | Data       |
       |                                                    | Inference | | Persistence|
       |                                                    | (ONNX)   | | (Batch     |
       |                                                    |          | |  Writer)   |
       |                                                    +-----+----+ +---+--------+
       |                                                          |          |
       |                                                          v          v
       |                                                   [ alerts    [ PostgreSQL ]
       |                                                     stream ]
       |                                                          |
       |                                                          v
       |                                                   [ Coordination ]
       |                                                          |
       |                                                          v
       |                                                   [ cloud-api ]
       |                                                     (gRPC)
```

### 3.2 Detailed Pipeline Steps

**Step 1: Device Publishes Telemetry**

An IoT device (or the simulator) publishes a JSON payload to the MQTT topic `devices/{device_id}/telemetry`. The payload contains sensor readings (temperature, vibration amplitude, pressure, etc.) along with device metadata (firmware version, signal strength). Constrained devices may alternatively POST CBOR-encoded data to the CoAP endpoint at UDP port 5683, or use the REST API at `POST /api/v1/telemetry`.

**Step 2: Data Ingestion Receives and Validates**

The `data-ingestion` service runs three concurrent receivers:

- **MQTT Subscriber** (`MQTTSubscriber`): An `aiomqtt.Client` subscribes to `devices/+/telemetry`. On each message it extracts the `device_id` from the topic path, parses the JSON payload, and constructs a `TelemetryReading` Pydantic model. Reconnection uses exponential backoff (1s base, 60s max, 2x multiplier).
- **CoAP Server** (`TelemetryResource`): An `aiocoap` resource tree listens on `::` port 5683. Accepts PUT/POST with content-format negotiation (JSON content-format 50, CBOR content-format 60). CBOR payloads are decoded via the `CborCodec`, then validated identically to MQTT messages.
- **REST Endpoint** (`/api/v1/telemetry`): A FastAPI POST route that accepts `TelemetryReading` JSON directly with full OpenAPI schema validation.

All three paths converge on the stateless `ingestion_service.process()` function, which serializes the reading to a flat dict (`to_stream_dict`) and publishes it via `XADD` to the `raw_telemetry` Redis Stream. The stream is capped at 100,000 entries (approximate trimming) to bound memory usage.

**Step 3: Redis Streams Fan-Out**

The `raw_telemetry` stream has three consumer groups pre-created at ingestion startup:

- `ml_inference` -- consumed by the ML inference service
- `data_persistence` -- consumed by the data persistence worker
- `alert_handler` -- consumed by the coordination service

Each consumer group independently tracks its read offset, enabling parallel processing without message duplication. Redis Streams guarantees at-least-once delivery; acknowledgement (`XACK`) occurs only after successful processing or write.

**Step 4: ML Inference (Anomaly Detection)**

The `AnomalyDetector` in the ml-inference service reads batches of up to 32 messages from the `raw_telemetry` stream via `XREADGROUP`. For each reading:

1. The `FeatureExtractor` extracts numeric values from the payload and computes sliding-window statistics (mean, std, min, max) over the last 10 readings per device.
2. The feature vector is passed to the `InferenceEngine`, which runs `onnxruntime.InferenceSession.run()` against the loaded ONNX model.
3. If the anomaly score exceeds 0.7 (configurable via `ANOMALY_THRESHOLD`), an `Alert` is created and published to the `alerts` Redis Stream. Scores above 0.9 are classified as `CRITICAL`; scores between 0.7 and 0.9 are `WARNING`.
4. The stream entry is acknowledged with `XACK`.

**Step 5: Data Persistence (Batch Write)**

The `data-persistence` worker consumes the same `raw_telemetry` stream under the `data_persistence` consumer group. It uses a `BatchWriter` that accumulates `TelemetryRecord` ORM instances in memory and flushes them to PostgreSQL when either:

- The batch reaches `batch_size` (default: 100 records), or
- `flush_interval_seconds` (default: 5.0s) have elapsed since the last flush.

Flushing uses SQLAlchemy Core `insert().values()` for efficient multi-row inserts. Only after a successful commit and PostgreSQL acknowledgement are the Redis Stream entries acknowledged. On flush failure, the buffer and pending IDs are retained for automatic retry on the next cycle.

### 3.3 Alert Flow

```
ML Inference                     Coordination                Cloud API
     |                                |                          |
     |-- XADD alerts stream --------->|                          |
     |                                |-- reads alert_handler -->|
     |                                |   consumer group         |
     |                                |                          |
     |                                |-- gRPC StreamTelemetry ->|
     |                                |   (batched alerts)       |
     |                                |                          |
     |                                |                     [ PostgreSQL ]
     |                                |                     [ long-term  ]
```

Alerts flow from the `ml-inference` service to the `alerts` Redis Stream. The `coordination` service (as cluster leader) reads from the `alert_handler` consumer group, aggregates alerts, and forwards them to `cloud-api` via the gRPC `TelemetryService.StreamTelemetry` RPC. The cloud-api persists alerts to PostgreSQL for dashboarding and historical analysis.

### 3.4 Model Lifecycle Flow

```
Cloud Training                Model Registry             Edge Inference
     |                              |                          |
     | 1. Train PyTorch model       |                          |
     | 2. Export to ONNX            |                          |
     | 3. POST /api/v1/models ----->|                          |
     |    (register version)        |                          |
     |                              |                          |
     |                              |<-- GET /models/latest ---|
     |                              |    (poll for new version)|
     |                              |                          |
     |                              |--- model metadata ------>|
     |                              |                          |
     |                              |    4. Download .onnx     |
     |                              |    5. Blue-green swap    |
     |                              |    (InferenceEngine      |
     |                              |     .load_model())       |
```

The model lifecycle is a four-phase process:

1. **Train**: The `ml-training` service trains a PyTorch model (Autoencoder or LSTM Predictor) on historical telemetry data.
2. **Export**: The `ModelExporter` converts the trained PyTorch model to ONNX format using `torch.onnx.export()` with dynamic batch axes (opset 13).
3. **Register**: The exported model is registered with the cloud `model_registry` via `POST /api/v1/models`, recording name, version, framework, file URL, and training metrics.
4. **Edge Pull and Hot-Swap**: The edge `ml-inference` service periodically polls `GET /api/v1/models/latest/{name}`. When a new version is detected, it downloads the ONNX artifact and calls `InferenceEngine.load_model()`, which creates a new `ort.InferenceSession` -- effectively a blue-green swap with zero downtime. The previous session is garbage-collected.

### 3.5 Configuration Propagation

```
Cloud / Admin                  Leader Node                Follower Nodes
     |                              |                          |
     |--- config update ----------->|                          |
     |                              |                          |
     |                     1. SET iot-gateway:config:{key}     |
     |                     2. INCR version counter             |
     |                     3. PUBLISH config_updates channel   |
     |                              |                          |
     |                              |--- pub/sub notification->|
     |                              |                          |
     |                              |    4. Follower compares  |
     |                              |       version vector     |
     |                              |    5. Apply if newer     |
```

Configuration propagation follows a leader-write, follower-subscribe model:

1. The leader node writes the config value to Redis (`iot-gateway:config:{key}`) and atomically increments the version counter (`iot-gateway:config:version:{key}`).
2. A JSON notification is published to the `config_updates` Redis Pub/Sub channel containing the key, value, version, and author.
3. Follower nodes subscribe to the channel. On receiving an update, they compare the incoming version against their local cache. Updates with a higher version are applied; stale updates are ignored.
4. On startup, every node runs `sync_from_leader()` to pull the full config from Redis, ensuring convergence even if Pub/Sub messages were missed.

---

## 4. Communication Protocols

### 4.1 MQTT (Device-to-Gateway)

| Property | Value |
|---|---|
| **Broker** | Eclipse Mosquitto (Docker image) |
| **Port** | 1883 (TCP, unencrypted in dev; TLS 8883 in production) |
| **Client Library** | `aiomqtt` (async wrapper around `paho-mqtt`) |
| **Protocol Version** | MQTT v3.1.1 |
| **QoS Levels** | QoS 0 for high-frequency telemetry (fire-and-forget, minimal overhead); QoS 1 for critical alerts and commands (at-least-once delivery with PUBACK) |
| **Topic Structure** | `devices/{device_id}/telemetry` -- wildcard subscription `devices/+/telemetry` |
| **Payload Format** | JSON (`{"device_id": "...", "device_type": "...", "payload": {...}, "metadata": {...}}`) |
| **Reconnection** | Exponential backoff: 1s base, 2x multiplier, 60s max delay |

The MQTT subscriber extracts the `device_id` from the topic path (second segment) when it is not present in the JSON body, providing flexibility for lightweight devices that cannot include device identity in every payload.

### 4.2 CoAP (Constrained Devices)

| Property | Value |
|---|---|
| **Library** | `aiocoap` |
| **Port** | 5683 (UDP) |
| **Resource Path** | `/telemetry` (PUT/POST) |
| **Discovery** | `/.well-known/core` (standard CoAP resource discovery) |
| **Content Formats** | JSON (content-format 50), CBOR (content-format 60) |
| **Encoding** | CBOR for bandwidth-constrained devices (binary, schema-less, ~30% smaller than JSON) |

CoAP is designed for constrained devices operating on low-power networks (NB-IoT, LTE-M) where TCP overhead is prohibitive. The `TelemetryResource` class auto-detects the content format from the CoAP option header and delegates to the appropriate codec (`CborCodec` or `JsonCodec`).

### 4.3 REST (Management APIs)

| Property | Value |
|---|---|
| **Framework** | FastAPI (Starlette-based, async) |
| **Serialization** | JSON with Pydantic v2 model validation |
| **Documentation** | Auto-generated OpenAPI 3.1 spec at `/docs` (Swagger UI) and `/redoc` |
| **Versioning** | URL-path versioning: `/api/v1/...` |
| **Error Format** | `{"detail": "..."}` with appropriate HTTP status codes |

REST APIs are exposed by: data-ingestion (`:8001`), device-manager (`:8002`), coordination health (`:8003`), ml-inference (`:8004`), scheduler (`:8005`), ml-training (`:8006`), batch-analytics (`:8007`), cloud-api (`:8080`), and middleware (`:8000`).

### 4.4 gRPC (Inter-Service)

| Property | Value |
|---|---|
| **Library** | `grpcio` + `grpcio-tools` |
| **Serialization** | Protocol Buffers v3 |
| **Proto Directory** | `proto/gateway/v1/` |

**Protobuf Schemas:**

| Schema File | Service Definition | Key RPCs |
|---|---|---|
| `telemetry.proto` | `TelemetryService` | `SendTelemetry` (batch upload), `StreamTelemetry` (client-streaming) |
| `device.proto` | `DeviceService` | `RegisterDevice`, `GetDevice`, `ListDevices`, `UpdateDeviceStatus` |
| `coordination.proto` | `CoordinationService` | `Heartbeat`, `JoinCluster`, `LeaveCluster`, `SyncConfig` (bidirectional stream), `GetClusterState` |
| `scheduling.proto` | `SchedulingService` | `SubmitTask`, `ReportResources`, `GetSchedulerStatus` |

gRPC is used for all internal inter-service communication where low latency and strong typing are required. The `SyncConfig` RPC uses bidirectional streaming for real-time config distribution. The `StreamTelemetry` RPC uses client-streaming for efficient edge-to-cloud telemetry upload.

### 4.5 Redis Streams (Internal Data Bus)

**Streams:**

| Stream Name | Producer | Purpose |
|---|---|---|
| `raw_telemetry` | data-ingestion | Raw sensor readings from all protocols |
| `alerts` | ml-inference | Anomaly alerts with severity and score |
| `device_events` | device-manager | Device lifecycle events (provisioning, status changes) |
| `inference_results` | ml-inference | Full inference results (including non-anomalous) |

**Consumer Groups:**

| Consumer Group | Stream | Consumer Service | Purpose |
|---|---|---|---|
| `ml_inference` | `raw_telemetry` | ml-inference | Real-time anomaly detection |
| `data_persistence` | `raw_telemetry` | data-persistence | Batch write to PostgreSQL |
| `alert_handler` | `raw_telemetry` | coordination | Alert forwarding to cloud |

Consumer groups enable independent, parallel consumption of the same stream with exactly-once processing semantics (via `XACK` after successful processing). The `XREADGROUP` call blocks for up to 1-2 seconds (`block_ms`) to avoid busy-waiting while still supporting responsive shutdown.

---

## 5. Data Storage

### 5.1 Redis

Redis 7 (Alpine image) serves four distinct roles in the system:

**5.1.1 Streams (Real-Time Pipeline)**

Redis Streams provide the backbone for the telemetry pipeline. Each stream entry is a flat key-value map (strings only) with an auto-generated monotonic ID (millisecond timestamp + sequence). Streams are capped at `maxlen=100,000` entries with approximate trimming (`~`) to bound memory usage while avoiding O(N) deletions on every write.

```
XADD raw_telemetry MAXLEN ~ 100000 *
    device_id   "550e8400-e29b-41d4-a716-446655440000"
    device_type "temperature_sensor"
    timestamp   "2026-02-27T10:30:00Z"
    payload     '{"temperature": 72.5, "humidity": 45.2}'
    metadata    '{"firmware_version": "1.2.0"}'
```

**5.1.2 Pub/Sub (Configuration Sync and Gossip)**

Two Pub/Sub channels are used:

- `config_updates` -- Leader publishes config changes; followers subscribe and apply.
- `iot-gateway:gossip` -- SWIM gossip pings between cluster nodes.

**5.1.3 Leader Election (`SET NX EX`)**

The `LeaderElection` class uses a single Redis key (`iot-gateway:leader`) with atomic `SET NX EX` semantics:

```
SET iot-gateway:leader "node-0" NX EX 15
```

- `NX`: Only set if the key does not exist (atomic acquire).
- `EX 15`: 15-second TTL acts as a lease. If the leader crashes, the key expires and another node can acquire it.
- The leader renews its lease by calling `SET ... EX 15` (without `NX`) on each election loop iteration (every ~5 seconds).

**5.1.4 Key-Value (Caching, Rate Limiting, API Keys)**

| Key Pattern | Purpose | TTL |
|---|---|---|
| `ratelimit:{client_id}:{window}` | Sliding-window request counter | `rate_limit_window_seconds + 1` |
| `apikey:{key}` | API key metadata (name, role, key_id) | Configurable per key |
| `iot-gateway:config:{key}` | Cluster-wide configuration values | None (persistent) |
| `iot-gateway:config:version:{key}` | Config version counter | None (persistent) |
| `iot-gateway:ota:node:{id}:target_version` | OTA firmware rollout target | None (cleared on completion) |

Redis is configured with `maxmemory 256mb` and `allkeys-lru` eviction policy. Append-only file (AOF) persistence is enabled for crash recovery.

### 5.2 PostgreSQL

PostgreSQL 16 (Alpine image) stores all persistent, queryable data. All database access uses SQLAlchemy 2.0 async ORM with the `asyncpg` driver for non-blocking I/O.

**5.2.1 Table Schema**

| Table | Service | Key Columns | Indexes |
|---|---|---|---|
| `telemetry_readings` | data-persistence | `id` (UUID PK), `device_id` (UUID), `device_type` (VARCHAR 50), `timestamp` (TIMESTAMPTZ), `payload` (JSON), `metadata` (JSON), `ingested_at` (TIMESTAMPTZ, auto) | `device_id`, `timestamp` |
| `devices` | device-manager | `id` (UUID PK), `name` (VARCHAR 255), `device_type` (VARCHAR 100), `status` (ENUM: registered/provisioned/active/inactive/decommissioned), `firmware_version` (VARCHAR 50), `last_seen_at` (TIMESTAMPTZ), `metadata` (JSON), `created_at`, `updated_at` | `name`, `device_type`, composite `(device_type, status)` |
| `device_groups` | device-manager | `id` (UUID PK), `name` (VARCHAR 255, UNIQUE), `description` (TEXT), `config` (JSON) | `name` (unique) |
| `firmware_versions` | device-manager | `id` (UUID PK), `version` (VARCHAR 50), `device_type` (VARCHAR 100), `checksum` (VARCHAR 128), `file_url` (TEXT), `release_notes` (TEXT), `created_at` | composite unique `(device_type, version)` |
| `provisioning_requests` | device-manager | `id` (UUID PK), `device_id` (UUID FK -> devices.id CASCADE), `status` (ENUM: pending/approved/rejected), `requested_at`, `approved_at`, `approved_by` (VARCHAR 255) | `device_id` |
| `ml_models` | cloud-api | `id` (UUID PK), `name` (VARCHAR 100), `version` (VARCHAR 50), `framework` (VARCHAR 50), `description` (TEXT), `file_url` (VARCHAR 500), `metrics` (JSON), `is_active` (BOOLEAN), `created_at` | `name` |

**5.2.2 Database Initialization**

Database tables are created via SQLAlchemy `Base.metadata.create_all()` during service startup. The shared `postgres.py` module provides `init_db()` and `close_db()` lifecycle functions, and `get_session_factory()` for scoped async sessions.

---

## 6. ML Pipeline

### 6.1 Edge Inference

**Runtime:** ONNX Runtime with `CPUExecutionProvider` (no GPU dependency on edge hardware).

**Baseline Model:** IsolationForest (scikit-learn) exported to ONNX via `skl2onnx.to_onnx()`.

- Trained on 5,000 synthetic normal samples with 8 features (temperature, humidity, pressure, vibration, power consumption, ambient temperature, noise level, barometric delta).
- 100 estimators, 5% contamination rate.
- Model file: `model_store/anomaly_detector.onnx`.

**Feature Extraction** (`FeatureExtractor`):

The feature extractor maintains a per-device sliding window (default size: 10 readings). For each incoming telemetry reading, it produces a feature vector consisting of:

1. **Current values**: All numeric fields from the payload (e.g., temperature=72.5, humidity=45.2).
2. **Window statistics**: mean, standard deviation, minimum, and maximum computed across all numeric values in the sliding window.

The resulting numpy array (shape `(1, n_features)`) is cast to `float32` and passed to the ONNX session.

**Inference Flow:**

```python
features = feature_extractor.extract(reading)          # (1, N) float32
score = inference_engine.predict(features)              # ONNX Runtime
anomaly_score = normalize_to_01(score)                  # Clamp to [0, 1]
if anomaly_score > 0.7:
    severity = CRITICAL if anomaly_score > 0.9 else WARNING
    publish_alert(alerts_stream, Alert(...))
```

### 6.2 Cloud Training Architectures

**Autoencoder** (`ml_training.architectures.autoencoder`):

```
Architecture:
    Encoder: input_dim(8) -> Linear(16) -> ReLU -> Linear(encoding_dim=4) -> ReLU
    Decoder: encoding_dim(4) -> Linear(16) -> ReLU -> Linear(input_dim=8)

Loss: MSE reconstruction error
Anomaly Score: Per-sample MSE -- high reconstruction error = anomaly
```

The autoencoder learns to compress normal sensor data into a 4-dimensional latent space. At inference time, anomalies produce high reconstruction error because the model has only been trained on normal operational data.

**LSTM Predictor** (`ml_training.architectures.lstm_predictor`):

```
Architecture:
    LSTM: input_dim(8) -> hidden_dim(32), num_layers=2, dropout=0.1
    Output: Linear(32 -> output_dim=8)

Input: Sequence of shape (batch, seq_len, 8)
Output: Next-step prediction of shape (batch, 8)
Anomaly Score: Prediction error (|actual - predicted|)
```

The LSTM predictor learns temporal patterns in sensor time series. Anomalies are detected when the actual next reading deviates significantly from the LSTM's prediction. It also supports multi-step auto-regressive prediction via `predict_sequence()`.

**Training Configuration:**

| Parameter | Default |
|---|---|
| Batch Size | 64 |
| Epochs | 50 |
| Learning Rate | 0.001 |
| Device | CUDA if available, else CPU |
| ONNX Opset | 13 |

### 6.3 Model Lifecycle

```
1. TRAIN         PyTorch model on historical data (cloud GPU)
       |
2. EXPORT        torch.onnx.export() with dynamic batch axes
       |
3. REGISTER      POST /api/v1/models -> ml_models table
       |              name, version, framework, file_url, metrics
       |
4. EDGE PULL     ml-inference polls GET /api/v1/models/latest/{name}
       |
5. HOT-SWAP      InferenceEngine.load_model(new_path)
                  -> new ort.InferenceSession(path, providers=["CPUExecutionProvider"])
                  -> old session garbage-collected (blue-green swap)
```

The `ModelExporter` handles ONNX conversion with:

- `export_params=True` -- embeds trained weights in the ONNX file.
- `do_constant_folding=True` -- optimizes the graph at export time.
- `dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}}` -- enables variable batch sizes at inference.

### 6.4 Anomaly Detection Thresholds

| Score Range | Severity | Action |
|---|---|---|
| 0.0 -- 0.7 | Normal | No alert; entry acknowledged |
| 0.7 -- 0.9 | `WARNING` | Alert published to `alerts` stream; logged |
| 0.9 -- 1.0 | `CRITICAL` | Alert published to `alerts` stream; forwarded to cloud immediately |

Thresholds are configurable via the `ANOMALY_THRESHOLD` environment variable (default: 0.7). The severity boundary at 0.9 is hardcoded in the `AnomalyDetector._process_entry()` method.

---

## 7. Cluster Coordination

The coordination service manages the edge cluster's distributed state: leadership, membership, configuration, and firmware rollouts.

### 7.1 Leader Election

**Algorithm:** Redis-based distributed lock using `SET NX EX`.

**Key:** `iot-gateway:leader`

**TTL:** 15 seconds (configurable via `leader_lock_ttl_seconds`).

**Election Loop:**

```
every 5 seconds:
    result = SET iot-gateway:leader {node_id} NX EX 15
    if result:
        -> we are the new leader (log "leader_elected")
    else:
        current = GET iot-gateway:leader
        if current == our node_id:
            -> renew: SET iot-gateway:leader {node_id} EX 15
            -> still leader
        else:
            -> someone else is leader (log "leader_lost" if we were previously leader)
```

**Graceful Release:** On shutdown, the leader calls `release()` which checks ownership and `DEL`s the key, enabling immediate failover rather than waiting for TTL expiry.

**Guarantees:**

- At most one leader at any time (Redis single-threaded command execution guarantees atomicity).
- Leader failure detection within 15 seconds (TTL expiry).
- Split-brain protection: a node that loses its Redis connection will fail to renew the lock and step down.

### 7.2 SWIM-Like Gossip Protocol

**Protocol:** Inspired by SWIM (Scalable Weakly-consistent Infection-style Process Group Membership Protocol).

**Transport:** Redis Pub/Sub on channel `iot-gateway:gossip` (in production, this would be replaced with direct UDP or gRPC probes).

**Algorithm:**

```
every 2 seconds (gossip_interval_seconds):
    peers = get_alive_peers(exclude=self)
    targets = random.sample(peers, min(fanout=3, len(peers)))
    for each target:
        publish ping to gossip channel:
            {"type": "ping", "from_node_id": self, "target_node_id": peer, "sequence": N}
        if publish fails:
            mark_suspect(peer.node_id)
```

**Listener:** Each node subscribes to the gossip channel and filters for pings addressed to itself. On receiving a ping, it calls `handle_ping()` which updates the sender's `last_heartbeat` timestamp in the cluster state.

**State Transitions:**

```
JOINING ----(heartbeat received)----> ALIVE
ALIVE ------(ping timeout)----------> SUSPECT
SUSPECT ----(heartbeat received)----> ALIVE
SUSPECT ----(sustained timeout)-----> DEAD
ALIVE ------(graceful shutdown)-----> LEAVING
```

### 7.3 Configuration Sync

The `ConfigSync` service provides strongly-consistent configuration distribution:

**Write Path (Leader Only):**

1. `INCR iot-gateway:config:version:{key}` -- atomically bump version.
2. `SET iot-gateway:config:{key} value` -- write the value.
3. `PUBLISH config_updates JSON({key, value, version, updated_by})` -- broadcast notification.
4. Update local cache: `self._local_config[key] = (value, version)`.

**Read Path (Any Node):**

- Real-time: Subscribe to `config_updates` Pub/Sub channel. On each message, compare incoming version against local version. Apply only if incoming version is strictly greater (prevents reordering).
- Startup: Run `sync_from_leader()` to `SCAN` all `iot-gateway:config:*` keys from Redis (skipping `version:*` tracker keys) and hydrate the local cache.

### 7.4 Version Vectors

The `VersionVector` class provides causal ordering for distributed config updates:

- **Increment:** Each node increments its own component on every write.
- **Merge:** On receiving an update, take the component-wise maximum.
- **Dominates:** `A` dominates `B` iff every component of `A` >= `B` and at least one is strictly greater (happened-after relationship).
- **Concurrent:** Neither vector dominates the other -- indicates independent, potentially conflicting updates requiring application-level resolution.

Version vectors are serialized to/from plain dicts for Redis storage and gRPC transport.

### 7.5 OTA Firmware Rollout

The `OTACoordinator` provides two rollout strategies:

**Rolling Update:**

1. For each target node (one at a time):
   - Set `iot-gateway:ota:node:{id}:target_version` in Redis.
   - Wait up to 60 seconds for the node to report `ALIVE` status (polling every 2 seconds).
   - If health check fails: roll back the node (clear the target version) and raise an error, halting the rollout.
   - If healthy: continue to next node.
2. All OTA events are recorded in the Redis list `iot-gateway:ota:events` for audit.

**Canary Update:**

1. Phase 1: Update a single canary node.
2. Phase 2: Observe the canary for `canary_watch_seconds` (default: 120s).
3. Phase 3: Re-check canary health. If degraded, roll back and abort.
4. Phase 4: Roll out to remaining nodes using the rolling update strategy.

---

## 8. Resource Scheduling (Edge-Cloud Continuum)

The scheduler service decides where to execute ML inference tasks: locally on the requesting edge node (`edge_local`), on a remote edge node (`edge_remote`), or in the cloud (`cloud`).

### 8.1 Scheduling Engine

The `SchedulerEngine` maintains a list of `PlacementPolicy` instances. For each submitted `InferenceTask`:

1. Query the `ResourceMonitor` for the current resource map (all non-stale edge nodes).
2. Evaluate every registered policy against the task and resource map.
3. Select the placement with the highest `score` (0.0 -- 1.0).
4. If no policy returns a result, default to `cloud`.

Tasks can be submitted synchronously (request-path) via `submit_task()` or queued for background processing via `enqueue_task()` / `drain_queue()`.

### 8.2 Placement Policies

**LatencyFirst:** Prefer the placement with the lowest estimated latency.

**CostAware:** Factor in compute cost differences between edge and cloud.

**Balanced:** Spread load evenly across available edge nodes.

### 8.3 Adaptive Scheduler

The `AdaptiveScheduler` is the primary placement policy. It maintains a feedback loop:

**EMA Latency Tracking:**

```python
ema_latency = alpha * observed_latency + (1 - alpha) * ema_latency
# alpha = 0.3 (smoothing factor)
```

After each edge inference completes, the observed latency is fed back via `record_latency()`. The EMA provides a noise-resistant estimate of current edge performance.

**Dynamic Threshold Adjustment:**

```python
ratio = ema_latency / edge_latency_sla_ms  # SLA default: 100ms

if ratio < 0.5:    # Latency well within SLA
    cpu_threshold += 1.0    # Relax (max 90%)
elif ratio > 0.9:  # Approaching SLA limit
    cpu_threshold -= 2.0    # Tighten (min 60%)
```

**Placement Decision:**

| Condition | Placement | Score |
|---|---|---|
| Edge available, latency within SLA, requesting node has capacity | `edge_local` | 0.5 -- 1.0 (inversely proportional to SLA ratio) |
| Edge available, latency within SLA, requesting node overloaded | `edge_remote` | 0.5 -- 1.0 |
| No edge capacity or latency above SLA | `cloud` | 0.4 |
| No edge nodes reporting | `cloud` | 0.5 |

### 8.4 Resource Monitor

The `ResourceMonitor` tracks per-node utilization:

```python
@dataclass
class NodeResources:
    node_id: str
    node_address: str
    cpu_usage_percent: float
    memory_usage_percent: float
    gpu_usage_percent: float
    pending_tasks: int
    avg_inference_latency_ms: float
    loaded_models: list[str]
    last_reported: datetime
```

Edge nodes push resource reports to the scheduler via `POST /resources/report` or the gRPC `ReportResources` RPC. Reports older than 30 seconds (`stale_threshold`) are excluded from scheduling decisions. The `get_healthy_nodes()` query returns non-stale nodes sorted by CPU usage (lowest first).

### 8.5 Overload Detection

A node is considered overloaded when:

- `cpu_usage_percent > dynamic_cpu_threshold` (default: 80%, dynamically adjusted 60-90%), OR
- `memory_usage_percent > 90%`

Overloaded nodes are excluded from edge placement; the scheduler shifts load to the cloud until conditions improve.

---

## 9. Middleware / API Gateway

The middleware service is the single entry point for all external API traffic. It runs on port 8000 and applies five middleware layers in strict order.

### 9.1 Middleware Stack (Execution Order on Request)

```
Incoming Request
       |
  ErrorHandlerMiddleware     -- catches unhandled exceptions, returns JSON 500
       |
  CorrelationIdMiddleware    -- reads/generates X-Correlation-ID, binds to structlog
       |
  RequestLoggerMiddleware    -- structured request/response logging
       |
  AuthenticationMiddleware   -- JWT or API key validation
       |
  RateLimiterMiddleware      -- Redis-backed sliding-window rate limiting
       |
  Route Handler (proxy)
```

### 9.2 Authentication

**JWT Bearer Tokens:**

- Algorithm: HS256 (configurable).
- Secret: `jwt_secret` from settings (must be changed in production).
- Expiration: Configurable via `jwt_expiration_minutes` (default: 60 min).
- Refresh tokens: `jwt_refresh_expiration_minutes` (default: 7 days / 10080 min).
- Payload claims: `sub` (subject), `role` (admin/user/device), `iat`, `exp`.
- Validation: `PyJWT` decode with signature verification and expiration check.

**API Key Authentication:**

- Header format: `Authorization: ApiKey {key}`
- Lookup: `GET apikey:{key}` from Redis. Value is a JSON blob containing `name`, `role`, and `key_id`.
- API keys are stored in Redis with optional TTL for expiration.

**mTLS (Device Certificates):**

In production, device certificates are validated at the TLS termination layer (Kubernetes Ingress or Mosquitto broker). The middleware trusts the upstream client identity after TLS handshake.

**Excluded Paths:** `/health`, `/ready`, `/health/dependencies`, `/auth/login`, `/docs`, `/redoc`, `/openapi.json`.

### 9.3 Rate Limiting

**Algorithm:** Sliding-window counter (Redis `INCR` + `EXPIRE`).

```
Key:    ratelimit:{client_id}:{window_number}
Window: time.time() // window_seconds
TTL:    window_seconds + 1
```

**Role-Based Multipliers:**

| Role | Multiplier | Effective Limit (base=100) |
|---|---|---|
| `admin` | 5.0x | 500 requests/window |
| `user` | 1.0x | 100 requests/window |
| `device` | 2.0x | 200 requests/window |

**Response Headers:**

- `X-RateLimit-Limit`: Effective limit for this client.
- `X-RateLimit-Remaining`: Requests remaining in the current window.
- `X-RateLimit-Reset`: Unix timestamp when the current window resets.
- `Retry-After`: Seconds until the window resets (only on 429 responses).

**Failure Mode:** Fail-open. If Redis is unreachable, the request is allowed through and a warning is logged.

### 9.4 Correlation ID Propagation

The `CorrelationIdMiddleware` ensures every request carries a unique trace identifier:

1. If the incoming request has an `X-Correlation-ID` header, use it.
2. Otherwise, generate a new UUID-4.
3. Attach to `request.state.correlation_id` for downstream access.
4. Bind to `structlog.contextvars` so every log entry includes the correlation ID.
5. Add to the response `X-Correlation-ID` header.
6. The reverse proxy forwards the correlation ID to upstream services.

### 9.5 Reverse Proxy Routing

The proxy router forwards requests to internal services based on URL prefix:

| Public Path | Upstream Service | Target Port |
|---|---|---|
| `/api/v1/devices/*` | device-manager | 8002 |
| `/api/v1/telemetry/*` | data-ingestion | 8001 |
| `/api/v1/inference/*` | ml-inference | 8004 |
| `/api/v1/scheduler/*` | scheduler | 8005 |

The proxy:

- Forwards all HTTP methods (GET, POST, PUT, PATCH, DELETE).
- Strips hop-by-hop headers (Connection, Transfer-Encoding, etc.).
- Injects `X-Correlation-ID` and `X-Forwarded-For` headers.
- Uses `httpx.AsyncClient` with connection pooling (200 max connections, 40 keepalive).
- 30-second request timeout, 5-second connect timeout.

---

## 10. Observability

### 10.1 Prometheus Metrics

All services export Prometheus metrics via the `prometheus_client` library. Key metrics:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `telemetry_received_total` | Counter | `device_type`, `protocol` | Total telemetry readings received across all protocols |
| `telemetry_processed_total` | Counter | `device_type`, `status` | Total readings processed (success/failure) |
| `processing_latency_seconds` | Histogram | `service`, `operation` | End-to-end processing latency (buckets: 1ms to 1s) |
| `inference_latency_seconds` | Histogram | `model_name` | ONNX Runtime inference latency (buckets: 1ms to 500ms) |
| `anomalies_detected_total` | Counter | `device_type`, `severity` | Anomalies detected, by type and severity |
| `active_devices` | Gauge | `device_type` | Currently active devices by type |
| `stream_messages_published_total` | Counter | `stream` | Messages published to each Redis Stream |
| `stream_messages_consumed_total` | Counter | `stream`, `consumer_group` | Messages consumed from Redis Streams |
| `service` | Info | -- | Service metadata (name, version, environment) |

### 10.2 Prometheus Scrape Configuration

Prometheus scrapes metrics from all services via Kubernetes pod service discovery (`kubernetes_sd_configs: role: pod`). Services are matched by the `app` pod label and scraped at their respective HTTP ports on the `/metrics` path. Scrape interval: 15 seconds. Evaluation interval: 15 seconds.

Configured scrape jobs: `data-ingestion` (:8001), `data-persistence` (:9090), `ml-inference` (:8004), `device-manager` (:8002), `middleware` (:8000), `cloud-api` (:8080), `redis` (:6379).

### 10.3 Grafana Dashboards

A pre-built **Gateway Overview** dashboard (`deploy/k3s/monitoring/grafana/dashboards/gateway-overview.json`) is provisioned automatically and includes the following panels:

| # | Panel | Visualization | Query Summary |
|---|---|---|---|
| 1 | Telemetry Ingestion Rate | Time series | `rate(telemetry_received_total[5m])` by protocol |
| 2 | Processing Latency (p50/p95/p99) | Heatmap / Time series | `histogram_quantile(0.95, processing_latency_seconds)` |
| 3 | Inference Latency | Time series | `histogram_quantile(0.99, inference_latency_seconds)` |
| 4 | Anomalies Detected | Bar chart | `increase(anomalies_detected_total[1h])` by severity |
| 5 | Active Devices | Stat / Gauge | `active_devices` by device type |
| 6 | Stream Throughput | Time series | `rate(stream_messages_published_total[5m])` vs consumed |
| 7 | Service Health | Status map | Health endpoint status across all services |

### 10.4 Alertmanager

Alertmanager is configured with severity-based routing:

```yaml
route:
  receiver: "default"
  group_by: ["alertname", "service"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - match:
        severity: critical
      receiver: "critical"
      group_wait: 10s       # Faster notification for critical alerts
```

**Inhibition Rules:** A firing `critical` alert inhibits `warning` alerts with the same `alertname` and `service`, preventing alert fatigue during major incidents.

### 10.5 Structured Logging

All services use `structlog` with JSON output. Log entries include:

- `timestamp` (ISO 8601)
- `level` (debug/info/warning/error)
- `service` (service name from settings)
- `correlation_id` (propagated via context vars)
- `event` (structured event name, e.g., `telemetry_published`, `anomaly_detected`)
- Event-specific fields (device_id, latency_ms, error, etc.)

Logging is configured via `setup_logging()` from the shared `observability.logging_config` module, which binds the service name to the structlog context.

---

## 11. Key Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | **Data bus** | Redis Streams (not Kafka) | Edge-friendly: single binary, ~5 MB RAM footprint, built-in consumer groups with at-least-once semantics. Kafka requires ZooKeeper/KRaft and 1+ GB RAM -- prohibitive on edge hardware. Redis already required for caching and leader election, so the Streams capability comes at zero additional operational cost. |
| 2 | **MQTT client** | `aiomqtt` (wraps `paho-mqtt`) | Fully async (`asyncio`-native), production-grade MQTT client. `paho-mqtt` is the most battle-tested Python MQTT library; `aiomqtt` provides a clean async context manager interface on top of it, avoiding thread-to-coroutine bridging complexity. |
| 3 | **Inter-service communication** | gRPC for internal service-to-service; REST for external APIs | gRPC provides strong typing (Protobuf), streaming RPCs (config sync, telemetry upload), and ~10x smaller wire format than JSON. REST (FastAPI) provides human-readable APIs, auto-generated Swagger docs, and broad client compatibility for external consumers. |
| 4 | **Code sharing** | Monorepo with `shared/` library | All 10+ services live in one repository. The `shared/` package contains Pydantic models (`TelemetryReading`, `Device`, `Alert`), database clients (Redis, PostgreSQL), observability setup (metrics, logging), stream constants, auth utilities, and health check routers. This eliminates version skew between services and enables atomic cross-service refactoring. |
| 5 | **ML baseline** | IsolationForest exported to ONNX | IsolationForest is unsupervised (no labeled anomaly data required), trains in seconds on synthetic data, and produces a lightweight ONNX model. It serves as the day-one baseline while the PyTorch Autoencoder and LSTM models are trained on production data. The ONNX export via `skl2onnx` enables a single inference runtime for both sklearn and PyTorch models. |
| 6 | **Kubernetes manifests** | Kustomize (not Helm) | Kustomize is built into `kubectl`, requires no Tiller server, and uses plain YAML with overlays. For an edge deployment with a small number of services, the simplicity of Kustomize outweighs Helm's templating power. The `deploy/k3s/kustomization.yaml` file declaratively lists all resources with a common `iot-edge-gateway` label. |
| 7 | **Coordination deployment** | StatefulSet for stable identity | Coordination nodes require stable network identities (`node-0`, `node-1`, ...) for leader election and gossip protocol peer addressing. Kubernetes `StatefulSet` guarantees ordered, sticky pod names and stable DNS, which Deployments cannot provide. |
| 8 | **CoAP encoding** | CBOR (not Protobuf over CoAP) | CBOR is the native binary encoding for CoAP (RFC 7049), ~30% smaller than JSON, and self-describing (no schema compilation required). Constrained devices on NB-IoT/LTE-M benefit from the reduced payload size and CPU overhead compared to Protobuf serialization. |
| 9 | **Configuration management** | Pydantic Settings + environment variables | All service configuration is defined as Pydantic `BaseSettings` subclasses with typed fields and sensible defaults. Values are loaded from environment variables (12-factor app compliant), with optional `.env` file support for local development. No external config server (Consul, etcd) is required. |
| 10 | **Edge ML runtime** | ONNX Runtime (not TFLite, not raw PyTorch) | ONNX Runtime is framework-agnostic (supports sklearn, PyTorch, TensorFlow exports), optimized for CPU inference on edge hardware, and provides a stable C++ runtime with Python bindings. It avoids locking the training pipeline to a specific framework. |

---

## 12. Security Considerations

### 12.1 Authentication and Authorization

**JWT Tokens:**

- Signed with HS256 (HMAC-SHA256) using a configurable secret (`jwt_secret`).
- Default expiration: 60 minutes (configurable via `jwt_expiration_minutes`).
- Refresh token lifetime: 7 days (configurable via `jwt_refresh_expiration_minutes`).
- Claims include `sub` (subject identifier), `role` (admin/user/device), `iat` (issued at), `exp` (expiration).
- **Production requirement:** Replace the dev secret (`dev-secret-change-in-production`) with a cryptographically random secret. Consider migrating to RS256 (asymmetric) for token verification without sharing the signing key.

**API Keys:**

- Stored in Redis with key pattern `apikey:{key}`.
- Metadata includes `name`, `role`, and `key_id`.
- Optional TTL for automatic expiration.
- Validated on every request by the `AuthenticationMiddleware`.
- **Production requirement:** Generate API keys using `secrets.token_urlsafe(32)` or equivalent. Implement key rotation and revocation workflows.

### 12.2 Rate Limiting

- Sliding-window counter per client per time window.
- Client identity derived from JWT subject (authenticated) or client IP (unauthenticated).
- Role-based multipliers: admins (5x), devices (2x), users (1x).
- 429 responses include `Retry-After` header.
- Fail-open on Redis failure to preserve availability.
- Health and documentation endpoints are excluded from rate limiting.

### 12.3 Correlation IDs for Distributed Tracing

Every request is assigned a unique `X-Correlation-ID` (UUID-4) that propagates through:

1. The middleware gateway (generated or forwarded from client).
2. All log entries via structlog context bindings.
3. Proxied requests to internal services via injected HTTP header.
4. Response headers back to the client.

This enables end-to-end request tracing across all microservices without a dedicated tracing backend (e.g., Jaeger). In production, correlation IDs can be forwarded to OpenTelemetry exporters for integration with distributed tracing systems.

### 12.4 Structured Logging for Audit Trails

All services produce structured JSON logs via `structlog`. Security-relevant events are logged with specific event names:

| Event | Service | Trigger |
|---|---|---|
| `jwt_validation_failed` | middleware | Invalid or expired JWT token |
| `api_key_validation_error` | middleware | Redis lookup failure for API key |
| `rate_limit_exceeded` | middleware | Client exceeded request limit |
| `leader_elected` / `leader_lost` | coordination | Leader election state change |
| `provisioning_requested` | device-manager | New provisioning request created |
| `provisioning_approved` | device-manager | Provisioning request approved |
| `rolling_update_started` / `completed` | coordination | OTA firmware rollout lifecycle |
| `node_rollback_initiated` | coordination | Failed health check during OTA |
| `config_set` | coordination | Configuration value changed |

### 12.5 Device Provisioning Workflow

Devices follow a controlled lifecycle with explicit approval:

```
REGISTERED ---(request_provisioning)---> PENDING
PENDING ------(approve_provisioning)---> PROVISIONED
PENDING ------(reject)-------------------> REJECTED
PROVISIONED --(first telemetry)---------> ACTIVE
ACTIVE -------(timeout)-----------------> INACTIVE
ANY ----------(decommission)------------> DECOMMISSIONED
```

Key security properties:

- A device cannot send telemetry until it reaches `PROVISIONED` status.
- Provisioning requests require explicit approval (`approved_by` field records the authorizer).
- Duplicate pending requests are rejected (one pending request per device).
- All provisioning events are published to the `device_events` Redis Stream for audit.
- Device state transitions are recorded with timestamps in PostgreSQL.

### 12.6 Network Security

| Boundary | Mechanism |
|---|---|
| Device to MQTT broker | TLS 1.3 (port 8883 in production), optional client certificates |
| External clients to middleware | TLS termination at Kubernetes Ingress / load balancer |
| Middleware to internal services | Cluster-internal networking (Kubernetes network policies) |
| Edge to cloud (gRPC) | TLS with mutual authentication (mTLS) over 5G backhaul |
| Redis | Password authentication (`requirepass`), bind to cluster-internal IP only |
| PostgreSQL | MD5/SCRAM-SHA-256 password authentication, SSL mode `require` in production |

### 12.7 Secrets Management

- Database credentials are stored in Kubernetes Secrets (`deploy/k3s/secrets/db-credentials.yaml`).
- JWT signing keys should be injected via Kubernetes Secrets, not hardcoded.
- API keys are runtime-managed in Redis (not stored in code or config files).
- **Production recommendation:** Integrate with an external secrets manager (HashiCorp Vault, AWS Secrets Manager) for rotation and access control.

---

*This document reflects the system as implemented in the `iot-edge-gateway` monorepo (version 0.1.0). It should be updated as the architecture evolves.*
