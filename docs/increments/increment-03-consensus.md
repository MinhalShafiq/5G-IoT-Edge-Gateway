# Increment 3 -- Time & Consensus

## Goal

Edge nodes discover each other, elect a leader, and synchronize configuration across the cluster. This increment introduces the Coordination service -- a standalone microservice that provides distributed consensus primitives for a fleet of edge gateway nodes. It implements Redis-based leader election, SWIM-like gossip protocol for membership probing, heartbeat-driven failure detection, distributed configuration synchronization via Redis Pub/Sub, OTA (over-the-air) firmware rollout coordination, and causal ordering via version vectors.

## Data Flow

```
                          Redis (Leader Lock)
                               ^  |
                     try_acquire  | get_leader
                               |  v
Node A (Coordination) <--- Gossip (Redis Pub/Sub) ---> Node B (Coordination)
         |                                                       |
         |--- Heartbeat check (scan all nodes) ---               |
         |                                                       |
         +--- Config write (leader) --> Redis --> Pub/Sub ------>+
                                                    |
                                              Config update
                                              applied locally
```

1. **Leader Election**: all coordination nodes compete for a single Redis key (`iot-gateway:leader`) using `SET NX EX`. The winner is the leader; all others are followers. The leader periodically renews the TTL.
2. **Gossip Protocol**: each node selects random alive peers every 2 seconds and probes them via Redis Pub/Sub. Failed probes mark the target as `SUSPECT`.
3. **Heartbeat Service**: a background loop scans all known nodes; if a node's heartbeat exceeds the timeout, it transitions from `ALIVE` to `SUSPECT`, and from `SUSPECT` to `DEAD` at 2x the timeout.
4. **Config Sync**: the leader writes config changes to Redis and publishes notifications to a `config_updates` Pub/Sub channel. Followers subscribe and apply updates. On startup, every node pulls the full config from Redis.
5. **OTA Coordinator**: the leader orchestrates firmware rollouts using either a rolling update (one node at a time with health checks) or a canary strategy (update one node, observe, then proceed).

---

## Files Created

### Coordination Service (`services/coordination/`)

The coordination service is a standalone microservice with 17 files organized into gRPC server, domain services, and state management layers.

#### `pyproject.toml`
Package metadata for `coordination`. Dependencies: `grpcio`, `grpcio-tools`, `redis`, `pydantic-settings`, `structlog`, `prometheus-client`, `fastapi`, `uvicorn[standard]`, and the `shared` library. Dev dependencies include `pytest`, `pytest-asyncio`, `pytest-cov`, and `httpx`.

#### `Dockerfile`
Based on `python:3.11-slim`. Installs `gcc`, copies and pip-installs the `shared` library, then the coordination service. Exposes ports 50052 (gRPC) and 8003 (HTTP health). Runs `python -m coordination.main`.

#### `coordination/__init__.py`
Package marker.

#### `coordination/config.py`
**`Settings`** extends `BaseServiceSettings` with coordination-specific fields:

| Field | Default | Purpose |
|---|---|---|
| `service_name` | `"coordination"` | Service identifier |
| `node_id` | `""` | Node identity (auto-generated from hostname if empty) |
| `node_address` | `"localhost:50052"` | gRPC advertised address |
| `grpc_port` | `50052` | gRPC listen port |
| `http_port` | `8003` | HTTP health endpoint port |
| `heartbeat_interval_seconds` | `2.0` | How often to check heartbeats |
| `heartbeat_timeout_seconds` | `10.0` | Seconds of silence before a node is suspected |
| `leader_lock_ttl_seconds` | `15` | TTL on the Redis leader lock key |
| `gossip_interval_seconds` | `2.0` | Seconds between gossip rounds |
| `gossip_fanout` | `3` | Number of random peers to probe per gossip round |
| `seed_nodes` | `[]` | Initial cluster members for bootstrap |

#### `coordination/main.py`
Dual-server entry point that orchestrates the entire coordination service:

**Initialization:**
1. Loads `Settings` and configures structured logging.
2. Generates a `node_id` from `hostname + uuid4()[:8]` if not explicitly set.
3. Publishes service metadata to the Prometheus `SERVICE_INFO` gauge.
4. Creates a `RedisClient` and verifies connectivity with `ping()`.
5. Initializes the in-memory `ClusterState` and registers the local node as `ALIVE`.

**Domain services construction:**
6. Creates `LeaderElection` (with Redis client and TTL).
7. Creates `ConfigSync` (with Redis client, cluster state, and node ID).
8. Creates `GossipProtocol` (with cluster state, node ID, interval, and fanout).
9. Creates `HeartbeatService` (with cluster state, node ID, interval, and timeout).

**Server setup:**
10. Creates a `GrpcServer` with the cluster state, leader election, and config sync instances.
11. Sets up a shutdown event (`asyncio.Event`) with `SIGTERM`/`SIGINT` handlers (Windows-compatible fallback).
12. Builds a minimal FastAPI health app using `create_health_router()`.

**Concurrent execution:**
13. Launches four background tasks as `asyncio.Task` instances:
    - `leader_election.run_election_loop()` -- periodic leader lock acquisition/renewal.
    - `gossip.run()` -- periodic peer probing.
    - `heartbeat_service.run()` -- periodic timeout checking.
    - `config_sync.run_sync_loop()` -- Pub/Sub listener for config updates.
14. Starts the gRPC server and the HTTP (uvicorn) server concurrently.
15. Blocks on `shutdown_event.wait()`.

**Shutdown sequence:**
16. Releases the leader lock if held.
17. Cancels all background tasks and awaits their completion.
18. Stops the gRPC server.
19. Signals uvicorn to exit.
20. Closes the Redis client.

#### `coordination/grpc_server/__init__.py`
Package marker.

#### `coordination/grpc_server/server.py`
**`GrpcServer`** -- manages the gRPC server lifecycle:
- **`__init__(port, cluster_state, leader_election, config_sync)`** -- stores dependencies for the servicer.
- **`start()`** -- creates a `grpc.aio.server()`, instantiates `CoordinationServicer` with the cluster state and domain services, binds to `[::]:{port}`, and starts the server. The proto stub registration is a placeholder (the servicer is stored on `self._servicer` for testing).
- **`stop(grace=5.0)`** -- gracefully stops the server.
- **`wait_for_termination()`** -- blocks until the server exits.

#### `coordination/grpc_server/coordination_servicer.py`
**`CoordinationServicer`** -- implements the five gRPC RPCs defined in `coordination.proto`:

**`Heartbeat(request, context)`**:
- Extracts `node_id` and `node_address` from the request.
- If the node is unknown, registers it as `ALIVE` in the cluster state.
- If the node is known, refreshes its heartbeat timestamp; if it was `SUSPECT` or `JOINING`, transitions it back to `ALIVE`.
- Returns `{"leader_id": ..., "cluster_version": ..., "ack": True}`.

**`JoinCluster(request, context)`**:
- Registers a new `ClusterNode` with status `ALIVE` and the provided `capabilities`.
- Returns the full member list (with each node's ID, address, status, and leader flag) plus the current shared config.

**`LeaveCluster(request, context)`**:
- Marks the specified node as `LEAVING` so it is excluded from future gossip rounds and leader election.
- Returns `{"ack": True}` on success or `{"ack": False, "error": "unknown node"}` if the node is not found.

**`GetClusterState(request, context)`**:
- Returns a full snapshot: all nodes with their status, capabilities, and metadata; the leader ID; the cluster version; and the shared config dictionary.

**`SyncConfig(request_iterator, context)`** -- bidirectional streaming:
- For each incoming config entry, compares the incoming version with the local version.
- If the local version is newer, yields the local value back to the caller.
- If the incoming version is newer, applies it locally via `config_sync.set_local()`.
- This allows two nodes to converge their config state in a single streaming exchange.

#### `coordination/services/__init__.py`
Package marker.

#### `coordination/services/leader_election.py`
**`LeaderElection`** -- Redis-based distributed leader election:

**Lock key**: `iot-gateway:leader`

**`try_acquire()`**:
1. Attempts `SET NX EX` (set-if-not-exists with TTL) on the lock key with `self._node_id` as the value.
2. If successful (`result=True`), this node becomes the leader. Logs `"leader_elected"` on first acquisition.
3. If the key already exists, reads its current value:
   - If it matches `self._node_id`, renews the TTL (the node is already leader).
   - Otherwise, another node holds the lock; this node is a follower.
4. Returns `True` if this node is the leader, `False` otherwise.

**`get_leader()`** -- reads the lock key and returns the leader's `node_id` (or `None`).

**`release()`** -- deletes the lock key only if the current value matches `self._node_id` (prevents releasing another node's lock).

**`run_election_loop(interval, shutdown_event)`** -- background loop that calls `try_acquire()` every `interval` seconds until `shutdown_event` is set. Uses `asyncio.wait_for()` with a timeout for efficient sleep that can be interrupted by the shutdown signal.

**Failure semantics**: if the leader crashes, the TTL (default 15 seconds) ensures the lock expires, allowing another node to acquire it. There is no split-brain risk because `SET NX` is atomic in Redis.

#### `coordination/services/gossip.py`
**`GossipProtocol`** -- SWIM-inspired membership probing:

**Architecture**: uses Redis Pub/Sub channel `iot-gateway:gossip` as the signalling transport (in production, this would be replaced with direct gRPC or UDP probes).

**`run(shutdown_event)`**:
1. Optionally starts a listener task (`_listen_for_pings`) that subscribes to the gossip channel.
2. Enters a loop calling `_gossip_round()` every `gossip_interval_seconds`.

**`_gossip_round()`**:
1. Gets all alive peers (excluding self) from the cluster state.
2. Randomly samples up to `gossip_fanout` peers.
3. For each target, calls `_ping_peer()`:
   - Publishes a JSON ping message to the gossip channel containing `from_node_id`, `target_node_id`, `sequence`, and `timestamp`.
   - If the publish fails (Redis error), returns `False`.
4. If a ping fails, calls `cluster_state.mark_suspect(peer.node_id)`.

**`_listen_for_pings(shutdown_event)`**:
1. Subscribes to the `iot-gateway:gossip` Pub/Sub channel.
2. For each incoming message, checks if the `target_node_id` matches self.
3. If it does, calls `handle_ping()` which updates the sender's heartbeat timestamp in the cluster state.

**`set_redis(redis_client)`** -- allows late-binding a Redis client after construction.

#### `coordination/services/config_sync.py`
**`ConfigSync`** -- distributed configuration replication:

**Redis key scheme**: config values are stored under `iot-gateway:config:{key}`, with corresponding version counters at `iot-gateway:config:version:{key}`.

**Write path (leader only):**
- **`set_config(key, value)`** -- atomically increments the version counter (`INCR`), stores the value, updates the local cache and cluster state, and publishes a JSON notification to the `config_updates` Pub/Sub channel containing the key, value, version, and updater node ID.

**Read path:**
- **`get_config(key)`** -- reads from Redis (source of truth).
- **`get_local(key)`** -- returns the locally cached `(value, version)` tuple (or `("", 0)` if unknown).
- **`set_local(key, value, version)`** -- updates the local cache directly (used by the `SyncConfig` RPC).

**Full sync:**
- **`sync_from_leader()`** -- scans all Redis keys matching `iot-gateway:config:*`, fetches each value and its version, and populates the local cache. Used on startup to quickly converge.

**Background subscriber:**
- **`run_sync_loop(shutdown_event)`**:
  1. Performs an initial full sync from Redis.
  2. Subscribes to the `config_updates` Pub/Sub channel.
  3. For each incoming notification: parses the JSON, compares the incoming version with the local version, and applies the update only if the incoming version is strictly newer. This prevents stale updates from overwriting newer values.

#### `coordination/services/heartbeat.py`
**`HeartbeatService`** -- node liveness monitoring:

**`run(shutdown_event)`** -- periodically calls `_check_timeouts()` every `interval` seconds.

**`_check_timeouts()`** -- scans all nodes in the cluster state:
- Skips the local node (never timeout ourselves) and nodes already `DEAD` or `LEAVING`.
- Computes `elapsed = now - node.last_heartbeat` (handling timezone-aware datetime comparison).
- If `elapsed > timeout * 2`: transitions the node to `DEAD` and logs `"node_declared_dead"`.
- If `elapsed > timeout`: transitions the node to `SUSPECT` and logs `"node_suspected"`.

**State transitions**:
```
ALIVE ---(timeout exceeded)---> SUSPECT ---(2x timeout exceeded)---> DEAD
SUSPECT ---(heartbeat received)---> ALIVE  (via gossip or direct heartbeat)
```

#### `coordination/services/ota_coordinator.py`
**`OTACoordinator`** -- firmware rollout coordination with two strategies:

**`start_rolling_update(firmware_version, nodes, health_timeout)`**:
1. Targets all alive peers (or a specified subset).
2. For each node sequentially:
   a. Calls `_update_node()` to set `pending_firmware` in the node's metadata and record the target version in Redis.
   b. Calls `_wait_for_health()` which polls the node's cluster status every 2 seconds for up to `health_timeout` seconds (default 60s), waiting for it to report `ALIVE`.
   c. If the health check passes, marks the node as `"updated"`.
   d. If the health check fails, calls `_rollback_node()` (clears the pending firmware metadata and Redis key) and raises a `RuntimeError`, halting the rollout.
3. Records OTA events to a Redis list (`iot-gateway:ota:events`) for audit.

**`start_canary_update(firmware_version, canary_watch_seconds, health_timeout)`**:
1. **Phase 1**: selects the first alive peer as the canary and updates it.
2. **Phase 2**: waits for `canary_watch_seconds` (default 120s), then re-checks the canary's health.
3. **Phase 3**: if the canary is healthy, proceeds to update all remaining nodes using the same sequential health-check pattern.
4. If the canary fails at any point, rolls it back and raises a `RuntimeError`.

**Internal helpers**:
- `_update_node(node, firmware_version)` -- placeholder that sets metadata and a Redis key; in production, would send a gRPC command to the node's management agent.
- `_wait_for_health(node, timeout, poll_interval)` -- polls the cluster state for the node's status.
- `_rollback_node(node)` -- clears pending firmware metadata and Redis keys.
- `_record_ota_event(firmware_version, event, node_ids)` -- appends a JSON audit record to `iot-gateway:ota:events` in Redis.

#### `coordination/state/__init__.py`
Package marker.

#### `coordination/state/cluster_state.py`
**Thread-safe in-memory cluster state** -- every coordination node maintains its own `ClusterState` instance, updated via heartbeats, gossip, and config sync.

**`NodeStatus`** (StrEnum): `JOINING`, `ALIVE`, `SUSPECT`, `DEAD`, `LEAVING`.

**`ClusterNode`** (dataclass): `node_id`, `node_address`, `status` (default `JOINING`), `is_leader` (bool), `last_heartbeat` (UTC datetime), `capabilities` (list), `metadata` (dict).

**`ClusterState`** class:
- Thread-safety via `threading.Lock` -- guards all mutations to `_nodes`, `_cluster_version`, and `_config`.
- `_cluster_version` -- monotonically increasing counter bumped on every state mutation.

**Node management methods:**
- `add_node(node)` -- inserts or replaces a node; bumps version.
- `remove_node(node_id)` -- deletes a node; bumps version.
- `get_node(node_id)` -- returns a node or `None`.
- `update_node_heartbeat(node_id)` -- refreshes `last_heartbeat` to `now(UTC)` and transitions `JOINING`/`SUSPECT` nodes to `ALIVE`.
- `mark_suspect(node_id)` -- transitions to `SUSPECT` (unless already `DEAD` or `LEAVING`).
- `mark_dead(node_id)` -- transitions to `DEAD` and clears the `is_leader` flag.
- `mark_alive(node_id)` -- transitions back to `ALIVE` with a fresh heartbeat.
- `mark_leaving(node_id)` -- transitions to `LEAVING`.

**Query methods:**
- `get_alive_peers(exclude)` -- returns all `ALIVE` nodes, optionally excluding one.
- `get_all_nodes()` -- returns a snapshot of all known nodes.
- `get_leader()` -- returns the node with `is_leader=True`, or `None`.
- `set_leader(node_id)` -- clears the leader flag on all nodes and sets it on the specified node.

**Config methods:**
- `set_config(key, value)` / `get_config(key)` -- key-value store for cluster-wide configuration.
- `config` property -- returns a copy of the config dict.

#### `coordination/state/version_vector.py`
**`VersionVector`** -- a vector clock implementation for causal ordering of distributed updates:

**State**: `_clock: dict[str, int]` -- maps node IDs to monotonically increasing counters.

**Mutation methods:**
- `increment(node_id)` -- bumps the counter for `node_id` by 1 and returns the new value.
- `merge(other)` -- takes the element-wise maximum of both vectors, used when receiving updates from another node.

**Comparison methods:**
- `dominates(other)` -- returns `True` if this vector causally happened-after `other` (every component >= other's, and at least one strictly greater).
- `concurrent_with(other)` -- returns `True` if neither vector dominates the other, indicating independent, potentially conflicting updates that need application-level resolution.

**Serialization:**
- `to_dict()` -- returns a plain `dict[str, int]`.
- `from_dict(data)` -- class method that reconstructs a `VersionVector` from a dict.

**Equality**: two vectors are equal if every component matches (missing keys are treated as 0).

---

## How It Works Together

### Leader Election Flow

1. Every coordination node runs a `LeaderElection` background loop that calls `try_acquire()` every `heartbeat_interval_seconds` (default 2s).
2. The first node to successfully `SET NX EX` the `iot-gateway:leader` key becomes the leader.
3. The leader renews the key's TTL on each loop iteration to prevent expiry.
4. If the leader crashes, the key expires after `leader_lock_ttl_seconds` (default 15s), and another node acquires it on its next attempt.
5. The leader is responsible for writing config changes (via `ConfigSync.set_config()`) and coordinating OTA rollouts.

### Gossip Protocol

1. Every `gossip_interval_seconds` (default 2s), each node selects up to `gossip_fanout` (default 3) random alive peers.
2. For each peer, a JSON ping is published to the `iot-gateway:gossip` Redis Pub/Sub channel.
3. The target node's listener picks up the ping and refreshes the sender's heartbeat timestamp.
4. If a ping fails (Redis error), the target is immediately marked `SUSPECT`.
5. Over time, the `HeartbeatService` promotes stale `SUSPECT` nodes to `DEAD`.

### Configuration Propagation

1. A client (or the leader itself) calls `ConfigSync.set_config(key, value)`.
2. The value is written to Redis, the version counter is atomically incremented, and a notification is published to the `config_updates` Pub/Sub channel.
3. Every follower node's `run_sync_loop()` receives the notification, compares versions, and applies the update to its local cache if the incoming version is newer.
4. On startup, each node calls `sync_from_leader()` to pull the full config from Redis, ensuring convergence even if Pub/Sub messages were missed.

### OTA Rollout

1. The leader calls either `start_rolling_update()` or `start_canary_update()` on the `OTACoordinator`.
2. For a **rolling update**: nodes are updated one at a time. After each update, the coordinator waits up to 60 seconds for the node to report `ALIVE` via its heartbeat. If the health check fails, the node is rolled back and the entire rollout halts.
3. For a **canary update**: one node is updated first; the coordinator waits for a configurable observation period (default 120s), re-checks health, and only then proceeds to update the remaining nodes.
4. All OTA events (start, success, failure, rollback) are recorded as JSON entries in a Redis list for audit and observability.

### Version Vectors

The `VersionVector` class provides the theoretical foundation for detecting causal ordering and conflicts in distributed config updates. When two nodes independently update the same config key, comparing their version vectors reveals whether one update supersedes the other or the updates are concurrent (requiring application-level conflict resolution). This mechanism is available for use by the `SyncConfig` bidirectional streaming RPC.
