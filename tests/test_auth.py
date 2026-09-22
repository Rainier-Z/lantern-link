from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.security import SESSION_COOKIE_NAME, get_access_token


@pytest.mark.parametrize("method", ["get", "post"])
def test_missing_token_is_rejected_with_401(client, method, message_payload):
    request = getattr(client, method)
    kwargs = {"json": message_payload} if method == "post" else {}

    response = request("/api/messages", **kwargs)

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["get", "post"])
def test_invalid_token_is_rejected_with_401(client, method, message_payload):
    request = getattr(client, method)
    kwargs = {"json": message_payload} if method == "post" else {}

    response = request(
        "/api/messages",
        headers={"Authorization": "Bearer invalid-token-for-tests"},
        **kwargs,
    )

    assert response.status_code == 401


def test_pairing_token_establishes_strict_http_only_session(client):
    token = get_access_token()

    response = client.get(
        "/",
        params={"token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie_header = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE_NAME}={token}" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "samesite=strict" in cookie_header.lower()
    assert "Path=/" in cookie_header
    assert "Secure" not in cookie_header
    assert token not in response.text

    paired_page = client.get("/")
    assert paired_page.status_code == 200
    assert token not in paired_page.text


def test_root_without_token_or_session_is_rejected(client):
    response = client.get("/")

    assert response.status_code == 401


def test_session_cookie_authenticates_protected_api(client):
    pairing = client.get(
        "/",
        params={"token": get_access_token()},
        follow_redirects=False,
    )
    assert pairing.status_code == 303

    response = client.get("/api/messages")

    assert response.status_code == 200
    assert isinstance(response.json().get("messages"), list)


def test_invalid_session_cookie_is_rejected(client):
    client.cookies.set(SESSION_COOKIE_NAME, "invalid-session-token")

    response = client.get("/api/messages")

    assert response.status_code == 401


def test_paired_session_uploads_and_downloads_a_file(tmp_path: Path):
    """A browser session can stream a generic download without a Bearer header."""

    from app.main import create_app

    application = create_app(data_dir=tmp_path)
    source = b"session-streamed-file\x00\xff"
    with TestClient(application) as client:
        paired = client.get(
            "/", params={"token": application.state.token}, follow_redirects=False
        )
        assert paired.status_code == 303

        uploaded = client.post(
            "/api/assets/files",
            data={"sender": "iphone"},
            files={"file": ("session.bin", source, "application/octet-stream")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()["asset"]["id"]

        downloaded = client.get(f"/api/assets/{asset_id}?download=1")

    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == source
    assert "attachment" in downloaded.headers["content-disposition"]
