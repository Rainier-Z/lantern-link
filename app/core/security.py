"""Pairing token generation and Bearer authentication."""

from __future__ import annotations

import hmac
import secrets

from fastapi import Header, HTTPException


def generate_access_token() -> str:
    """Generate a 32-byte token encoded as URL-safe hexadecimal text."""

    return secrets.token_hex(32)


ACCESS_TOKEN = generate_access_token()


def get_access_token() -> str:
    """Return the token generated for this server process."""

    return ACCESS_TOKEN


def is_valid_token(token: str | None) -> bool:
    """Compare a candidate token without leaking timing information."""

    return bool(token) and hmac.compare_digest(token, ACCESS_TOKEN)


def require_bearer_token(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency enforcing an Authorization Bearer token."""

    parts = authorization.split() if authorization else []
    if len(parts) != 2 or parts[0].lower() != "bearer" or not is_valid_token(parts[1]):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1]
