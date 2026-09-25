"""Shared fixtures for the private_send HTTP API tests."""

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


@pytest.fixture
def application(app_module: Any, tmp_path) -> FastAPI:
    """Create a fresh app whose lifecycle can only touch ``tmp_path``."""

    factory = getattr(app_module, "create_app", None)
    if not callable(factory):
        pytest.fail("app.main must expose create_app")
    return factory(
        data_dir=tmp_path / "app-data",
        user_files_dir=tmp_path / "Downloads" / "file_private_send",
        legacy_data_dir=tmp_path / "legacy",
    )


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
