"""Pairing token generation and Bearer authentication."""

from __future__ import annotations

import hmac
import secrets

from fastapi import Cookie, Header, HTTPException


def generate_access_token() -> str:
    """Generate a 32-byte token encoded as URL-safe hexadecimal text."""

    return secrets.token_hex(32)


ACCESS_TOKEN = generate_access_token()
SESSION_COOKIE_NAME = "rainier_session"


def get_access_token() -> str:
    """Return the token generated for this server process."""

    return ACCESS_TOKEN


def is_valid_token(token: str | None) -> bool:
    """Compare a candidate token without leaking timing information."""

    return bool(token) and hmac.compare_digest(token, ACCESS_TOKEN)


def require_bearer_token(
    authorization: str | None = Header(default=None),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> str:
    """Accept a valid Bearer token or the browser's paired session cookie."""

    parts = authorization.split() if authorization else []
    bearer_token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None
    if is_valid_token(bearer_token):
        return bearer_token
    if is_valid_token(session_token):
        return session_token

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )
