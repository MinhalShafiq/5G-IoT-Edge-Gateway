# Increment 6 -- Resource Scheduling

## Goal

Distribute ML inference tasks between edge gateways and the cloud based on real-time resource availability, latency requirements, and cost. The scheduler evaluates multiple placement policies, selects the optimal target (edge local, edge remote, or cloud), and adapts its behaviour over time through a latency feedback loop.

---

## Files Created

### Scheduler Service (`services/scheduler/`)

This service runs a dual-server architecture: a FastAPI HTTP API on port 8005 for task submission and resource reporting, and a gRPC server on port 50053 for high-performance inter-service communication. A background loop continuously drains queued tasks at a configurable interval.

The service contains **18 files** organised into routers, a core engine, resource monitoring, and pluggable placement policies.

---

### 1. `pyproject.toml`

**Purpose:** Python package definition and dependency manifest.

**Dependencies:**
- `fastapi>=0.104.0` -- HTTP API framework
- `uvicorn[standard]>=0.24.0` -- ASGI server for the HTTP side
- `grpcio>=1.60.0` -- gRPC server and client runtime
- `redis>=5.0.0` -- Redis client (used by the shared library)
- `pydantic-settings>=2.1.0` -- environment-based configuration
- `structlog>=23.2.0` -- structured logging
- `prometheus-client>=0.19.0` -- metrics instrumentation

**Note:** Unlike most services, the scheduler does not declare `shared` as a direct dependency in `pyproject.toml`. Instead, it relies on the shared library being pre-installed in the Docker image (via `pip install /app/shared`), and documents this in a `[tool.scheduler.dependencies-path]` hint.

A `[project.scripts]` entry registers `scheduler = "scheduler.main:run"` so the service can be started via the `scheduler` console command.

---

### 2. `Dockerfile`

**Purpose:** Container image definition.

**Build stages:**
1. Base image: `python:3.11-slim`
2. Sets `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1` for cleaner container logging
3. Installs `gcc` (required by `grpcio` for native compilation)
4. Copies and installs the `shared/` library
5. Copies and installs the `services/scheduler/` package
6. Sets the working directory to `/app/services/scheduler`
7. Exposes both port `8005` (HTTP) and `50053` (gRPC)
8. Entrypoint: `python -m scheduler.main` (uses the module's `run()` function)

---

### 3. `scheduler/config.py`

**Purpose:** Service-specific configuration extending `BaseServiceSettings`.

**Fields:**
- `service_name: str = "scheduler"`
- `http_port: int = 8005` -- FastAPI HTTP API
- `grpc_port: int = 50053` -- gRPC server for inter-service calls
- `edge_latency_sla_ms: float = 100.0` -- the target maximum edge inference latency in milliseconds; policies use this as the threshold for deciding between edge and cloud
- `cloud_endpoint: str = "http://cloud-api:8080"` -- base URL for the cloud API gateway used when tasks are placed in the cloud
- `scheduling_interval_seconds: float = 1.0` -- how often the background drain loop processes queued tasks
- `resource_stale_threshold_seconds: float = 30.0` -- edge nodes that have not reported within this window are considered stale and excluded from placement decisions

The `get_settings()` factory function returns a new `Settings` instance (for use outside of the FastAPI dependency chain).

---

### 4. `scheduler/main.py`

**Purpose:** Application entry point running a dual FastAPI + gRPC server.

**Architecture:**

```
                        +------------------+
                        |    main.py       |
                        |                  |
                        |  FastAPI (8005)  |--- HTTP routes (tasks, resources, health)
                        |                  |
                        |  gRPC  (50053)   |--- future inter-service scheduling calls
                        |                  |
                        |  Scheduling Loop |--- background asyncio.Task draining queue
                        +------------------+
```

**Lifespan handler:**

*Startup:*
1. Configures structured logging
2. Initialises a `RedisClient` and stores it on `app.state.redis`
3. Creates a `ResourceMonitor` with the configured stale threshold
4. Assembles three placement policies: `LatencyFirstPolicy`, `CostAwarePolicy`, `BalancedPolicy` -- each parameterised from settings
5. Creates an `AdaptiveScheduler` (feedback-based policy)
6. Builds the `SchedulerEngine` with the resource monitor and the three policies
7. Launches the gRPC server as a background `asyncio.Task` via `start_grpc_server()` -- this runs `grpc.aio.server` with a 4-worker thread pool, binds to `[::]:50053`, and waits for termination
8. Launches the scheduling drain loop as a background `asyncio.Task` -- this calls `engine.drain_queue()` every `scheduling_interval_seconds` (default 1.0s)

*Shutdown:*
1. Cancels the scheduling loop task
2. Gracefully stops the gRPC server with a 5-second grace period
3. Closes the Redis client

**`create_app()` factory:**
Creates the FastAPI instance and mounts routers with `/api/v1` prefix for tasks and resources, plus a root-level health router.

**`run()` function:**
Console entry point that starts uvicorn on the configured HTTP port, with auto-reload enabled in development mode.

---

### 5. `scheduler/routers/tasks.py`

**Purpose:** HTTP endpoints for submitting and querying inference task placements.

**Schemas:**
- `TaskSubmitResponse` -- wraps `task_id` and the full `TaskPlacement` result
- `TaskListResponse` -- paginated list of `TaskPlacement` objects with total count

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/tasks` | Submit an `InferenceTask` for immediate placement evaluation. The engine runs all policies, returns the highest-scoring `TaskPlacement`. Returns 201. |
| GET | `/api/v1/tasks/{task_id}` | Retrieve the placement result for a previously submitted task. Returns 404 if the task_id is unknown. |
| GET | `/api/v1/tasks` | List all recent task placements with pagination (limit/offset). Results are sorted by task_id descending (newest first). |

The router retrieves the `scheduler_engine` from `request.app.state` and delegates all logic to it. Structured logging records the submission event, model name, priority, and the eventual placement target and score.

---

### 6. `scheduler/routers/resources.py`

**Purpose:** HTTP endpoints for edge node resource reporting and querying.

**Schemas:**
- `ResourceListResponse` -- total count and list of `NodeResources`
- `ResourceReportAccepted` -- acknowledgement with node_id and message

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/resources` | List all non-stale edge nodes and their resource utilisation |
| GET | `/api/v1/resources/{node_id}` | Get the resource report for a specific node; 404 if not found or stale |
| POST | `/api/v1/resources/report` | Receive a `NodeResources` report from an edge node; returns 202 Accepted |

Edge nodes call `POST /resources/report` periodically (every 5-10 seconds) to keep the scheduler informed of their CPU, memory, GPU utilisation, pending task count, average inference latency, and loaded model list.

---

### 7. `scheduler/routers/health.py`

**Purpose:** Health check router.

A minimal module that creates the standard `/health` (liveness) and `/ready` (readiness) endpoints by calling `create_health_router("scheduler")` from the shared library.

---

### 8. `scheduler/services/scheduler_engine.py`

**Purpose:** Core scheduling engine that evaluates all policies and selects the optimal placement.

**Domain models (Pydantic):**

#### `InferenceTask`
- `task_id: str` -- auto-generated UUID if not provided
- `model_name: str` -- name of the ML model to run
- `model_version: str = "latest"` -- model version
- `input_size_bytes: int = 0` -- size of the inference input payload
- `priority: str = "medium"` -- "low", "medium", or "high"
- `max_latency_ms: float = 100.0` -- maximum acceptable inference latency
- `requesting_node_id: str = ""` -- the edge node that submitted the task (for local preference)

#### `TaskPlacement`
- `task_id: str` -- matches the submitted task
- `target: str` -- one of "edge_local", "edge_remote", "cloud"
- `target_node_id: str = ""` -- the selected edge node (empty for cloud)
- `target_endpoint: str = ""` -- the URL or address to send the inference request to
- `estimated_latency_ms: float = 0` -- predicted latency for this placement
- `score: float = 0` -- policy confidence score (0.0 to 1.0)
- `reason: str = ""` -- human-readable explanation of the placement decision

**SchedulerEngine class:**

*Constructor:* Takes a `ResourceMonitor` and a list of `PlacementPolicy` instances. Maintains an internal `asyncio.Queue` for asynchronous task processing and a `dict` of completed placement results.

*Synchronous scheduling -- `submit_task(task)`:**
1. Retrieves the current resource map from the resource monitor (only non-stale nodes)
2. Iterates over all registered policies, calling `policy.evaluate(task, resources)`
3. Tracks the placement with the highest score
4. If no policy returns a result (all raised exceptions), falls back to a cloud placement with `score=0` and reason "no policy returned a placement; defaulting to cloud"
5. Stores the result in `self._results[task_id]` and returns it

*Asynchronous scheduling -- `enqueue_task(task)` / `drain_queue()`:**
Tasks can be added to an internal `asyncio.Queue` for background processing. The `drain_queue()` method processes all queued tasks in a single pass by calling `submit_task()` for each. It returns the count of tasks processed. The main module calls `drain_queue()` every `scheduling_interval_seconds` in a background loop.

*Result access -- `get_result(task_id)` / `get_all_results()`:**
Simple lookups into the in-memory results dictionary.

---

### 9. `scheduler/services/resource_monitor.py`

**Purpose:** In-memory store tracking the resource utilisation of all edge nodes.

**NodeResources model (Pydantic):**
- `node_id: str` -- unique identifier for the edge node
- `node_address: str = ""` -- network address (hostname:port or URL)
- `cpu_usage_percent: float = 0.0`
- `memory_usage_percent: float = 0.0`
- `gpu_usage_percent: float = 0.0`
- `pending_tasks: int = 0` -- number of inference tasks in the node's local queue
- `avg_inference_latency_ms: float = 0.0` -- moving average of inference latency
- `loaded_models: list[str]` -- model names currently loaded in memory
- `last_reported: datetime` -- timestamp of the last report (auto-set to UTC now)

**ResourceMonitor class:**

*Constructor:* Accepts a `stale_threshold` (default 30.0 seconds). Maintains a `dict[str, NodeResources]`.

*`update(report)`*: Accepts a new `NodeResources` report from an edge node. Overwrites the previous report for the same `node_id` and stamps `last_reported` to the current UTC time.

*`get_resource_map()`*: Returns a filtered dictionary of only non-stale nodes. For each stored node, it computes `(now - last_reported).total_seconds()` and excludes nodes exceeding the stale threshold. Handles timezone-naive datetimes by assuming UTC. Stale nodes are logged at debug level.

*`get_healthy_nodes()`*: Returns a list of non-stale nodes sorted by `cpu_usage_percent` ascending (least loaded first). This is used by policies to find the best candidate for edge placement.

*`get_node(node_id)`*: Returns the raw report for a node regardless of staleness (for diagnostics).

*`node_count` property*: Total number of nodes that have ever reported, including stale ones.

---

### 10. `scheduler/services/placement_policy.py`

**Purpose:** Abstract base class (ABC) defining the interface that all placement policies must implement.

```python
class PlacementPolicy(ABC):
    @abstractmethod
    def evaluate(
        self,
        task: InferenceTask,
        resources: dict[str, NodeResources],
    ) -> TaskPlacement:
        ...
```

The `evaluate` method receives the task to place and the current resource map. It must return a `TaskPlacement` with a `score` between 0.0 and 1.0. The scheduling engine selects the placement with the highest score across all policies.

Uses `TYPE_CHECKING` imports to avoid circular dependencies between the engine and policy modules.

---

### 11. `scheduler/policies/latency_first.py`

**Purpose:** Placement policy that minimises latency by always preferring edge nodes.

**Strategy:** Edge placement is the default. Cloud is used only as a last resort when every known edge node is overloaded.

**Overload thresholds:**
- CPU usage > 85%
- Memory usage > 90%

**Algorithm:**
1. If no edge nodes are known, return cloud placement with score 0.3
2. Partition nodes into healthy (below thresholds) and overloaded
3. If all nodes are overloaded, return cloud placement with score 0.35
4. Among healthy nodes, prefer the requesting node (local inference avoids network hops)
5. If the requesting node is not healthy, pick the node with the lowest CPU utilisation
6. Score: **0.9 for edge_local** (task runs on the same node that requested it), **0.8 for edge_remote** (task is routed to a different edge node)
7. Estimated latency uses the node's `avg_inference_latency_ms`, falling back to 10ms if unreported

**Scoring rationale:** The high base scores (0.8-0.9) mean this policy will "win" in the engine whenever edge capacity is available, embodying the philosophy that for 5G IoT workloads, minimising network latency is paramount.

---

### 12. `scheduler/policies/cost_aware.py`

**Purpose:** Placement policy that balances inference cost against placement location.

**Core insight:** Edge inference is essentially free (hardware is already provisioned), while cloud inference has per-request API costs. However, large models may run more efficiently on specialised cloud hardware (e.g. large GPUs).

**Size threshold:** `_SMALL_MODEL_SIZE_BYTES = 10 * 1024 * 1024` (10 MB)

**Cloud cost penalty:** `_CLOUD_COST_FACTOR = 0.15` -- subtracted from the cloud score to penalise cloud placement

**Algorithm:**
1. Collect healthy edge nodes (same CPU > 85% / memory > 90% thresholds)
2. **Small models (< 10 MB)** and healthy edge nodes exist:
   - Prefer the requesting node; fallback to least-loaded
   - Score: 0.85 for local, 0.75 for remote
   - Reason: small models are cheap to run locally
3. **Large models** or no healthy edge nodes:
   - Default to cloud
   - Cloud score: `0.7 - cloud_cost_factor` = 0.55 (penalised for cost)
   - If no edge nodes exist at all, cloud score boosted to 0.6
   - Minimum score clamped to 0.1

**Constructor parameters:** `cloud_endpoint` (URL), `cloud_cost_factor` (float), `small_model_threshold_bytes` (int) -- all configurable for different deployment profiles.

---

### 13. `scheduler/policies/balanced.py`

**Purpose:** Placement policy that combines latency and cost scores with configurable weights.

**Formula:** `combined = latency_weight * latency_score + cost_weight * cost_score`

**Default weights:** `latency_weight = 0.6`, `cost_weight = 0.4`

**Score computation:**

*Latency score for edge:* Linear scale from 1.0 (0ms latency) to 0.0 (at `max_latency_ms`). Uses the node's `avg_inference_latency_ms`.

*Latency score for cloud:* Same linear scale, assuming cloud latency of 50ms.

*Cost score for edge:* 0.9 for small models (< 10 MB), 0.5 for large models.

*Cost score for cloud:* 0.3 for small models (wasteful), 0.7 for large models (efficient use of cloud GPUs).

**Algorithm:**
1. For each healthy edge node, compute the combined weighted score
2. Track the edge node with the highest combined score
3. Compute the cloud's combined weighted score
4. If `best_edge_score >= cloud_score` and a healthy edge node exists, place on edge
5. Otherwise, place on cloud
6. The `reason` field includes both scores and the weights used, enabling operators to understand the tradeoff

**Example:** For a small model with a fast edge node (5ms avg latency, 100ms SLA):
- Edge latency score: `1.0 - 5/100 = 0.95`
- Edge cost score: `0.9`
- Combined: `0.6 * 0.95 + 0.4 * 0.9 = 0.93`
- Cloud latency score: `1.0 - 50/100 = 0.5`
- Cloud cost score: `0.3`
- Combined: `0.6 * 0.5 + 0.4 * 0.3 = 0.42`
- Result: edge wins (0.93 vs 0.42)

---

### 14. `scheduler/services/adaptive_scheduler.py`

**Purpose:** A self-tuning placement policy that uses an exponential moving average (EMA) of observed edge inference latency to dynamically adjust placement thresholds.

**Key constants:**
- `_DEFAULT_WINDOW_SIZE = 50` -- max observations in the deque
- `_CPU_OVERLOAD_THRESHOLD = 80.0%` -- initial threshold (can be dynamically adjusted)
- `_MEMORY_OVERLOAD_THRESHOLD = 90.0%` -- fixed
- `_EMA_ALPHA = 0.3` -- smoothing factor (higher = more responsive to recent observations)

**Feedback loop -- `record_latency(latency_ms)`:**
Called after each edge inference completes. Updates the EMA:

```
ema = alpha * new_observation + (1 - alpha) * previous_ema
```

Then calls `_adjust_thresholds()`:
- Computes `ratio = ema_latency / sla_target`
- If `ratio < 0.5` (latency well within SLA): **relaxes** the CPU threshold by +1.0, up to 90%
  - This allows more tasks to be placed on edge when conditions are good
- If `ratio > 0.9` (latency approaching SLA limit): **tightens** the CPU threshold by -2.0, down to 60%
  - This proactively shifts load to cloud before the SLA is breached

**Placement evaluation -- `evaluate(task, resources)`:**
1. If no edge nodes are reporting, return cloud with score 0.5
2. Check if `ema_latency <= sla_target` (or no observations yet -- optimistic default)
3. Find a suitable edge node: skip nodes where CPU > dynamic threshold or memory > 90%
4. Prefer the requesting node; otherwise pick the least-loaded
5. If a suitable node exists AND latency is within SLA:
   - Place on edge (local or remote)
   - Score: `max(0.0, min(1.0, 1.0 - sla_ratio * 0.5))` -- higher when latency is well below SLA
6. Otherwise: place on cloud with score 0.4
7. The reason string includes the current EMA latency and dynamic CPU threshold for observability

**Introspection properties:**
- `ema_latency` -- current moving average for monitoring
- `dynamic_cpu_threshold` -- current adjusted threshold for debugging

**Design note:** While the adaptive scheduler is instantiated and stored on `app.state.adaptive_scheduler`, the main scheduling engine's policy list contains only the three static policies (LatencyFirst, CostAware, Balanced). The adaptive scheduler is available for direct use by services that want feedback-aware placement but does not participate in the default "highest score wins" competition. This is intentional -- the adaptive scheduler requires a latency feedback stream and is meant for advanced use cases where the calling service can provide `record_latency()` callbacks.

---

### 15-18. `__init__.py` files

Package markers for:
- `scheduler/__init__.py`
- `scheduler/routers/__init__.py`
- `scheduler/services/__init__.py`
- `scheduler/policies/__init__.py`

---

## Design Patterns

### Strategy Pattern
The placement policies implement the Strategy design pattern. The `PlacementPolicy` ABC defines a common interface (`evaluate`), and each concrete policy (`LatencyFirstPolicy`, `CostAwarePolicy`, `BalancedPolicy`, `AdaptiveScheduler`) provides a different algorithm. The `SchedulerEngine` holds a list of strategies and iterates over all of them, selecting the one with the highest score. This makes it trivial to:
- Add new policies by implementing `PlacementPolicy.evaluate()`
- Remove or reorder policies via configuration
- A/B test policies by adjusting their score ranges

### Feedback Loop (Adaptive Scheduling)
The `AdaptiveScheduler` implements a closed-loop control system:
1. **Observation:** Edge inference latency is fed back via `record_latency()`
2. **Smoothing:** An EMA filters out noise while remaining responsive to trends
3. **Adjustment:** The CPU overload threshold is raised or lowered based on the latency-to-SLA ratio
4. **Action:** Placement decisions use the dynamically adjusted threshold

This prevents oscillation (thanks to EMA smoothing) while allowing the system to gradually shift load between edge and cloud as conditions change.

### Resource Monitoring with Staleness Detection
The `ResourceMonitor` follows an event-driven design where edge nodes push reports rather than being polled. Staleness detection (configurable threshold, default 30s) ensures that the scheduler never routes tasks to nodes that have stopped reporting -- which could indicate a crash, network partition, or overload.

### Dual-Protocol Server
The service runs both HTTP (FastAPI on 8005) and gRPC (on 50053) simultaneously using `asyncio.create_task()`. HTTP is used for human-facing APIs (task submission, resource queries), while gRPC enables high-throughput, low-latency inter-service communication for real-time scheduling requests.

### Background Task Queue
The engine supports both synchronous (request-path) and asynchronous (queue-based) scheduling. The background drain loop processes queued tasks every second (configurable), allowing the system to absorb bursts without blocking HTTP requests.

---

## How It Works Together

### Task submission flow

```
Client / Edge Node
       |
       | POST /api/v1/tasks  {model_name, input_size_bytes, max_latency_ms, ...}
       v
  [tasks.py router]
       |
       v
  [SchedulerEngine.submit_task()]
       |
       +---> [ResourceMonitor.get_resource_map()]  -- filter stale nodes
       |
       +---> [LatencyFirstPolicy.evaluate()]   --> TaskPlacement(score=0.9, target=edge_local)
       +---> [CostAwarePolicy.evaluate()]      --> TaskPlacement(score=0.85, target=edge_local)
       +---> [BalancedPolicy.evaluate()]        --> TaskPlacement(score=0.93, target=edge_local)
       |
       +---> Pick highest score (BalancedPolicy wins at 0.93)
       |
       v
  Return TaskPlacement to client
```

### Resource reporting flow

```
Edge Node (periodic, every 5-10s)
       |
       | POST /api/v1/resources/report  {node_id, cpu, memory, gpu, ...}
       v
  [resources.py router]
       |
       v
  [ResourceMonitor.update()]  -- overwrite previous report, stamp last_reported
```

### Adaptive feedback flow

```
Edge Node completes inference
       |
       v
  AdaptiveScheduler.record_latency(latency_ms)
       |
       +---> Update EMA: ema = 0.3 * new + 0.7 * old
       +---> Adjust CPU threshold based on ema/sla ratio
       |
  (Next placement evaluation uses adjusted threshold)
```
