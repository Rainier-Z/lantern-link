"""Persistence and HTTP contract tests for the v0.2 message history.

These tests deliberately create an application with a temporary data directory.
The v0.2 application contract is ``app.main.create_app(data_dir=...)``; keeping
the directory injectable prevents tests from reading or mutating a developer's
real ``data/`` directory and makes an application restart deterministic.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import get_access_token


def _create_isolated_app(data_dir: Any) -> FastAPI:
    """Build an application backed by ``data_dir`` for one test instance.

    v0.1 has no application factory, so failing here gives a direct migration
    signal instead of accidentally testing the process-global in-memory store.
    """

    from app import main

    factory = getattr(main, "create_app", None)
    if factory is None:
        pytest.fail(
            "v0.2 persistence tests require app.main.create_app(data_dir=...). "
            "The current v0.1 app has only a process-global MessageStore."
        )

    application = factory(data_dir=data_dir)
    if not isinstance(application, FastAPI):
        pytest.fail("app.main.create_app(data_dir=...) must return FastAPI")
    return application


def _auth_headers(application: FastAPI) -> dict[str, str]:
    """Return the token for an isolated application instance."""

    token = getattr(application.state, "token", None) or get_access_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated_client(tmp_path: Any) -> Iterator[tuple[TestClient, FastAPI, Any]]:
    """Yield a client, application, and temporary data directory."""

    application = _create_isolated_app(tmp_path)
    with TestClient(application) as client:
        yield client, application, tmp_path


def _post_text(client: TestClient, application: FastAPI, content: str) -> dict[str, Any]:
    response = client.post(
        "/api/messages",
        headers=_auth_headers(application),
        json={"sender": "pc", "type": "text", "content": content},
    )
    assert response.status_code == 200, response.text
    return response.json()["message"]


def test_text_message_survives_recreated_application(isolated_client) -> None:
    """A newly constructed application must read the previous SQLite history."""

    client, application, data_dir = isolated_client
    posted = _post_text(client, application, "persist across restart")
    client.close()

    restarted = _create_isolated_app(data_dir)
    with TestClient(restarted) as restarted_client:
        response = restarted_client.get(
            "/api/messages", headers=_auth_headers(restarted)
        )

    assert response.status_code == 200, response.text
    assert posted in response.json()["messages"]


def test_message_history_limit_returns_latest_messages(isolated_client) -> None:
    """``limit`` bounds the result and the default order is newest first."""

    client, application, _ = isolated_client
    created = [_post_text(client, application, f"message-{index}") for index in range(5)]

    response = client.get(
        "/api/messages",
        headers=_auth_headers(application),
        params={"limit": 2},
    )

    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assert len(messages) == 2
    assert [item["id"] for item in messages] == [
        created[-1]["id"],
        created[-2]["id"],
    ]


def test_message_history_before_cursor_returns_older_messages(isolated_client) -> None:
    client, application, _ = isolated_client
    created = [_post_text(client, application, f"message-{index}") for index in range(4)]

    response = client.get(
        "/api/messages",
        headers=_auth_headers(application),
        params={"before": created[-1]["id"], "limit": 50},
    )

    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assert created[-1]["id"] not in {item["id"] for item in messages}
    assert {item["id"] for item in messages} == {
        item["id"] for item in created[:-1]
    }


def test_message_history_after_cursor_returns_newer_messages(isolated_client) -> None:
    client, application, _ = isolated_client
    created = [_post_text(client, application, f"message-{index}") for index in range(4)]

    response = client.get(
        "/api/messages",
        headers=_auth_headers(application),
        params={"after": created[0]["id"], "limit": 50},
    )

    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assert created[0]["id"] not in {item["id"] for item in messages}
    assert {item["id"] for item in messages} == {
        item["id"] for item in created[1:]
    }


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Basic wrong-token"},
        {"Authorization": "Bearer"},
    ],
)
def test_message_history_rejects_missing_or_invalid_token(
    isolated_client, headers: dict[str, str]
) -> None:
    client, _, _ = isolated_client

    response = client.get("/api/messages", headers=headers)

    assert response.status_code == 401
