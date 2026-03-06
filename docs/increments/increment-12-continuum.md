# Increment 12 -- Edge-Cloud Continuum

## Goal

Implement adaptive scheduling so immediate analytics run at the edge while deeper insights come from the cloud. Complete the monitoring stack, testing infrastructure, and operational scripts to bring the system to production readiness.

---

## Components

### Adaptive Scheduler

The scheduler service (built in Increment 6) contains an `AdaptiveScheduler` that implements the edge-cloud continuum. It extends the `PlacementPolicy` abstract base class and uses a feedback loop to dynamically shift workloads between edge and cloud based on real-time conditions.

**Path:** `services/scheduler/scheduler/services/adaptive_scheduler.py`

**Core mechanism -- Exponential Moving Average (EMA):**

The scheduler maintains an EMA of observed edge inference latency, updated every time an edge node reports actual latency via `record_latency()`:

```python
ema_latency = alpha * new_observation + (1 - alpha) * ema_latency
```

With `alpha = 0.3`, the EMA weights recent observations more heavily while smoothing out individual spikes.

**Placement decision logic:**
- If `ema_latency <= SLA` (100ms default) and no edge nodes are overloaded: prefer edge placement.
- If edge nodes are overloaded (`cpu_usage > dynamic_threshold` or `memory_usage > 90%`): shift tasks to cloud.
- If no edge nodes are available or reporting: default to cloud.

**Dynamic threshold adjustment:**
The CPU overload threshold is not static. It adapts based on the latency-to-SLA ratio:

| Latency/SLA Ratio | Action | Threshold Change |
|---|---|---|
| < 0.5 (well within SLA) | Relax threshold | +1.0% (up to 90%) |
| > 0.9 (approaching SLA) | Tighten threshold | -2.0% (down to 60%) |
| 0.5 - 0.9 | No change | Stable |

This creates a self-balancing feedback loop: when the edge is performing well, the scheduler becomes more tolerant of higher CPU usage; when latency rises, it becomes stricter and offloads sooner.

**Scoring:**
The adaptive policy scores edge placements based on the SLA ratio: `score = 1.0 - (ema_latency / sla_ms) * 0.5`. This produces scores in the range [0.5, 1.0], with higher scores when latency is lower. Cloud placements receive a fixed score of 0.4, meaning edge is always preferred when conditions are acceptable.

**Introspection properties:**
- `ema_latency` -- Current EMA value.
- `dynamic_cpu_threshold` -- Current dynamically adjusted CPU threshold.

---

### Placement Decision Flow

The `SchedulerEngine` (`services/scheduler/scheduler/services/scheduler_engine.py`) evaluates all registered placement policies against the current resource map and selects the placement with the highest score.

```
InferenceTask arrives (via POST /api/v1/tasks)
    |
    | Fields: task_id, model_name, model_version, input_size_bytes,
    |         priority, max_latency_ms, requesting_node_id
    v
ResourceMonitor.get_resource_map()
    |
    | Returns dict[node_id -> NodeResources] excluding stale nodes
    | (nodes not reporting within stale_threshold seconds)
    v
Evaluate ALL registered policies in parallel:
    |
    |-- LatencyFirstPolicy
    |   Always prefers edge to minimize hops.
    |   Falls back to cloud only when ALL edge nodes are overloaded (CPU > 85% or memory > 90%).
    |   Scores: 0.9 (local edge), 0.8 (remote edge), 0.3-0.35 (cloud).
    |
    |-- CostAwarePolicy
    |   Prefers edge for small models (< 10MB input), cloud for large models.
    |   Applies a cloud_cost_factor (0.15) penalty to cloud placements.
    |   Scores: 0.85 (local edge, small), 0.75 (remote edge, small), 0.55 (cloud, large).
    |
    |-- BalancedPolicy
    |   Weighted combination: 0.6 * latency_score + 0.4 * cost_score.
    |   Latency score: linear scale from 1.0 (0ms) to 0.0 (max_latency_ms).
    |   Cost score: 0.9 (edge, small model), 0.7 (cloud, large model).
    |
    |-- AdaptiveScheduler
    |   EMA-based feedback loop with dynamic CPU threshold.
    |   Scores: [0.5, 1.0] for edge, 0.4 for cloud.
    v
Pick TaskPlacement with highest score across all policies
    |
    v
Return: {
    task_id, target (edge_local | edge_remote | cloud),
    target_node_id, target_endpoint,
    estimated_latency_ms, score, reason
}
```

**PlacementPolicy abstract base class** (`services/scheduler/scheduler/services/placement_policy.py`):

All policies extend `PlacementPolicy` and implement the `evaluate(task, resources)` method. This returns a `TaskPlacement` with a `score` between 0.0 and 1.0. The engine picks the highest score. If no policy returns a result, the engine defaults to cloud placement.

**ResourceMonitor** (`services/scheduler/scheduler/services/resource_monitor.py`):

Stores the latest resource report per node with a staleness filter (default 30 seconds). Edge nodes push reports via `POST /api/v1/resources/report`. Reports include: `node_id`, `node_address`, `cpu_usage_percent`, `memory_usage_percent`, `gpu_usage_percent`, `pending_tasks`, `avg_inference_latency_ms`, `loaded_models`, `last_reported`.

**Resource reporting endpoint** (`services/scheduler/scheduler/routers/resources.py`):

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/resources` | List all active (non-stale) edge nodes with their resource usage. |
| `GET` | `/api/v1/resources/{node_id}` | Get resource report for a specific node. 404 if stale or unknown. |
| `POST` | `/api/v1/resources/report` | Accept a resource utilization report from an edge node. Returns 202. |

**Task submission endpoint** (`services/scheduler/scheduler/routers/tasks.py`):

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/tasks` | Submit an inference task for placement evaluation. Returns 201 with the placement decision. |
| `GET` | `/api/v1/tasks/{task_id}` | Get the placement result for a previously submitted task. |
| `GET` | `/api/v1/tasks` | List recent task placements with pagination (limit/offset). |

---

### Monitoring Dashboards

**Path:** `deploy/k3s/monitoring/grafana/dashboards/gateway-overview.json`

A Grafana dashboard with 7 panels providing operational visibility across the entire gateway:

| Panel | Type | PromQL Expression | Description |
|---|---|---|---|
| **Telemetry Ingestion Rate** | timeseries | `rate(telemetry_received_total[5m])` | Readings per second, broken down by `device_type` and `protocol`. Shows whether ingestion is keeping up with device fleet growth. |
| **Active Devices** | stat | `sum(active_devices)` | Gauge showing the total number of currently active devices across all edge gateways. |
| **Anomalies Detected** | stat | `sum(increase(anomalies_detected_total[1h]))` | Total anomalies detected in the last hour. A spike here indicates either a real-world issue or a model calibration problem. |
| **Processing Latency p99** | timeseries | `histogram_quantile(0.99, rate(processing_latency_seconds_bucket[5m]))` | 99th percentile processing latency by `service` and `operation`. Helps identify bottlenecks in the pipeline. |
| **ML Inference Latency p99** | timeseries | `histogram_quantile(0.99, rate(inference_latency_seconds_bucket[5m]))` | 99th percentile inference latency by `model_name`. Critical for monitoring the edge SLA target of <100ms. |
| **Redis Stream Lengths** | timeseries | `stream_messages_published_total - stream_messages_consumed_total` | Backlog per stream. A growing backlog indicates consumers cannot keep up with producers. |
| **Stream Throughput** | timeseries | `rate(stream_messages_published_total[5m])` and `rate(stream_messages_consumed_total[5m])` | Published vs consumed message rates per stream and consumer group. Healthy system shows consumed rate matching published rate. |

Dashboard configuration: auto-refresh every 10 seconds, dark theme, default time range of 1 hour.

---

### Alertmanager Rules

**Path:** `deploy/k3s/monitoring/alertmanager/configmap.yaml`

Kubernetes ConfigMap containing the Alertmanager configuration:

**Route hierarchy:**
- **Default route:** Groups alerts by `alertname` and `service`. Group wait: 30 seconds. Group interval: 5 minutes. Repeat interval: 4 hours.
- **Critical route:** Matches `severity: critical`. Overrides group wait to **10 seconds** for faster notification of critical issues.

**Receivers:**
- `"default"` -- Placeholder for webhook, email, or Slack integration.
- `"critical"` -- Placeholder for high-priority alerting channel (e.g., PagerDuty, SMS).

**Inhibition rules:**
- When a `critical` alert is active for a given `alertname` and `service`, any corresponding `warning` alert is suppressed. This prevents operators from being flooded with both critical and warning notifications for the same issue.

---

### E2E Tests

**Path:** `tests/e2e/test_full_pipeline.py`

End-to-end tests that require all services running (via `docker-compose up`). Marked with `pytest.mark.e2e`.

**Test cases:**

- **`test_telemetry_ingestion_via_rest`** -- Posts a telemetry reading to `POST /api/v1/telemetry` on the data-ingestion service and verifies a 202 response.
- **`test_telemetry_stats_endpoint`** -- Calls `GET /api/v1/telemetry/stats` and verifies that `stream_length` is present in the response.
- **`test_health_checks`** -- Iterates over service health endpoints and verifies each returns `{"status": "ok"}`. Gracefully skips services that are not running.
- **`test_batch_telemetry_to_cloud`** -- Sends a batch of 10 readings to the cloud-api's `POST /api/v1/telemetry/batch` endpoint and verifies `accepted_count == 10`.

Uses `httpx.AsyncClient` with 10-second timeouts. Skips tests when services are unreachable via `pytest.skip()`.

---

### Load Tests

**Path:** `tests/load/locustfile.py`

Locust load test simulating thousands of IoT devices.

**Usage:**
```bash
locust -f tests/load/locustfile.py --host http://localhost:8001
```

**`IoTDeviceUser` class:**
- Each simulated user represents a single IoT device with a unique `device_id` (UUID) and randomly assigned `device_type` (temperature_sensor, vibration_sensor, or pressure_sensor).
- Wait time between requests: 1-5 seconds (simulating real sensor polling intervals).

**Tasks:**

| Task | Weight | Description |
|---|---|---|
| `send_telemetry` | 10 | Sends a normal telemetry reading with realistic payloads per device type. Temperature sensors emit `{temperature, humidity}`, vibration sensors emit `{rms_velocity, peak_frequency}`, pressure sensors emit `{pressure_hpa, flow_rate}`. |
| `send_anomalous_telemetry` | 1 | Sends an anomalous reading with extreme values (e.g., temperature 80-120, pressure 500-800). Represents ~10% of traffic. |
| `check_stats` | 1 | Queries the telemetry stats endpoint. |
| `health_check` | 1 | Hits the health endpoint. |

Payload generation uses `random.gauss()` for realistic normal distributions and `random.uniform()` for anomalous ranges.

---

### Integration Tests

**Path:** `tests/integration/test_telemetry_pipeline.py`

Tests the data flow from HTTP ingestion to Redis Stream to consumer group:

- **`test_post_telemetry_returns_202`** -- Posts telemetry via the FastAPI ASGI transport (in-process, no network) and verifies 202 with a `stream_entry_id` in the response.
- **`test_telemetry_appears_in_redis_stream`** -- Publishes a `TelemetryReading` directly to the `raw_telemetry` stream and verifies the stream length increases.
- **`test_consumer_group_reads_telemetry`** -- Creates a consumer group, adds a reading, reads via `XREADGROUP`, verifies the data matches, and confirms `XACK` returns 1.

**Path:** `tests/integration/test_anomaly_detection_flow.py`

Tests the alert pipeline:

- **`test_alert_published_on_anomaly`** -- Simulates the ML inference service publishing an alert (score 0.95, CRITICAL) to the `alerts` stream. Reads it back via a consumer group and verifies severity and score.
- **`test_normal_reading_no_alert`** -- Publishes a normal reading to `raw_telemetry` and verifies that the `alerts` stream length does not increase.

---

### Test Infrastructure

**Path:** `tests/conftest.py`

Session-level pytest fixtures for integration and E2E tests:

- Overrides environment variables for the test environment: `REDIS_URL` set to DB 1 (isolated from production), `POSTGRES_DSN` pointing to `iot_gateway_test` database, `LOG_LEVEL` set to WARNING to reduce noise.
- **`event_loop`** (session scope) -- Creates a single event loop shared across all async tests.
- **`redis_client`** -- Provides a `RedisClient` connected to the test Redis DB. Calls `flushdb()` after each test for clean state.
- **`db_session`** -- Creates all database tables before tests, provides an `AsyncSession`, rolls back after each test, and drops all tables after the test suite completes.

**Path:** `docker-compose.test.yml`

Lightweight test infrastructure stack:

| Service | Image | Port | Notes |
|---|---|---|---|
| `redis-test` | `redis:7-alpine` | 6380 -> 6379 | No persistence (appendonly no), 64MB max memory |
| `postgres-test` | `postgres:16-alpine` | 5433 -> 5432 | DB: `iot_gateway_test`, **RAM-backed via tmpfs** for maximum speed |
| `mosquitto-test` | `eclipse-mosquitto:2` | 1884 -> 1883 | Verbose mode, no config file |

The tmpfs mount for PostgreSQL (`/var/lib/postgresql/data`) eliminates disk I/O, making tests significantly faster. Non-standard ports (6380, 5433, 1884) prevent conflicts with local development services.

---

### Dev/Ops Scripts

**Path:** `scripts/setup-dev.sh`

Development environment setup:
1. Checks prerequisites: `python3`, `docker`, `docker-compose`.
2. Creates a Python virtual environment at `.venv`.
3. Installs the shared library in editable mode.
4. Iterates over all service directories and installs each in editable mode (skips services with missing system dependencies).
5. Installs the simulator package.
6. Installs dev tools: pytest, pytest-asyncio, pytest-cov, httpx, ruff, locust.
7. Copies `.env.example` to `.env` if not already present.

---

**Path:** `scripts/run-local.sh`

Local development launcher:
1. Starts infrastructure containers: Redis, PostgreSQL, Mosquitto.
2. Waits 5 seconds for infrastructure to initialize.
3. Starts edge services: data-ingestion and data-persistence.
4. Prints the service URLs and usage instructions.

---

**Path:** `scripts/build-images.sh`

Docker image builder for all 12 services:
1. Accepts `REGISTRY` (default: `iot-gateway`) and `TAG` (default: `latest`) environment variables.
2. Builds images for: mqtt-broker, data-ingestion, data-persistence, device-manager, ml-inference, coordination, scheduler, middleware, cloud-api, ml-training, batch-analytics, simulator.
3. Edge services use `-f services/{name}/Dockerfile .` with the project root as build context (required because they COPY the `shared/` directory).
4. Optionally pushes all images to the registry if `--push` is passed.

---

**Path:** `scripts/deploy-edge.sh`

K3s edge cluster deployment:
1. Checks for `kubectl` and verifies K3s cluster connectivity.
2. Builds all Docker images via `build-images.sh`.
3. Applies Kustomize manifests: `kubectl apply -k deploy/k3s/`.
4. Waits for rollout completion of redis, mosquitto, and data-ingestion deployments with a 120-second timeout.
5. Lists pods in the `iot-gateway` namespace.

---

**Path:** `scripts/train-baseline-model.sh`

Baseline model training:
1. Creates the model output directory at `services/ml-inference/ml_inference/model_store/`.
2. Runs `python -m ml_inference.models.isolation_forest` with 10,000 training samples.
3. Prints the model path and file size.

---

**Path:** `scripts/generate-protos.sh`

Protocol Buffers compilation:
1. Locates proto files at `proto/gateway/v1/*.proto`.
2. Runs `python -m grpc_tools.protoc` to generate:
   - Python message classes (`_pb2.py`)
   - Type stubs (`_pb2.pyi`)
   - gRPC service stubs (`_pb2_grpc.py`)
3. Creates `__init__.py` files in the output directory hierarchy at `shared/shared/proto_gen/`.

---

## The Edge-Cloud Continuum in Practice

The system adapts dynamically to changing conditions through the interplay of the adaptive scheduler, resource monitor, and feedback loop:

### 1. Under Low Load -- All Inference at Edge

When the device fleet is small and edge hardware is underutilized:
- Edge inference latency is well below the 100ms SLA (typically 5-15ms).
- The EMA stays low, keeping the SLA ratio below 0.5.
- The dynamic CPU threshold relaxes upward (toward 90%).
- The AdaptiveScheduler scores edge placements highly (0.85-1.0).
- All inference tasks are placed on edge_local or edge_remote nodes.
- Result: sub-10ms latency for anomaly detection.

### 2. Device Fleet Scales Up -- Rising Latency

As more devices connect and send telemetry:
- Inference volume increases.
- Edge CPU utilization rises.
- Per-request latency starts to increase.
- The EMA gradually reflects the rising trend (smoothed by alpha=0.3).
- The SLA ratio moves from below 0.5 toward 0.9.

### 3. Edge Overload -- Offload to Cloud

When edge capacity is exceeded:
- The SLA ratio exceeds 0.9, triggering threshold tightening (-2% per observation).
- The dynamic CPU threshold drops (from 80% toward 60%).
- Edge nodes with high CPU usage are excluded from consideration.
- The AdaptiveScheduler shifts new tasks to the cloud endpoint.
- Cloud processes the heavier or overflow workloads.
- Result: edge latency stabilizes as load is redistributed.

### 4. Cloud Handles Heavy Analytics

While the cloud handles overflow inference:
- The batch-analytics service (Increment 10) runs PySpark jobs for historical analysis.
- The ml-training service (Increment 9) trains new model versions on accumulated data.
- Cloud-api acts as the hub: receiving batch telemetry, serving the model registry, and routing API requests.

### 5. Edge Recovers -- Tasks Return to Edge

As edge load decreases (devices go offline, batch processing completes):
- Edge CPU utilization drops.
- Inference latency decreases.
- The EMA gradually reflects the improvement.
- The SLA ratio drops below 0.5.
- The dynamic CPU threshold relaxes upward (+1% per observation, toward 90%).
- The AdaptiveScheduler starts routing tasks back to edge.
- Result: the system self-heals without operator intervention.

### 6. Steady State -- Optimal Placement

In the steady state, the system reaches an equilibrium:
- Real-time anomaly detection runs at the edge with sub-100ms latency.
- Heavy batch analytics and model training run in the cloud.
- The adaptive scheduler continuously fine-tunes the edge/cloud split.
- New models trained in the cloud are pushed to the model registry.
- Edge ml-inference services poll the registry, download new models, and hot-swap them with zero downtime.
- Result: optimal use of both edge (low latency, no network dependency) and cloud (elastic compute, GPU access) resources.

---

## Design Patterns Summary

| Pattern | Where Used | Purpose |
|---|---|---|
| Strategy pattern | `PlacementPolicy` + concrete policies | Interchangeable placement algorithms with a common interface |
| EMA feedback loop | `AdaptiveScheduler` | Smooth latency tracking with dynamic threshold adjustment |
| Observer pattern | `ResourceMonitor` + `POST /resources/report` | Edge nodes push updates, scheduler reacts to state changes |
| Staleness filter | `ResourceMonitor.get_resource_map()` | Excludes non-responsive nodes from scheduling decisions |
| Competitive evaluation | `SchedulerEngine.submit_task()` | All policies compete; highest score wins |
| Blue-green model deployment | `model_manager.hot_swap()` | Zero-downtime model updates with validation |
| Consumer group | `AnomalyDetector` | Reliable at-most-once stream processing |
| Prometheus instrumentation | Grafana dashboard | Operational visibility across the full pipeline |
| Inhibition rules | Alertmanager config | Prevents alert fatigue by suppressing redundant warnings |
| RAM-backed test DB | `docker-compose.test.yml` | Fast integration tests via tmpfs PostgreSQL |
| Load simulation | `locustfile.py` | Realistic IoT device behavior for performance testing |
