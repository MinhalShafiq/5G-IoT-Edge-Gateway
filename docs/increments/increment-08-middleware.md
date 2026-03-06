# Increment 8 -- Middleware Layer

## Goal

Implement a full API gateway providing authentication (JWT and API key), rate limiting, structured request logging, correlation ID propagation, global error handling, and reverse proxying to internal microservices. All external traffic enters the system through this single entry point on port 8000.

---

## Files Created

### Shared Auth Module (`shared/shared/auth/`)

#### `jwt_handler.py`

**Purpose:** Reusable JWT token creation and verification used by the middleware and any other service that needs to validate tokens.

**Functions:**

##### `create_token(payload, secret, algorithm="HS256", expires_minutes=60) -> str`
Creates a signed JWT string using the PyJWT library.
1. Copies the payload dict (to avoid mutating the caller's data)
2. Adds `iat` (issued-at) and `exp` (expiration) claims based on the current UTC time plus `expires_minutes`
3. Encodes and signs with `jwt.encode(to_encode, secret, algorithm=algorithm)`
4. Returns the encoded JWT string

##### `decode_token(token, secret, algorithm="HS256") -> dict`
Decodes and validates a JWT token.
1. Calls `jwt.decode(token, secret, algorithms=[algorithm])`
2. On `ExpiredSignatureError`: raises `JWTError("Token has expired")`
3. On `InvalidTokenError`: raises `JWTError(f"Invalid token: {exc}")`
4. Returns the decoded payload dictionary on success

##### `JWTError`
Custom exception class wrapping PyJWT errors. Provides a clean abstraction so consumers do not need to import or handle `jwt.exceptions` directly.

---

### Middleware Service (`services/middleware/`)

The middleware service is the API gateway for the entire IoT Edge Gateway platform. It intercepts every HTTP request, applies a pipeline of middleware layers, authenticates the caller, enforces rate limits, logs the request/response pair, and proxies the request to the appropriate upstream service.

The service contains **17 files**.

---

### 1. `pyproject.toml`

**Purpose:** Package definition and dependencies.

**Dependencies:**
- `fastapi>=0.104.0` -- web framework
- `uvicorn[standard]>=0.24.0` -- ASGI server
- `httpx>=0.25.0` -- async HTTP client for reverse proxying
- `redis>=5.0.0` -- rate limiting and API key storage
- `PyJWT>=2.8.0` -- JWT token signing and verification
- `cryptography>=41.0.0` -- cryptographic primitives (required by PyJWT for certain algorithms)
- `pydantic-settings>=2.1.0` -- configuration
- `structlog>=23.2.0` -- structured logging
- `prometheus-client>=0.19.0` -- metrics
- `shared` -- shared library

---

### 2. `Dockerfile`

**Purpose:** Container image for the middleware service.

**Build stages:**
1. Base: `python:3.11-slim`
2. System deps: `gcc`
3. Shared library: copy and install
4. Service deps: copy `pyproject.toml` and install (separate layer from code for caching)
5. Service code: copy `middleware/` directory
6. Working directory: `/app/services/middleware`
7. Expose port `8000`
8. CMD: `uvicorn middleware.main:app --host 0.0.0.0 --port 8000`

**Note:** This Dockerfile uses a slightly different caching strategy than other services -- it installs dependencies from `pyproject.toml` in a separate layer before copying the source code, so dependency-only changes do not rebuild the code layer.

---

### 3. `middleware/config.py`

**Purpose:** Gateway-specific configuration.

**Fields extending `BaseServiceSettings`:**
- `service_name: str = "middleware"`
- `http_port: int = 8000`
- `rate_limit_requests: int = 100` -- base rate limit per client per window
- `rate_limit_window_seconds: int = 60` -- sliding window duration (1 minute)
- `upstream_device_manager: str = "http://device-manager:8002"` -- device CRUD proxy target
- `upstream_data_ingestion: str = "http://data-ingestion:8001"` -- telemetry proxy target
- `upstream_ml_inference: str = "http://ml-inference:8004"` -- inference proxy target
- `upstream_scheduler: str = "http://scheduler:8005"` -- scheduling proxy target
- `jwt_refresh_expiration_minutes: int = 10080` -- 7 days for refresh tokens

The `get_settings()` factory returns a `Settings` instance.

---

### 4. `middleware/main.py`

**Purpose:** FastAPI application entry point, middleware registration, and lifecycle management.

**Lifespan handler:**
- **Startup:**
  1. Configures structured logging
  2. Initialises `RedisClient` and stores on `app.state.redis`
  3. Stores `Settings` on `app.state.settings`
  4. Creates an `httpx.AsyncClient` for reverse proxying with:
     - 30-second request timeout, 5-second connection timeout
     - No redirect following (the gateway controls routing)
     - Connection pool: 200 max connections, 40 keepalive
  5. Stores the client on `app.state.http_client`
- **Shutdown:**
  1. Closes the httpx async client
  2. Closes the Redis client

**Middleware registration order:**

Starlette processes middleware in **reverse registration order** for requests (last added = outermost). The code registers them so that the request execution order is:

```
Request flow:   ErrorHandler -> CorrelationId -> RequestLogger -> Authentication -> RateLimiter -> Router
Response flow:  Router -> RateLimiter -> Authentication -> RequestLogger -> CorrelationId -> ErrorHandler
```

Registration order in code (innermost first):
1. `RateLimiterMiddleware` (innermost -- closest to router)
2. `AuthenticationMiddleware`
3. `RequestLoggerMiddleware`
4. `CorrelationIdMiddleware`
5. `ErrorHandlerMiddleware` (outermost -- first to see request, last to see response)

**Router registration:**
- `health.router` -- liveness, readiness, and dependency health checks
- `auth.router` -- authentication endpoints (login, refresh, API keys)
- `proxy.router` -- reverse proxy routes to upstream services

---

### 5. `middleware/routers/auth.py`

**Purpose:** Authentication endpoints for login, token refresh, and API key management.

**Schemas:**
- `LoginRequest` -- `username`, `password`
- `TokenResponse` -- `access_token`, `refresh_token`, `token_type="bearer"`
- `RefreshRequest` -- `refresh_token`
- `AccessTokenResponse` -- `access_token`, `token_type="bearer"`
- `ApiKeyCreateRequest` -- `name`, `role` (default "device")
- `ApiKeyResponse` -- `key_id`, `api_key`, `name`, `role`
- `MessageResponse` -- `message`

**Helper functions:**
- `_get_auth_service(request)` -- constructs an `AuthService` from `app.state` (Redis client and settings)
- `_require_admin(request)` -- FastAPI dependency that raises 403 if `request.state.user.role != "admin"`

**Endpoints:**

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | None | Authenticate with username/password. Returns JWT access and refresh tokens. Returns 401 on invalid credentials. |
| POST | `/auth/refresh` | None | Exchange a valid refresh token for a new access token. Validates the token type is "refresh". Returns 401 on invalid/expired token. |
| POST | `/auth/api-keys` | Admin | Create a new API key with a name and role. Stored in Redis. Returns 201 with the raw key (shown only once). |
| DELETE | `/auth/api-keys/{key_id}` | Admin | Revoke an API key by its key_id. Deletes both the key lookup and the index entry from Redis. Returns 404 if not found. |

---

### 6. `middleware/routers/proxy.py`

**Purpose:** Reverse proxy that forwards requests to internal microservices.

**Route mapping:**
| Public Path | Upstream Service | Target URL Pattern |
|-------------|------------------|--------------------|
| `/api/v1/devices/*` | device-manager:8002 | `http://device-manager:8002/api/v1/devices/{path}` |
| `/api/v1/telemetry/*` | data-ingestion:8001 | `http://data-ingestion:8001/api/v1/telemetry/{path}` |
| `/api/v1/inference/*` | ml-inference:8004 | `http://ml-inference:8004/api/v1/inference/{path}` |
| `/api/v1/scheduler/*` | scheduler:8005 | `http://scheduler:8005/api/v1/scheduler/{path}` |

Each route is defined as a catch-all with `{path:path}` and supports all HTTP methods (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`).

**Generic proxy function `_proxy(request, upstream_base, path)`:**
1. Constructs the target URL: `{upstream_base}/api/v1/{path}[?query_string]`
2. Forwards headers, filtering out hop-by-hop headers (`host`, `connection`, `keep-alive`, `proxy-authenticate`, `proxy-authorization`, `te`, `trailers`, `transfer-encoding`, `upgrade`)
3. Injects `X-Correlation-ID` from `request.state.correlation_id`
4. Builds `X-Forwarded-For` by appending the client IP to any existing value
5. Reads and forwards the request body
6. Sends the request via `httpx.AsyncClient.request()` (from `app.state.http_client`)
7. Returns the upstream response verbatim, filtering out response headers that would conflict with the gateway's own encoding (`content-encoding`, `content-length`, `transfer-encoding`)

The `ROUTE_MAP` dictionary provides a declarative mapping from path prefix to settings attribute name, though the routes themselves are defined as explicit `@router.api_route` decorators for type safety and OpenAPI documentation.

---

### 7. `middleware/routers/health.py`

**Purpose:** Health check endpoints with dependency verification.

Extends the standard shared health router with a deep health check:

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Standard liveness probe (from shared library) |
| GET | `/ready` | Standard readiness probe (from shared library) |
| GET | `/health/dependencies` | Deep health check verifying Redis and all upstream services |

**`/health/dependencies` implementation:**
1. **Redis check:** Calls `redis_client.ping()` and reports "ok" / "degraded" / "error"
2. **Upstream checks:** For each of the 4 upstream services (device-manager, data-ingestion, ml-inference, scheduler):
   - Sends `GET {upstream_url}/health` with a 3-second timeout
   - Reports "ok" (HTTP 200), "degraded" (non-200), or "unreachable" (connection error)
3. **Overall status:**
   - "ok" if all dependencies are "ok"
   - "degraded" if any dependency is "error" or "unreachable"

---

### 8. `middleware/middleware_layers/authentication.py`

**Purpose:** Enforce authentication on all non-excluded routes.

**Excluded paths (no auth required):**
- Exact matches: `/health`, `/ready`, `/health/dependencies`, `/auth/login`, `/docs`, `/redoc`, `/openapi.json`
- Prefix matches: `/health/*`, `/docs/*`

**Authentication flow:**
1. If the path is excluded, pass through to the next middleware
2. If no `Authorization` header is present, return 401 with "Missing Authorization header"
3. **Bearer JWT:** If the header starts with `Bearer `:
   - Extract the token string
   - Call `decode_token()` from the shared JWT handler with the app's `jwt_secret` and `jwt_algorithm`
   - On success: set `request.state.user = {"sub": ..., "role": ..., "auth_method": "jwt"}`
   - On `JWTError`: return 401 with "Invalid or expired JWT token"
4. **API Key:** If the header starts with `ApiKey `:
   - Extract the key string
   - Look up `apikey:{key}` in Redis
   - On hit: parse the JSON metadata and set `request.state.user = {"sub": name, "role": ..., "auth_method": "api_key", "key_id": ...}`
   - On miss or Redis error: return 401 with "Invalid API key"
5. If the header starts with neither prefix, return 401 with "Authorization header must start with 'Bearer' or 'ApiKey'"

**Design choice:** The middleware uses `BaseHTTPMiddleware` from Starlette, which wraps each request in a coroutine. This is acceptable for the gateway since request processing is I/O-bound (Redis lookups, upstream proxying), not CPU-bound.

---

### 9. `middleware/middleware_layers/rate_limiter.py`

**Purpose:** Sliding-window rate limiter backed by Redis.

**Algorithm:** Redis `INCR` + `EXPIRE` per client per time window.

**Rate limit calculation:**
- Base limit: `settings.rate_limit_requests` (default 100)
- Role multipliers:
  - `admin`: 5x (500 requests/minute)
  - `device`: 2x (200 requests/minute) -- devices generate steady automated traffic
  - `user`: 1x (100 requests/minute)
- Effective limit: `base_limit * role_multiplier`

**Key format:** `ratelimit:{client_id}:{window_number}`
- `client_id` is derived from the authenticated user subject (`user:{sub}`) or falls back to the client IP (`ip:{host}`)
- `window_number` is `int(time.time()) // window_seconds` -- this creates fixed-size time windows

**Flow:**
1. Skip rate limiting for health endpoints (`/health`, `/ready`, `/health/dependencies`)
2. Compute the Redis key and atomic `INCR` it
3. If the count is 1 (first request in this window), set an `EXPIRE` of `window + 1` seconds
4. If count exceeds the effective limit:
   - Calculate `Retry-After` in seconds until the window resets
   - Return HTTP 429 with JSON body `{"detail": "Rate limit exceeded", "retry_after_seconds": N}`
   - Include response headers: `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` (0), `X-RateLimit-Reset` (Unix timestamp)
5. If within limits, proceed to the next middleware and attach rate limit headers to the response:
   - `X-RateLimit-Limit` -- the effective limit
   - `X-RateLimit-Remaining` -- requests remaining in this window
   - `X-RateLimit-Reset` -- Unix timestamp when the window resets

**Fail-open behaviour:** If Redis is unavailable (connection error, timeout), the rate limiter allows the request through and logs a warning. This prevents Redis outages from causing total API downtime, at the cost of temporarily losing rate limiting protection.

---

### 10. `middleware/middleware_layers/request_logger.py`

**Purpose:** Log every HTTP request/response and emit Prometheus metrics.

**Prometheus metrics:**
- `gateway_http_requests_total` (Counter) -- labelled by `method`, `path`, `status_code`
- `gateway_http_request_latency_seconds` (Histogram) -- labelled by `method`, `path`; buckets from 5ms to 10s

**Flow:**
1. Record start time with `time.perf_counter()`
2. Call the next middleware/router
3. Compute latency in seconds and milliseconds
4. Normalise the path for Prometheus labels (see below)
5. Increment the request counter with method, normalised path, and status code
6. Observe the latency in the histogram
7. Log a structured event with: method, original path, status_code, latency_ms, correlation_id, client_ip, user_agent

**Path normalisation -- `_normalise_path(path)`:**
Prevents Prometheus label cardinality explosion by replacing dynamic path segments (UUIDs, numeric IDs) with `:id`. The heuristic checks if a segment is longer than 20 characters or contains both digits and hyphens (common UUID pattern). For example:
- `/api/v1/devices/550e8400-e29b-41d4-a716-446655440000` becomes `/api/v1/devices/:id`
- `/api/v1/tasks` stays unchanged

---

### 11. `middleware/middleware_layers/correlation_id.py`

**Purpose:** Ensure every request/response carries a unique correlation ID for distributed tracing.

**Header name:** `X-Correlation-ID`

**Flow:**
1. Read the `X-Correlation-ID` header from the incoming request
2. If not present, generate a new UUID-4
3. Set `request.state.correlation_id` for downstream access (used by the proxy, logger, and error handler)
4. Bind the correlation_id to the structlog context variables (`structlog.contextvars.bind_contextvars`) so every log entry in the request lifecycle includes it
5. Call the next middleware/router
6. In the `finally` block: unbind the structlog context variable to prevent leaking between requests
7. Set the `X-Correlation-ID` header on the response

**Design importance:** This is one of the outermost middleware layers (second only to ErrorHandler), ensuring that correlation IDs are available for all logging and error responses throughout the request lifecycle.

---

### 12. `middleware/middleware_layers/error_handler.py`

**Purpose:** Catch all unhandled exceptions and return structured JSON error responses.

**Exception-to-status mapping:**
| Exception Type | HTTP Status |
|---------------|-------------|
| `ValueError` | 400 Bad Request |
| `KeyError` | 400 Bad Request |
| `PermissionError` | 403 Forbidden |
| `FileNotFoundError` | 404 Not Found |
| `NotImplementedError` | 501 Not Implemented |
| `TimeoutError` | 504 Gateway Timeout |
| `RequestValidationError` | 422 Unprocessable Entity |
| Any other exception | 500 Internal Server Error |

**Flow:**
1. Try to call the next middleware/router
2. On `RequestValidationError`: return 422 with the validation error details
3. On any other exception:
   - Look up the status code from `EXCEPTION_STATUS_MAP` (default 500)
   - Log the full error with: exception type, message, correlation_id, path, method, and full traceback
   - For status < 500: include the exception message in the response (safe for client-side errors)
   - For status >= 500: return generic "Internal server error" message (avoid leaking implementation details)

**Response format:**
```json
{
    "error": "ValueError",
    "message": "Cannot update a decommissioned device.",
    "correlation_id": "abc-123-def",
    "status_code": 400
}
```

This is the outermost middleware layer, ensuring that no exception ever results in an HTML error page or an unstructured response body.

---

### 13. `middleware/services/auth_service.py`

**Purpose:** Authentication business logic encapsulating user verification, token lifecycle, and API key CRUD.

**Hardcoded dev users:**
| Username | Password (SHA-256 hash) | Role |
|----------|------------------------|------|
| `admin` | hash of "admin" | `admin` |
| `operator` | hash of "operator" | `user` |
| `device-service` | hash of "device-service" | `device` |

**Note:** This is explicitly for local development. The module docstring states that production deployments should replace `authenticate_user` with a proper identity store lookup.

**AuthService class:**

*Constructor:* Takes a `RedisClient` and a `Settings` instance.

##### `authenticate_user(username, password) -> dict | None`
1. Looks up the username in `_DEV_USERS`
2. Hashes the provided password with SHA-256
3. Compares using `secrets.compare_digest()` (constant-time comparison to prevent timing attacks)
4. Returns `{"sub": username, "role": role}` on success, `None` on failure

##### `create_access_token(user) -> str`
Creates a short-lived JWT with claims:
- `sub` -- username
- `role` -- user role
- `type` -- "access"
- Expiration: `settings.jwt_expiration_minutes` (default 60 minutes)

##### `create_refresh_token(user) -> str`
Creates a long-lived JWT with:
- Same claims as access token but `type: "refresh"`
- Expiration: `settings.jwt_refresh_expiration_minutes` (default 7 days / 10080 minutes)

##### `verify_token(token) -> dict`
Decodes and validates a JWT using the shared `decode_token()`. Raises `JWTError` on failure.

##### `create_api_key(name, role="device") -> dict`
1. Generates a `key_id`: 16-character hex string via `secrets.token_hex(8)`
2. Generates a `raw_key`: 43-character URL-safe string via `secrets.token_urlsafe(32)`
3. Stores metadata in Redis:
   - `apikey:{raw_key}` -> JSON `{"key_id": ..., "name": ..., "role": ...}` (for O(1) lookup during authentication)
   - `apikey_id:{key_id}` -> `raw_key` (reverse index for revocation by key_id)
4. Returns `{"key_id": ..., "api_key": raw_key, "name": ..., "role": ...}`

**Important:** The raw API key is returned exactly once. Only the hash/lookup key is stored. Clients must save it immediately.

##### `verify_api_key(api_key) -> dict | None`
O(1) Redis lookup: `GET apikey:{api_key}`. Returns parsed JSON metadata or `None`.

##### `revoke_api_key(key_id) -> bool`
1. Looks up the raw key via the reverse index: `GET apikey_id:{key_id}`
2. If found, deletes both entries: `apikey:{raw_key}` and `apikey_id:{key_id}`
3. Returns `True` if deleted, `False` if the key_id was not found

---

### 14-17. `__init__.py` files

Package markers for:
- `middleware/__init__.py`
- `middleware/routers/__init__.py`
- `middleware/services/__init__.py`
- `middleware/middleware_layers/__init__.py`

---

## Middleware Execution Order

The middleware pipeline processes requests and responses in a specific order. Understanding this order is critical for debugging and for ensuring that features like correlation IDs are available to all downstream layers.

### Request path (top to bottom):

```
Incoming HTTP Request
       |
       v
1. [ErrorHandlerMiddleware]
       |  Wraps everything in try/catch
       v
2. [CorrelationIdMiddleware]
       |  Reads or generates X-Correlation-ID
       |  Binds to structlog context
       v
3. [RequestLoggerMiddleware]
       |  Starts latency timer
       v
4. [AuthenticationMiddleware]
       |  Validates Bearer JWT or ApiKey
       |  Sets request.state.user
       v
5. [RateLimiterMiddleware]
       |  Checks Redis counter against limit
       |  Returns 429 if exceeded
       v
6. [Router]
       |  auth.py / proxy.py / health.py
       |  Handles the actual request
```

### Response path (bottom to top):

```
Router Response
       |
       v
5. [RateLimiterMiddleware]
       |  Attaches X-RateLimit-* headers
       v
4. [AuthenticationMiddleware]
       |  (pass-through on response)
       v
3. [RequestLoggerMiddleware]
       |  Stops timer, logs request, updates Prometheus
       v
2. [CorrelationIdMiddleware]
       |  Sets X-Correlation-ID on response header
       |  Unbinds structlog context
       v
1. [ErrorHandlerMiddleware]
       |  (pass-through if no exception)
       v
HTTP Response to Client
```

### Why this order matters:

1. **ErrorHandler is outermost** -- it catches exceptions from any layer, including authentication failures or rate limiter Redis errors, and returns structured JSON.

2. **CorrelationId is second** -- it runs before logging and auth so that every log entry and error response includes the correlation ID. It runs after ErrorHandler so that even error responses get the correlation header.

3. **RequestLogger is third** -- it captures the full request lifecycle including auth and rate limiting time in the latency measurement.

4. **Authentication is fourth** -- it runs before rate limiting so that the rate limiter can use the authenticated user identity (rather than just IP) for per-user limits.

5. **RateLimiter is innermost** -- it runs after authentication so it can apply role-based multipliers. Being closest to the router means rate-limited requests never reach the proxy layer, saving upstream service load.

---

## Design Patterns

### Middleware Pipeline (Chain of Responsibility)
Each middleware layer is a Starlette `BaseHTTPMiddleware` that can inspect/modify the request, delegate to the next handler via `call_next()`, and inspect/modify the response. This is a classic Chain of Responsibility pattern where each handler decides whether to pass the request along or short-circuit (e.g. returning 401 or 429).

### Reverse Proxy (Gateway Pattern)
The proxy router implements the API Gateway pattern. External clients interact with a single entry point (port 8000) and the gateway handles routing, authentication, and cross-cutting concerns. Internal services are not exposed to the public network.

### Token-Based Authentication (Bearer + API Key)
Two authentication schemes are supported:
- **Bearer JWT** for interactive users and service-to-service calls with short-lived tokens
- **API Key** for long-lived machine credentials (IoT devices, automation scripts)

Both schemes result in the same `request.state.user` structure, making downstream code agnostic to the auth method.

### Fail-Open Rate Limiting
The rate limiter intentionally fails open when Redis is unavailable. This is a deliberate trade-off: availability is preferred over protection during infrastructure failures. The failure is logged so operators can respond, but API traffic continues flowing.

### Sliding Window Counter
The rate limiting uses a fixed-window counter approximation (not a true sliding window) with Redis `INCR`/`EXPIRE`. Each window is `rate_limit_window_seconds` wide, keyed by `floor(time / window)`. This is simple, fast (single Redis command), and memory-efficient.

### Structured Error Responses
Every error -- whether from a validation failure, business rule violation, or unhandled exception -- returns the same JSON structure: `{error, message, correlation_id, status_code}`. This makes it easy for clients to parse errors programmatically and for operators to trace issues using the correlation ID.
