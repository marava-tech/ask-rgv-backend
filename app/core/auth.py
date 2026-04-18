import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)
UTC = timezone.utc
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)


def verify_google_token(token: str) -> dict:
    return id_token.verify_oauth2_token(
        token, google_requests.Request(), settings.google_client_id
    )


def create_access_token(user_id: str, tier: str) -> str:
    payload = {
        "sub": user_id,
        "tier": tier,
        "jti": str(uuid4()),
        "exp": datetime.now(UTC) + ACCESS_TOKEN_TTL,
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_admin_token() -> str:
    payload = {
        "sub": "admin",
        "role": "admin",
        "jti": str(uuid4()),
        "exp": datetime.now(UTC) + timedelta(hours=8),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.admin_jwt_secret, algorithm="HS256")


def generate_refresh_token() -> tuple[str, str, datetime]:
    raw = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(UTC) + REFRESH_TOKEN_TTL
    return raw, hashed, expires


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def decode_admin_token(token: str) -> dict:
    return jwt.decode(token, settings.admin_jwt_secret, algorithms=["HS256"])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict | None:
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        return payload
    except jwt.PyJWTError:
        return None


async def require_user(user: dict | None = Depends(get_current_user)) -> dict:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def require_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if settings.admin_ip_list:
        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
        if client_ip not in settings.admin_ip_list:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="IP not allowed")
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin token required")
    try:
        payload = decode_admin_token(credentials.credentials)
        if payload.get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not admin")
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
