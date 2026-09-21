"""Shared fixtures for the Rainier Link HTTP API tests."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import get_access_token


@pytest.fixture(scope="session")
def app_module() -> Any:
    """Load the application module once so its generated token is stable."""

    return importlib.import_module("app.main")


@pytest.fixture(scope="session")
def application(app_module: Any) -> FastAPI:
    app = getattr(app_module, "app", None)
    if not isinstance(app, FastAPI):
        pytest.fail("app.main must expose a FastAPI instance named 'app'")
    return app


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application) as test_client:
        yield test_client


def token_for(application: FastAPI, app_module: Any) -> str:
    """Read the server-generated pairing token without introducing a token API."""
    return get_access_token()


@pytest.fixture
def auth_headers(application: FastAPI, app_module: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(application, app_module)}"}


@pytest.fixture
def message_payload() -> dict[str, str]:
    return {"sender": "iphone", "type": "text", "content": "Hello PC"}
