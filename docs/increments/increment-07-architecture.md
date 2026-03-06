# Increment 7 -- System Architecture

## Goal

Finalise all service containers, health checks, and Kubernetes manifests into a cohesive microservices deployment. This increment establishes the structural conventions that every service follows and assembles the K3s manifests that deploy the entire system to a lightweight Kubernetes cluster suitable for edge environments.

---

## Architecture Principles

### 1. Independently Deployable Services
Each microservice is a self-contained Python package with its own `pyproject.toml`, `Dockerfile`, configuration module, and health endpoints. Services can be built, tested, versioned, and deployed independently without coordinating with other teams.

### 2. Shared Library as Local Package
The `shared/` library is installed as a local pip package in each Docker image (`COPY shared/ && pip install shared/`). This provides common infrastructure (configuration, database clients, Redis wrappers, logging, metrics, health checks, JWT handling, Pydantic models) without requiring a private PyPI registry. The shared library is copied first in the Dockerfile to exploit Docker layer caching -- service code changes do not invalidate the shared dependency layer.

### 3. Universal Health Endpoints
Every service exposes `/health` (liveness) and `/ready` (readiness) endpoints via `shared.utils.health.create_health_router()`. These return JSON responses with service name, version, and status. Kubernetes probes are configured to use these endpoints for automated health management.

### 4. Kustomize for K3s Manifest Management
All Kubernetes manifests live under `deploy/k3s/` and are orchestrated by a root `kustomization.yaml`. Kustomize provides base/overlay composition without template engines, keeping manifests as plain YAML that can be validated and diffed.

### 5. Traefik Ingress for External Traffic
K3s ships with Traefik as the default ingress controller. External traffic enters through the middleware service (port 8000), which acts as the API gateway and reverse-proxies requests to internal services.

---

## Service Port Map

| Service | HTTP Port | gRPC Port | Type | Description |
|---------|-----------|-----------|------|-------------|
| middleware | 8000 | -- | API Gateway | Auth, rate limiting, proxying |
| data-ingestion | 8001 | -- | Edge | MQTT/CoAP/HTTP telemetry ingestion |
| device-manager | 8002 | -- | Edge | Device CRUD, provisioning, firmware |
| coordination | 8003 | 50052 | Edge | Leader election, gossip, cluster state |
| ml-inference | 8004 | -- | Edge | Real-time anomaly detection |
| scheduler | 8005 | 50053 | Edge | Edge/cloud placement decisions |
| ml-training | 8006 | -- | Cloud | Model training pipelines |
| batch-analytics | 8007 | -- | Cloud | PySpark batch analytics |
| cloud-api | 8080 | 50051 | Cloud | Cloud gateway, model registry |

**Port allocation convention:** Edge services use ports 8000-8005, cloud services use 8006-8007 and 8080. gRPC ports are in the 50051-50053 range.

---

## Shared Library (`shared/`)

The shared library is the foundation layer that all services depend on. It provides:

### `shared/config.py` -- BaseServiceSettings
All services inherit from `BaseServiceSettings`, which provides:
- `service_name`, `environment`, `log_level`
- `redis_url`, `redis_max_connections`
- `postgres_dsn` (async PostgreSQL connection string)
- `jwt_secret`, `jwt_algorithm`, `jwt_expiration_minutes`
- `prometheus_port`, `enable_tracing`
- Pydantic Settings with `.env` file support and `extra = "ignore"`

### `shared/database/postgres.py` -- Async PostgreSQL
- `init_db(dsn)` -- creates an async SQLAlchemy engine and runs `Base.metadata.create_all()`
- `close_db()` -- disposes the engine
- `get_async_session(dsn)` -- async generator yielding `AsyncSession` instances

### `shared/database/redis_client.py` -- RedisClient
Wraps the `redis.asyncio` client with:
- Connection pooling (`max_connections`)
- `stream_add()` for Redis Streams publishing
- `get()`, `set()`, `delete()` for key-value operations
- `ping()` for health checks
- `close()` for clean shutdown

### `shared/auth/jwt_handler.py` -- JWT Operations
- `create_token(payload, secret, algorithm, expires_minutes)` -- signs a JWT with `iat` and `exp` claims
- `decode_token(token, secret, algorithm)` -- verifies and decodes a JWT
- `JWTError` -- custom exception wrapping PyJWT errors

### `shared/observability/logging_config.py` -- Structured Logging
- `setup_logging(service_name, log_level)` -- configures structlog with JSON output
- `get_logger(name)` -- returns a bound logger

### `shared/observability/metrics.py` -- Prometheus Metrics
Pre-defined metrics used across services:
- `TELEMETRY_RECEIVED` (Counter) -- by device_type and protocol
- `TELEMETRY_PROCESSED` (Counter) -- by device_type and status
- `STREAM_MESSAGES_PUBLISHED` / `STREAM_MESSAGES_CONSUMED` (Counters) -- by stream/group
- `PROCESSING_LATENCY` (Histogram) -- by service and operation
- `INFERENCE_LATENCY` (Histogram) -- by model_name
- `ANOMALIES_DETECTED` (Counter) -- by device_type and severity
- `ACTIVE_DEVICES` (Gauge) -- by device_type
- `SERVICE_INFO` (Info) -- service metadata

### `shared/streams/constants.py` -- Stream Names
Canonical Redis Stream names:
- `StreamName.RAW_TELEMETRY` = "raw_telemetry"
- `StreamName.ALERTS` = "alerts"
- `StreamName.DEVICE_EVENTS` = "device_events"
- `StreamName.INFERENCE_RESULTS` = "inference_results"

Consumer group names:
- `ConsumerGroup.ML_INFERENCE`, `DATA_PERSISTENCE`, `ALERT_HANDLER`

### `shared/utils/health.py` -- Health Router Factory
`create_health_router(service_name, version)` returns a FastAPI `APIRouter` with:
- `GET /health` -- returns `{"status": "ok", "service": "...", "version": "..."}`
- `GET /ready` -- returns `{"status": "ready"}`

### `shared/models/` -- Pydantic Domain Models
- `device.py` -- `DeviceCreate`, `DeviceStatus`, `DeviceType`
- `telemetry.py` -- telemetry reading schemas
- `alert.py` -- alert schemas

---

## Dockerfile Pattern

All services follow the same multi-step build pattern:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 1. Install system dependencies (gcc for native extensions, libpq-dev for psycopg)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# 2. Copy and install shared library first (layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

# 3. Copy and install service code
COPY services/<service-name>/ /app/service/
RUN pip install --no-cache-dir /app/service/

# 4. Expose service port(s)
EXPOSE <http-port> [<grpc-port>]

# 5. Run the service
CMD ["uvicorn", "<module>.main:app", "--host", "0.0.0.0", "--port", "<port>"]
```

**Variations:**
- Services with gRPC (scheduler, coordination) use `python -m <module>.main` instead of uvicorn directly, since their main module starts both HTTP and gRPC servers
- The ml-training service has additional PyTorch dependencies
- The batch-analytics service uses a Spark base image (`deploy/docker/spark-base.Dockerfile`)

---

## K3s Manifest Structure

```
deploy/k3s/
|-- namespace.yaml                          # iot-gateway namespace
|-- kustomization.yaml                      # Kustomize root
|
|-- configmaps/
|   +-- common-config.yaml                  # Shared env vars (ENVIRONMENT, LOG_LEVEL, REDIS_URL, MQTT settings)
|
|-- secrets/
|   +-- db-credentials.yaml                 # POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DSN, JWT_SECRET
|
|-- infrastructure/
|   |-- redis/
|   |   |-- pvc.yaml                        # PersistentVolumeClaim for Redis data
|   |   |-- deployment.yaml                 # Redis 7.x single-instance deployment
|   |   +-- service.yaml                    # ClusterIP service on port 6379
|   |
|   |-- postgres/
|   |   |-- statefulset.yaml                # PostgreSQL 16 StatefulSet with persistent storage
|   |   +-- service.yaml                    # ClusterIP service on port 5432
|   |
|   +-- mosquitto/
|       |-- deployment.yaml                 # Eclipse Mosquitto MQTT broker
|       +-- service.yaml                    # ClusterIP service on port 1883
|
|-- services/
|   |-- data-ingestion/
|   |   |-- deployment.yaml                 # Deployment with health probes, resource limits
|   |   |-- service.yaml                    # ClusterIP service on port 8001
|   |   +-- hpa.yaml                        # HorizontalPodAutoscaler (CPU-based)
|   |
|   +-- data-persistence/
|       |-- deployment.yaml                 # Deployment for the persistence consumer
|       +-- service.yaml                    # ClusterIP service
|
+-- monitoring/
    |-- prometheus/
    |   |-- configmap.yaml                  # Prometheus scrape configuration
    |   |-- deployment.yaml                 # Prometheus server
    |   |-- pvc.yaml                        # Persistent storage for metrics
    |   +-- service.yaml                    # ClusterIP on port 9090
    |
    |-- grafana/
    |   |-- configmap.yaml                  # Grafana datasource and dashboard provisioning
    |   |-- dashboards/
    |   |   +-- gateway-overview.json       # Pre-built dashboard for IoT gateway metrics
    |   |-- deployment.yaml                 # Grafana server
    |   +-- service.yaml                    # ClusterIP on port 3000
    |
    +-- alertmanager/
        |-- configmap.yaml                  # Alert routing and receiver configuration
        +-- deployment.yaml                 # Alertmanager instance
```

### `namespace.yaml`
Creates the `iot-gateway` namespace with the label `app.kubernetes.io/part-of: iot-edge-gateway`.

### `kustomization.yaml`
The root Kustomize file sets `namespace: iot-gateway` and enumerates all resources in dependency order:
1. Namespace
2. ConfigMaps and Secrets
3. Infrastructure (Redis, PostgreSQL, Mosquitto)
4. Services (data-ingestion, data-persistence, and others as they are added)

Applies `commonLabels: { app.kubernetes.io/part-of: iot-edge-gateway }` to all resources.

### `configmaps/common-config.yaml`
Shared environment variables injected into all service pods via `envFrom`:
- `ENVIRONMENT: "production"`
- `LOG_LEVEL: "INFO"`
- `REDIS_URL: "redis://redis:6379/0"`
- `MQTT_BROKER_HOST: "mosquitto"`
- `MQTT_BROKER_PORT: "1883"`

### `secrets/db-credentials.yaml`
Sensitive configuration stored as Kubernetes Secrets:
- `POSTGRES_USER: iot_gateway`
- `POSTGRES_PASSWORD: changeme`
- `POSTGRES_DSN: postgresql+asyncpg://iot_gateway:changeme@postgres:5432/iot_gateway`
- `JWT_SECRET: change-this-to-a-secure-random-string`

**Note:** In production, these should be managed by an external secret store (e.g. HashiCorp Vault, AWS Secrets Manager) and injected via a CSI driver.

---

## Health Check Pattern

Every service uses the shared health router:

```python
from shared.utils.health import create_health_router

app.include_router(create_health_router("service-name"))
```

This provides:
- **`GET /health`** (liveness probe) -- returns `{"status": "ok", "service": "<name>", "version": "0.1.0"}`. If this fails, Kubernetes restarts the pod.
- **`GET /ready`** (readiness probe) -- returns `{"status": "ready"}`. If this fails, Kubernetes removes the pod from the Service's endpoint list (no traffic routed).

K3s deployment manifests configure these probes:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: <http-port>
  initialDelaySeconds: 10
  periodSeconds: 15
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /ready
    port: <http-port>
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 2
```

The middleware service extends this with `GET /health/dependencies` that actively checks connectivity to Redis and all four upstream services, returning a composite health status.

---

## Infrastructure Components

### Redis
- **Role:** Event bus (Redis Streams), caching (rollout state, API keys, rate limiting), inter-service communication
- **Deployment:** Single-instance with a PersistentVolumeClaim for data durability
- **Port:** 6379 (ClusterIP, internal only)

### PostgreSQL
- **Role:** Primary relational database for device state, provisioning records, firmware catalog, telemetry aggregates
- **Deployment:** StatefulSet with persistent storage for data durability and stable network identity
- **Port:** 5432 (ClusterIP, internal only)

### Mosquitto
- **Role:** MQTT broker for IoT device telemetry ingestion (MQTT 3.1.1/5.0)
- **Deployment:** Single-instance
- **Port:** 1883 (ClusterIP, internal only; exposed externally via NodePort or ingress for device connectivity)

---

## Monitoring Stack

### Prometheus
Scrapes `/metrics` endpoints from all services (exposed via `prometheus-client` Python library). Pre-configured with scrape targets for all service ports. Stores metrics with configurable retention on a PersistentVolumeClaim.

### Grafana
Pre-provisioned with:
- Prometheus as a datasource
- A `gateway-overview.json` dashboard showing:
  - Active devices by type
  - Telemetry ingestion rate
  - Inference latency distribution
  - Stream message throughput
  - HTTP request rates and error rates

### Alertmanager
Receives alerts from Prometheus alert rules and routes them to configured receivers (email, Slack, PagerDuty, etc.).

---

## Service Communication Patterns

```
External Clients
       |
       v
  [Middleware :8000]  -- auth, rate limit, logging, proxy
       |
       +----> [device-manager :8002]    (REST)
       +----> [data-ingestion :8001]    (REST)
       +----> [ml-inference :8004]      (REST)
       +----> [scheduler :8005]         (REST)

IoT Devices
       |
       +----> [Mosquitto :1883]  -- MQTT telemetry
       |           |
       |           v
       |      [data-ingestion :8001]  -- subscribes to MQTT topics
       |
       +----> [data-ingestion :8001]  -- CoAP (:5683) / HTTP REST

Internal Service Communication
       |
       +----> [coordination :8003/50052]  -- gRPC gossip, leader election
       +----> [scheduler :8005/50053]     -- gRPC scheduling requests
       +----> [cloud-api :8080/50051]     -- gRPC model sync, cloud offload

Redis Streams (Event Bus)
       |
       +----> raw_telemetry:    data-ingestion --> ml-inference, data-persistence
       +----> device_events:    device-manager --> coordination, data-persistence
       +----> alerts:           ml-inference --> alert handlers
       +----> inference_results: ml-inference --> data-persistence, cloud-api
```

---

## Deployment Topology

### Edge Node
Each edge gateway runs the full stack minus cloud-only services:
- middleware, data-ingestion, device-manager, coordination, ml-inference, scheduler
- Redis, PostgreSQL, Mosquitto
- Prometheus, Grafana (optional on resource-constrained nodes)

### Cloud
- cloud-api, ml-training, batch-analytics
- Central Prometheus/Grafana aggregation
- Model registry and training data lake

### Cross-Edge Communication
The coordination service (Increment 4) uses gRPC gossip and Redis Streams to replicate device state, model updates, and scheduling decisions across edge nodes. The cloud-api bridges edge and cloud for model synchronisation and batch analytics data transfer.
