"""Authentication business logic.

Encapsulates user authentication, JWT token creation/verification, and
API key management (CRUD in Redis).  The hardcoded admin user is provided
for local development; production deployments should replace
``authenticate_user`` with a proper identity store lookup.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

import structlog

from shared.auth.jwt_handler import create_token, decode_token, JWTError
from shared.database.redis_client import RedisClient

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded users for development (username -> {password_hash, role})
# In production, replace with a database or external IdP lookup.
# ---------------------------------------------------------------------------
_DEV_USERS: dict[str, dict[str, str]] = {
    "admin": {
        "password_hash": hashlib.sha256(b"admin").hexdigest(),
        "role": "admin",
    },
    "operator": {
        "password_hash": hashlib.sha256(b"operator").hexdigest(),
        "role": "user",
    },
    "device-service": {
        "password_hash": hashlib.sha256(b"device-service").hexdigest(),
        "role": "device",
    },
}

# Redis key prefixes
_API_KEY_PREFIX = "apikey:"
_API_KEY_INDEX_PREFIX = "apikey_id:"


class AuthService:
    """Handles authentication, token lifecycle, and API key management."""

    def __init__(self, redis: RedisClient, settings: Any) -> None:
        self._redis = redis
        self._settings = settings

    # ------------------------------------------------------------------
    # User authentication
    # ------------------------------------------------------------------

    async def authenticate_user(
        self, username: str, password: str
    ) -> dict[str, str] | None:
        """Validate username/password and return a user dict, or None.

        For development, checks against a hardcoded user table.
        """
        user_record = _DEV_USERS.get(username)
        if user_record is None:
            return None

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        if not secrets.compare_digest(password_hash, user_record["password_hash"]):
            return None

        logger.info("user_authenticated", username=username, role=user_record["role"])
        return {"sub": username, "role": user_record["role"]}

    # ------------------------------------------------------------------
    # JWT tokens
    # ------------------------------------------------------------------

    def create_access_token(self, user: dict[str, str]) -> str:
        """Create a short-lived access JWT for the given user."""
        payload = {
            "sub": user["sub"],
            "role": user.get("role", "user"),
            "type": "access",
        }
        return create_token(
            payload,
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
            expires_minutes=self._settings.jwt_expiration_minutes,
        )

    def create_refresh_token(self, user: dict[str, str]) -> str:
        """Create a longer-lived refresh JWT for the given user."""
        payload = {
            "sub": user["sub"],
            "role": user.get("role", "user"),
            "type": "refresh",
        }
        return create_token(
            payload,
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
            expires_minutes=self._settings.jwt_refresh_expiration_minutes,
        )

    def verify_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a JWT token.

        Raises:
            JWTError: If the token is invalid or expired.
        """
        return decode_token(
            token,
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
        )

    # ------------------------------------------------------------------
    # API key management
    # ------------------------------------------------------------------

    async def create_api_key(self, name: str, role: str = "device") -> dict[str, str]:
        """Generate a new API key, store metadata in Redis, and return it.

        The raw key is returned exactly once; only the hash is stored for
        future verification.

        Returns:
            dict with key_id, api_key, name, role.
        """
        key_id = secrets.token_hex(8)       # 16-char hex identifier
        raw_key = secrets.token_urlsafe(32)  # 43-char URL-safe key

        meta = json.dumps({
            "key_id": key_id,
            "name": name,
            "role": role,
        })

        # Store by raw key for O(1) lookup during authentication
        await self._redis.set(f"{_API_KEY_PREFIX}{raw_key}", meta)
        # Store an index entry so we can revoke by key_id
        await self._redis.set(f"{_API_KEY_INDEX_PREFIX}{key_id}", raw_key)

        logger.info("api_key_created", key_id=key_id, name=name, role=role)

        return {
            "key_id": key_id,
            "api_key": raw_key,
            "name": name,
            "role": role,
        }

    async def verify_api_key(self, api_key: str) -> dict[str, Any] | None:
        """Look up an API key in Redis and return its metadata, or None."""
        data = await self._redis.get(f"{_API_KEY_PREFIX}{api_key}")
        if data is None:
            return None
        return json.loads(data)

    async def revoke_api_key(self, key_id: str) -> bool:
        """Delete an API key by its key_id.

        Returns True if the key existed and was deleted.
        """
        # Look up the raw key via the index
        raw_key = await self._redis.get(f"{_API_KEY_INDEX_PREFIX}{key_id}")
        if raw_key is None:
            return False

        # Delete both entries
        await self._redis.delete(
            f"{_API_KEY_PREFIX}{raw_key}",
            f"{_API_KEY_INDEX_PREFIX}{key_id}",
        )

        logger.info("api_key_revoked", key_id=key_id)
        return True
