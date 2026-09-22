"""End-to-end tests for authenticated image persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.asset_repository import AssetRepository
from app.services.asset_service import AssetService


def client_for(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    app = create_app(data_dir=tmp_path)
    return TestClient(app), {"Authorization": f"Bearer {app.state.token}"}, tmp_path


def upload(client: TestClient, headers: dict[str, str], name: str, content: bytes):
    return client.post(
        "/api/assets/images",
        headers=headers,
        data={"sender": "iphone"},
        files={"file": (name, content, "image/jpeg")},
    )


def upload_with_ordered_parts(
    client: TestClient,
    headers: dict[str, str],
    parts: list[tuple[str, object]],
):
    """Submit the same ordered multipart parts a browser FormData can emit."""

    return client.post(
        "/api/assets/images",
        headers=headers,
        files=parts,
    )


def test_image_round_trip_history_and_stats(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    source = b"small-image-payload"

    response = upload(client, headers, "旅行 photo.jpg", source)

    assert response.status_code == 200, response.text
    body = response.json()
    asset = body["asset"]
    assert asset["size"] == len(source)
    assert client.get(f"/api/assets/{asset['id']}", headers=headers).content == source
    history = client.get("/api/messages", headers=headers).json()["messages"]
    assert history[0]["type"] == "image"
    assert history[0]["asset"]["original_filename"] == "旅行 photo.jpg"
    stats = client.get("/api/storage/stats", headers=headers).json()
    assert stats["image_bytes"] == len(source)
    assert stats["asset_count"] == 1


def test_missing_file_returns_gone_and_delete_removes_last_reference(tmp_path: Path) -> None:
    client, headers, data_dir = client_for(tmp_path)
    created = upload(client, headers, "camera.jpg", b"image-bytes").json()
    asset_id = created["asset"]["id"]
    path = next((data_dir / "assets").rglob(f"{asset_id}.jpg"))
    path.unlink()

    missing = client.get(f"/api/assets/{asset_id}", headers=headers)
    assert missing.status_code == 410
    deleted = client.delete(f"/api/messages/{created['message']['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/assets/{asset_id}", headers=headers).status_code == 404


def test_delete_pending_asset_is_retried_on_service_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep metadata pending when unlink fails, then recover it on startup."""

    client, headers, data_dir = client_for(tmp_path)
    created = upload(client, headers, "retry-on-startup.jpg", b"image-bytes").json()
    asset_id = created["asset"]["id"]
    message_id = created["message"]["id"]
    asset_path = next((data_dir / "assets").rglob(f"{asset_id}.jpg"))
    original_unlink = Path.unlink

    def fail_only_for_target(path: Path, *, missing_ok: bool = False) -> None:
        if path.resolve() == asset_path.resolve():
            raise OSError("simulated physical delete failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_only_for_target)
    deleted = client.delete(f"/api/messages/{message_id}", headers=headers)

    assert deleted.status_code == 200, deleted.text
    pending = AssetRepository(tmp_path / "rainier.db").get(asset_id)
    assert pending is not None
    assert pending.status == "DELETE_PENDING"
    assert asset_path.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    recovered = AssetService(tmp_path / "rainier.db")

    completed = recovered.repository.get(asset_id)
    assert completed is not None
    assert completed.status == "DELETED"
    assert not asset_path.exists()


def test_upload_requires_token_and_allowed_extension(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    assert upload(client, {}, "photo.jpg", b"x").status_code == 401
    response = client.post(
        "/api/assets/images",
        headers=headers,
        data={"sender": "pc"},
        files={"file": ("notes.txt", b"not-image", "text/plain")},
    )
    assert response.status_code == 415


def test_png_and_heic_are_stored_without_conversion(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    for name, mime in (("photo.png", "image/png"), ("相机.heic", "image/heic")):
        response = client.post(
            "/api/assets/images",
            headers=headers,
            data={"sender": "pc"},
            files={"file": (name, b"original-image-bytes", mime)},
        )
        assert response.status_code == 200, response.text
        asset = response.json()["asset"]
        assert asset["original_filename"] == name
        assert client.get(f"/api/assets/{asset['id']}", headers=headers).content == b"original-image-bytes"


def test_browser_formdata_file_then_sender_order_is_accepted(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    response = upload_with_ordered_parts(
        client,
        headers,
        [
            ("file", ("from-browser.jpg", b"file-first", "image/jpeg")),
            ("sender", (None, "iphone")),
        ],
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"]["sender"] == "iphone"
    assert response.json()["asset"]["size"] == len(b"file-first")


def test_browser_formdata_sender_then_file_order_is_accepted(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    response = upload_with_ordered_parts(
        client,
        headers,
        [
            ("sender", (None, "pc")),
            ("file", ("from-browser.png", b"sender-first", "image/png")),
        ],
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"]["sender"] == "pc"
    assert response.json()["asset"]["size"] == len(b"sender-first")


def test_asset_get_and_download_require_auth_and_preserve_bytes(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    source = b"exact-original-bytes\x00\xff"
    created = upload(client, headers, "download.jpg", source).json()
    asset_id = created["asset"]["id"]

    assert client.get(f"/api/assets/{asset_id}").status_code == 401
    assert client.get(f"/api/assets/{asset_id}?download=1").status_code == 401

    inline = client.get(f"/api/assets/{asset_id}", headers=headers)
    download = client.get(
        f"/api/assets/{asset_id}?download=1",
        headers=headers,
    )
    assert inline.status_code == 200, inline.text
    assert download.status_code == 200, download.text
    assert inline.content == source
    assert download.content == source


def test_upload_errors_return_json_detail(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    response = client.post(
        "/api/assets/images",
        headers=headers,
        files=[("sender", (None, "iphone"))],
    )

    assert response.status_code == 422
    body = response.json()
    assert body.get("detail")
    assert body["detail"]
