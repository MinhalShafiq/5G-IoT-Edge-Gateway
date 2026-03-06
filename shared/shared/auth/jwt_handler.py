"""JWT token creation and verification using PyJWT.

Provides reusable helpers for creating and decoding JSON Web Tokens
used across all services that need authentication.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt


class JWTError(Exception):
    """Raised when JWT operations fail."""


def create_token(
    payload: dict[str, Any],
    secret: str,
    algorithm: str = "HS256",
    expires_minutes: int = 60,
) -> str:
    """Create a signed JWT token.

    Args:
        payload: Claims to include in the token (e.g. sub, role).
        secret: Secret key used for signing.
        algorithm: Signing algorithm (default HS256).
        expires_minutes: Token lifetime in minutes.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    to_encode = payload.copy()
    to_encode.update(
        {
            "iat": now,
            "exp": now + timedelta(minutes=expires_minutes),
        }
    )
    return jwt.encode(to_encode, secret, algorithm=algorithm)


def decode_token(
    token: str,
    secret: str,
    algorithm: str = "HS256",
) -> dict[str, Any]:
    """Decode and validate a JWT token.

    Args:
        token: Encoded JWT string.
        secret: Secret key used for verification.
        algorithm: Expected signing algorithm.

    Returns:
        Decoded payload dictionary.

    Raises:
        JWTError: If the token is expired, invalid, or tampered with.
    """
    try:
        return jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError:
        raise JWTError("Token has expired")
    except jwt.InvalidTokenError as exc:
        raise JWTError(f"Invalid token: {exc}")
