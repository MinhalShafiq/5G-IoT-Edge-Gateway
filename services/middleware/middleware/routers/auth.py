"""Authentication endpoints: login, token refresh, API key management."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from middleware.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


# --------------- Schemas ---------------

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ApiKeyCreateRequest(BaseModel):
    name: str
    role: str = "device"


class ApiKeyResponse(BaseModel):
    key_id: str
    api_key: str
    name: str
    role: str


class MessageResponse(BaseModel):
    message: str


# --------------- Helpers ---------------

def _get_auth_service(request: Request) -> AuthService:
    """Build an AuthService from app state."""
    return AuthService(
        redis=request.app.state.redis,
        settings=request.app.state.settings,
    )


def _require_admin(request: Request) -> None:
    """Raise 403 if the authenticated user is not an admin."""
    user = getattr(request.state, "user", None)
    if user is None or user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )


# --------------- Endpoints ---------------

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    """Authenticate with username/password and receive JWT tokens."""
    auth_service = _get_auth_service(request)
    user = await auth_service.authenticate_user(body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    access_token = auth_service.create_access_token(user)
    refresh_token = auth_service.create_refresh_token(user)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(body: RefreshRequest, request: Request):
    """Exchange a valid refresh token for a new access token."""
    auth_service = _get_auth_service(request)
    try:
        payload = auth_service.verify_token(body.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not a refresh token",
        )
    user = {"sub": payload["sub"], "role": payload.get("role", "user")}
    access_token = auth_service.create_access_token(user)
    return AccessTokenResponse(access_token=access_token)


@router.post(
    "/api-keys",
    response_model=ApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_admin)],
)
async def create_api_key(body: ApiKeyCreateRequest, request: Request):
    """Create a new API key (admin only)."""
    auth_service = _get_auth_service(request)
    result = await auth_service.create_api_key(body.name, body.role)
    return ApiKeyResponse(**result)


@router.delete(
    "/api-keys/{key_id}",
    response_model=MessageResponse,
    dependencies=[Depends(_require_admin)],
)
async def revoke_api_key(key_id: str, request: Request):
    """Revoke an existing API key (admin only)."""
    auth_service = _get_auth_service(request)
    deleted = await auth_service.revoke_api_key(key_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )
    return MessageResponse(message=f"API key '{key_id}' revoked")
