# Increment 5 -- Replication & Consistency

## Goal

Deliver a complete device management service with full CRUD operations, a provisioning approval workflow, firmware/OTA rollout management, and event-driven state replication across edge nodes via Redis Streams. Every state mutation emits a domain event so that other services (and other edge gateways) can subscribe, replay, and converge on a consistent view of the device fleet.

---

## Files Created

### Device Manager Service (`services/device-manager/`)

This service owns the canonical state of every IoT device registered in the gateway. It exposes a RESTful API on port 8002, persists device records in PostgreSQL, and publishes lifecycle events to the `device_events` Redis Stream.

The service contains **17 files** organised in a layered architecture: routers (HTTP) -> services (business logic) -> repositories (data access) -> ORM models (database).

---

### 1. `pyproject.toml`

**Purpose:** Python package definition and dependency manifest for the device-manager service.

**Dependencies:**
- `fastapi>=0.104.0` -- async web framework for the REST API
- `uvicorn[standard]>=0.24.0` -- ASGI server
- `sqlalchemy[asyncio]>=2.0.23` -- async ORM for PostgreSQL
- `asyncpg>=0.29.0` -- high-performance PostgreSQL driver for asyncio
- `redis>=5.0.0` -- Redis client for event streaming and caching
- `pydantic-settings>=2.1.0` -- environment-based configuration
- `structlog>=23.2.0` -- structured logging
- `prometheus-client>=0.19.0` -- Prometheus metrics instrumentation
- `shared` -- the project's shared library (local path dependency)

**Dev dependencies** include pytest, pytest-asyncio, pytest-cov, and httpx for testing.

The `[tool.setuptools.packages.find]` section restricts package discovery to `device_manager*`. A `[tool.pip.find-links]` hint tells pip where to find the shared library at `../../shared` during local development.

---

### 2. `Dockerfile`

**Purpose:** Container image definition for the device-manager service.

**Build stages:**
1. Base image: `python:3.11-slim`
2. Installs system dependencies (`gcc`) needed for compiling native extensions
3. Copies the `shared/` library and installs it first -- this exploits Docker layer caching so that shared code changes do not invalidate the heavier service dependency layer
4. Copies and installs the `services/device-manager/` package
5. Exposes port `8002`
6. Entrypoint: `uvicorn device_manager.main:app --host 0.0.0.0 --port 8002`

This follows the standard Dockerfile pattern used across all services in the project, ensuring consistent build semantics and layer caching.

---

### 3. `device_manager/config.py`

**Purpose:** Service-specific configuration.

Defines a `Settings` class that extends `shared.config.BaseServiceSettings` (which provides Redis URL, PostgreSQL DSN, JWT settings, logging level, and other base fields). The device-manager adds:

- `service_name: str = "device-manager"` -- used in logging, metrics, and health checks
- `http_port: int = 8002` -- the HTTP listener port

All settings can be overridden via environment variables thanks to Pydantic Settings.

---

### 4. `device_manager/main.py`

**Purpose:** FastAPI application entry point and lifecycle management.

**Lifespan handler (`async def lifespan`):**
- **Startup:**
  1. Instantiates `Settings()` and configures structured logging via `setup_logging()`
  2. Publishes service metadata (name, version, environment) to the `SERVICE_INFO` Prometheus Info metric
  3. Initialises a `RedisClient` for event streaming and caching
  4. Calls `init_db()` to create the async SQLAlchemy engine and run table creation against PostgreSQL
  5. Stores `redis_client` and `settings` on `app.state` for dependency injection into routers and services
- **Shutdown:**
  1. Closes the Redis connection pool
  2. Closes the PostgreSQL connection pool via `close_db()`

**Router registration:**
- `create_health_router("device-manager")` -- provides `/health` (liveness) and `/ready` (readiness)
- `devices_router` at `/api/v1` -- device CRUD endpoints
- `provisioning_router` at `/api/v1` -- provisioning workflow endpoints
- `firmware_router` at `/api/v1` -- firmware management endpoints

The module imports `device_manager.db.models` as a side-effect so SQLAlchemy's `Base.metadata` discovers all table definitions before `init_db()` is called.

---

### 5. `device_manager/db/models.py`

**Purpose:** SQLAlchemy ORM model definitions for all database tables.

**Enums:**
- `DeviceStatusEnum` -- lifecycle states: `REGISTERED`, `PROVISIONED`, `ACTIVE`, `INACTIVE`, `DECOMMISSIONED`
- `ProvisioningStatusEnum` -- request states: `PENDING`, `APPROVED`, `REJECTED`

**Models:**

#### `Device`
- **Table:** `devices`
- **Primary key:** `id` -- UUID, auto-generated via `uuid.uuid4()`
- **Columns:**
  - `name` (String 255, indexed) -- human-readable device label
  - `device_type` (String 100, indexed) -- e.g. "temperature_sensor", "camera"
  - `status` (Enum DeviceStatusEnum, indexed, default REGISTERED) -- lifecycle state
  - `firmware_version` (String 50, default "1.0.0") -- current firmware
  - `last_seen_at` (DateTime, nullable) -- last telemetry heartbeat
  - `metadata_` (JSON, mapped from column name "metadata") -- arbitrary JSONB metadata
  - `created_at`, `updated_at` (DateTime with timezone) -- automatic timestamps
- **Composite index:** `ix_devices_type_status` on `(device_type, status)` for filtered queries
- **Relationship:** `provisioning_requests` (one-to-many with `ProvisioningRequest`, lazy `selectin`)

#### `DeviceGroup`
- **Table:** `device_groups`
- **Columns:** `id` (UUID PK), `name` (String 255, unique, indexed), `description` (Text, nullable), `config` (JSON)
- **Purpose:** Logical grouping for bulk operations (e.g. firmware rollouts to all sensors in zone A)

#### `FirmwareVersion`
- **Table:** `firmware_versions`
- **Columns:** `id` (UUID PK), `version` (String 50, indexed), `device_type` (String 100, indexed), `checksum` (String 128), `file_url` (Text), `release_notes` (Text, nullable), `created_at`
- **Unique constraint:** Composite unique index `ix_firmware_type_version` on `(device_type, version)` -- prevents duplicate firmware versions per device type

#### `ProvisioningRequest`
- **Table:** `provisioning_requests`
- **Columns:** `id` (UUID PK), `device_id` (UUID FK to `devices.id`, CASCADE delete, indexed), `status` (Enum ProvisioningStatusEnum, default PENDING), `requested_at`, `approved_at` (nullable), `approved_by` (String 255, nullable)
- **Relationship:** `device` (many-to-one back to `Device`)

All timestamps use `datetime.now(timezone.utc)` via the `_utcnow()` helper to ensure UTC-aware values.

---

### 6. `device_manager/repositories/device_repo.py`

**Purpose:** Data access layer implementing the Repository pattern for device CRUD.

All functions accept an `AsyncSession` and return ORM model instances. The repository is purely concerned with data access -- no business rules, no events.

**Functions:**

#### `list_devices(session, device_type?, status?, limit=50, offset=0) -> (list[Device], int)`
Builds a filtered, paginated query. Applies optional `WHERE` clauses for `device_type` and `status`. Executes a parallel `COUNT` query for the total matching rows, then applies `ORDER BY created_at DESC`, `OFFSET`, and `LIMIT`. Returns both the device list and the total count for pagination metadata.

#### `get_device(session, device_id) -> Device | None`
Single-row lookup by UUID primary key using `scalar_one_or_none()`.

#### `create_device(session, device_data) -> Device`
Constructs a `Device` from the data dictionary, adds it to the session, commits, refreshes, and returns the new ORM instance with its server-generated UUID and timestamps.

#### `update_device(session, device_id, updates) -> Device | None`
Uses SQLAlchemy's `update().where().values().returning()` for an atomic partial update. Commits and refreshes to return the updated row. Returns `None` if the device ID does not exist.

#### `delete_device(session, device_id) -> bool`
Implements **soft delete**: sets the device status to `DECOMMISSIONED` rather than physically removing the row. Uses `update().where().values(status=DECOMMISSIONED).returning(Device.id)`. Returns `True` if a row was updated, `False` otherwise.

#### `get_stats(session) -> dict`
Executes two GROUP BY queries: one counting devices by `device_type`, another by `status`. Returns a dictionary with `by_type`, `by_status`, and `total` keys. Handles both raw string and enum values for the status key.

---

### 7. `device_manager/routers/devices.py`

**Purpose:** HTTP router for device CRUD endpoints.

**Response schemas (Pydantic models):**
- `DeviceResponse` -- serialises an ORM Device with `from_attributes=True` for automatic mapping
- `DeviceListResponse` -- wraps a list of devices with `total`, `limit`, `offset` pagination metadata
- `DeviceUpdate` -- partial update schema with all fields optional
- `DeviceStatsResponse` -- `by_type`, `by_status`, `total`

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/devices/stats` | Aggregate fleet statistics by type and status |
| GET | `/api/v1/devices` | Paginated device list with optional `device_type` and `status` query filters (limit max 500) |
| GET | `/api/v1/devices/{device_id}` | Single device lookup by UUID; returns 404 if not found |
| POST | `/api/v1/devices` | Register a new device; returns 201 with the created resource |
| PUT | `/api/v1/devices/{device_id}` | Partial update; maps Pydantic field names to ORM column names (e.g. `metadata` -> `metadata_`); returns 409 if business rule violated, 404 if not found |
| DELETE | `/api/v1/devices/{device_id}` | Soft-delete (decommission); returns 409 if the device is active, 404 if not found |

**Important detail:** The `/stats` route is declared before `/{device_id}` to avoid FastAPI treating "stats" as a UUID path parameter.

The helper `_device_to_response()` converts ORM instances to Pydantic response models, handling enum `.value` extraction and ISO timestamp formatting.

---

### 8. `device_manager/routers/provisioning.py`

**Purpose:** HTTP router for the device provisioning workflow.

**Schemas:**
- `ProvisioningRequestBody` -- `device_id` (UUID) and optional `credentials` (dict)
- `ProvisioningApproveBody` -- `approved_by` (str)
- `ProvisioningResponse` -- serialised provisioning request state

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/provision/request` | Device submits a provisioning request; returns 201. Validates device is in REGISTERED status and has no existing pending request (409 on conflict). |
| POST | `/api/v1/provision/approve/{request_id}` | Admin approves a pending request; transitions device to PROVISIONED status. Returns 409 if request is not pending. |
| GET | `/api/v1/provision/status/{device_id}` | Returns the most recent provisioning request for the device; 404 if none found. |

---

### 9. `device_manager/routers/firmware.py`

**Purpose:** HTTP router for firmware version management and OTA rollouts.

**Schemas:**
- `FirmwareCreateBody` -- version, device_type, checksum, file_url, release_notes
- `FirmwareResponse` -- firmware version metadata
- `RolloutRequestBody` -- firmware_id (UUID) and target_devices (list of device IDs)
- `RolloutResponse` -- rollout_id, firmware metadata, target list, status, started_at

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/firmware` | Register a new firmware version; 409 if version+type already exists |
| GET | `/api/v1/firmware` | List firmware versions with optional `device_type` filter |
| POST | `/api/v1/firmware/rollout` | Start an OTA rollout; publishes event to Redis Stream; stores rollout state in Redis with 24h TTL |
| GET | `/api/v1/firmware/rollout/{rollout_id}` | Check rollout status; reads from Redis; 404 if expired or not found |

---

### 10. `device_manager/services/device_service.py`

**Purpose:** Business logic layer for device lifecycle management. Sits between the router and repository layers.

**Key responsibilities:**
1. **Repository orchestration** -- delegates data access to `device_repo`
2. **Event emission** -- publishes domain events to the `device_events` Redis Stream via `_emit_device_event()`
3. **Metrics instrumentation** -- updates the `ACTIVE_DEVICES` Prometheus Gauge on status transitions
4. **Business rule enforcement** -- raises `ValueError` for invalid operations

**Private helper -- `_emit_device_event(redis, event_type, device_id, payload?)`:**
Constructs a structured event dict with `event_type`, `device_id`, `timestamp` (ISO 8601 UTC), and optional JSON-serialised payload. Publishes to `StreamName.DEVICE_EVENTS` ("device_events") via `redis.stream_add()`.

**Functions:**

#### `list_devices(session, ...)` / `get_device(session, device_id)` / `get_stats(session)`
Thin pass-throughs to the repository -- no additional business logic needed for reads.

#### `create_device(session, redis, device_data)`
1. Calls `device_repo.create_device()` to persist the new device
2. Emits a `device_created` event with `name` and `device_type` in the payload
3. Initialises the `ACTIVE_DEVICES` gauge label for this device type (`.inc(0)`)
4. Returns the new device

#### `update_device(session, redis, device_id, updates)`
1. Fetches the existing device to check preconditions
2. **Business rule:** raises `ValueError("Cannot update a decommissioned device.")` if status is `DECOMMISSIONED` -- this surfaces as HTTP 409 in the router
3. Calls `device_repo.update_device()`
4. Emits a `device_updated` event with the update payload
5. Adjusts the `ACTIVE_DEVICES` gauge: increments if transitioning to ACTIVE, decrements if transitioning away from ACTIVE

#### `delete_device(session, redis, device_id)`
1. Fetches the existing device
2. **Business rule:** raises `ValueError("Cannot decommission an active device. Set status to INACTIVE first.")` if device is ACTIVE
3. **Business rule:** raises `ValueError("Device is already decommissioned.")` if already decommissioned
4. Calls `device_repo.delete_device()` (soft delete)
5. Emits a `device_decommissioned` event
6. Decrements the `ACTIVE_DEVICES` gauge if the device was previously active

---

### 11. `device_manager/services/provision_service.py`

**Purpose:** Business logic for the provisioning workflow.

**Functions:**

#### `request_provisioning(session, redis, device_id)`
1. Validates the device exists (raises `ValueError` if not)
2. Validates the device is in `REGISTERED` status (raises `ValueError` with current status otherwise)
3. Checks for an existing `PENDING` provisioning request (raises `ValueError` for duplicate)
4. Creates a new `ProvisioningRequest` with `PENDING` status
5. Emits a `provisioning_requested` event to the `device_events` stream

#### `approve_provisioning(session, redis, request_id, approved_by)`
1. Looks up the provisioning request (raises `ValueError` if not found)
2. Validates it is in `PENDING` status (raises `ValueError` if already processed)
3. Updates the request: sets status to `APPROVED`, records `approved_at` timestamp and `approved_by` identity
4. Transitions the associated device to `PROVISIONED` status
5. Commits both changes atomically in the same session
6. Emits a `provisioning_approved` event

#### `get_provisioning_status(session, device_id)`
Queries for the most recent provisioning request for the device, ordered by `requested_at DESC`, limited to 1 row.

---

### 12. `device_manager/services/firmware_service.py`

**Purpose:** Business logic for firmware version management and OTA rollouts.

**Functions:**

#### `register_firmware(session, version, device_type, checksum, file_url, release_notes?)`
1. Checks for a duplicate (same version + device_type); raises `ValueError` if found
2. Creates a new `FirmwareVersion` ORM instance, commits, and returns it

#### `list_firmware(session, device_type?)`
Queries all firmware versions ordered by `created_at DESC`, with an optional `device_type` filter.

#### `start_rollout(session, redis, firmware_id, target_devices)`
1. Validates the firmware exists (raises `ValueError` if not)
2. Generates a UUID rollout_id
3. Constructs a rollout record dict containing firmware metadata, target device list, status "in_progress", and a timestamp
4. Stores the rollout record in Redis under key `firmware_rollout:{rollout_id}` with a **24-hour TTL** (86400 seconds) as JSON
5. Publishes a `firmware_rollout_started` event to the `device_events` stream with all rollout details
6. Returns the rollout record

#### `get_rollout_status(redis, rollout_id)`
Reads the rollout JSON from Redis key `firmware_rollout:{rollout_id}`. Returns `None` if the key has expired or does not exist.

---

### 13-17. `__init__.py` files

The following `__init__.py` files exist as empty package markers:
- `device_manager/__init__.py`
- `device_manager/db/__init__.py`
- `device_manager/repositories/__init__.py`
- `device_manager/routers/__init__.py`
- `device_manager/services/__init__.py`

---

## Design Patterns

### Repository Pattern
The `device_repo.py` module isolates all database access behind a set of pure async functions. The service layer never constructs SQL queries directly -- it calls repository functions that accept an `AsyncSession` and return domain objects. This separation makes it straightforward to:
- Unit test business logic with a mocked repository
- Swap the storage backend (e.g. from PostgreSQL to TimescaleDB) without changing service code
- Keep SQL concerns out of the HTTP layer

### Service Layer Pattern
`device_service.py`, `provision_service.py`, and `firmware_service.py` form the service layer. They orchestrate repository calls, enforce business rules, emit events, and update metrics. The routers are kept thin -- they parse HTTP input, call the service, and format HTTP output.

### Event Sourcing (Lightweight)
Every device lifecycle mutation emits a structured event to the `device_events` Redis Stream:
- `device_created`, `device_updated`, `device_decommissioned`
- `provisioning_requested`, `provisioning_approved`
- `firmware_rollout_started`

These events serve multiple purposes:
1. **Audit trail** -- all state changes are recorded as an append-only event log
2. **Cross-service integration** -- other services (coordination, data-persistence) can subscribe to the stream via consumer groups
3. **Edge replication** -- other edge gateways can replay the event stream to converge on a consistent device state
4. **Decoupling** -- producers and consumers are decoupled; new consumers can be added without modifying the device-manager

### Soft Delete
Devices are never physically deleted from the database. The `delete_device` operation sets the status to `DECOMMISSIONED`, preserving the full audit history. This is essential for:
- Regulatory compliance (device lifecycle records must be retained)
- Debugging (understanding why a device was removed from the fleet)
- Analytics (historical fleet composition)

### Optimistic Metrics
The `ACTIVE_DEVICES` Prometheus Gauge is updated synchronously with each lifecycle transition. On creation, the gauge label is initialised (`.inc(0)`). On status changes to/from ACTIVE, the gauge is incremented or decremented. This provides real-time fleet health visibility in Grafana dashboards.

---

## How It Works Together

```
HTTP Request
     |
     v
  [Router]  -- parses request, validates schemas, maps to service calls
     |
     v
  [Service Layer]  -- enforces business rules, orchestrates operations
     |
     +---> [Repository]  -- executes SQL via async SQLAlchemy session
     |          |
     |          v
     |     [PostgreSQL]  -- persistent device state
     |
     +---> [Redis Stream: device_events]  -- emits domain events
     |
     +---> [Prometheus Gauge: ACTIVE_DEVICES]  -- updates metrics
     |
     v
HTTP Response
```

### Request flow (example: create device)
1. Client sends `POST /api/v1/devices` with a JSON body
2. The `devices.py` router validates the body against `DeviceCreate`, extracts `redis_client` from `app.state`, and calls `device_service.create_device()`
3. The service layer calls `device_repo.create_device()` to insert the row
4. The service emits a `device_created` event to the `device_events` Redis Stream
5. The service initialises the `ACTIVE_DEVICES` gauge for the device type
6. The router converts the ORM instance to `DeviceResponse` and returns HTTP 201

### Event-driven replication
Other edge nodes subscribe to the `device_events` stream using Redis consumer groups. When a device is created, updated, or decommissioned on one gateway, the event is consumed by other gateways which replay the change against their local state. This provides eventual consistency across the edge cluster without requiring synchronous cross-node communication.

### Firmware rollout flow
1. Admin registers a firmware version via `POST /firmware`
2. Admin starts a rollout via `POST /firmware/rollout` specifying target devices
3. The service stores rollout state in Redis (24h TTL) and publishes a `firmware_rollout_started` event
4. Downstream services (coordination, device agents) consume the event and orchestrate the actual binary distribution
5. The rollout status can be polled via `GET /firmware/rollout/{rollout_id}` until the TTL expires
