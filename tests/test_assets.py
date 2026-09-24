"""End-to-end tests for authenticated image persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_connection
from app.core.database import initialize_database
from app.main import bootstrap_app, create_app
from app.repositories.asset_repository import AssetRepository


def client_for(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    user_files_dir = tmp_path / "Downloads" / "file_private_send"
    app = create_app(data_dir=tmp_path, user_files_dir=user_files_dir)
    return (
        TestClient(app),
        {"Authorization": f"Bearer {app.state.token}"},
        user_files_dir,
    )


def upload(client: TestClient, headers: dict[str, str], name: str, content: bytes):
    return client.post(
        "/api/assets/images",
        headers=headers,
        data={"sender": "iphone"},
        files={"file": (name, content, "image/jpeg")},
    )


def upload_generic_file(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    content: bytes,
    mime_type: str,
):
    return client.post(
        "/api/assets/files",
        headers=headers,
        data={"sender": "pc"},
        files={"file": (name, content, mime_type)},
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
    client, headers, user_files_dir = client_for(tmp_path)
    created = upload(client, headers, "camera.jpg", b"image-bytes").json()
    asset_id = created["asset"]["id"]
    path = user_files_dir / created["asset"]["relative_path"]
    path.unlink()

    missing = client.get(f"/api/assets/{asset_id}", headers=headers)
    assert missing.status_code == 410
    with get_connection(tmp_path / "private_send.db") as connection:
        assert connection.execute(
            "SELECT status FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()[0] == "MISSING"
    history = client.get("/api/messages", headers=headers).json()["messages"]
    assert any(item["asset"]["id"] == asset_id for item in history)

    deleted = client.delete(f"/api/messages/{created['message']['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/assets/{asset_id}", headers=headers).status_code == 404


def test_delete_pending_asset_is_retried_on_service_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry failed metadata deletion without removing the archived file."""

    client, headers, user_files_dir = client_for(tmp_path)
    created = upload(client, headers, "retry-on-startup.jpg", b"image-bytes").json()
    asset_id = created["asset"]["id"]
    message_id = created["message"]["id"]
    asset_path = user_files_dir / created["asset"]["relative_path"]
    original_mark_deleted = AssetRepository.mark_deleted

    def fail_only_for_target(repository, target_id, deleted_at):
        if target_id == asset_id:
            return False
        return original_mark_deleted(repository, target_id, deleted_at)

    monkeypatch.setattr(AssetRepository, "mark_deleted", fail_only_for_target)
    deleted = client.delete(f"/api/messages/{message_id}", headers=headers)

    assert deleted.status_code == 200, deleted.text
    pending = AssetRepository(tmp_path / "private_send.db").get(asset_id)
    assert pending is not None
    assert pending.status == "DELETE_PENDING"
    assert asset_path.is_file()

    monkeypatch.setattr(AssetRepository, "mark_deleted", original_mark_deleted)
    _, _, recovered, _ = bootstrap_app(
        app_data_dir=tmp_path,
        user_files_dir=user_files_dir,
    )

    completed = recovered.repository.get(asset_id)
    assert completed is not None
    assert completed.status == "DELETED"
    assert asset_path.read_bytes() == b"image-bytes"


def test_legacy_asset_import_copies_to_date_archive_and_updates_metadata(
    tmp_path: Path,
) -> None:
    legacy_dir = tmp_path / "legacy"
    app_data_dir = tmp_path / "app-data"
    user_files_dir = tmp_path / "Downloads" / "file_private_send"
    database_path = legacy_dir / "private_send.db"
    asset_id = "legacy-asset"
    source_relative_path = f"assets/2026/01/02/{asset_id}.jpg"
    source_path = legacy_dir / source_relative_path
    payload = b"old image bytes"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(payload)
    initialize_database(database_path)
    with get_connection(database_path) as connection:
        connection.execute(
            """INSERT INTO assets
               (id, kind, original_filename, stored_filename, extension, mime_type,
                size, sha256, relative_path, status, created_at)
               VALUES (?, 'image', 'old trip.jpg', ?, '.jpg', 'image/jpeg', ?, ?, ?,
                       'AVAILABLE', '2026-01-02T03:04:05+00:00')""",
            (
                asset_id,
                f"{asset_id}.jpg",
                len(payload),
                "unused-test-digest",
                source_relative_path,
            ),
        )
        connection.execute(
            """INSERT INTO messages
               (id, sender, type, asset_id, status, created_at)
               VALUES ('legacy-message', 'iphone', 'image', ?, 'SENT',
                       '2026-01-02T03:04:05+00:00')""",
            (asset_id,),
        )

    app = create_app(
        data_dir=app_data_dir,
        user_files_dir=user_files_dir,
        legacy_data_dir=legacy_dir,
    )
    headers = {"Authorization": f"Bearer {app.state.token}"}
    migrated_asset = AssetRepository(app_data_dir / "private_send.db").get(asset_id)

    assert migrated_asset is not None
    assert migrated_asset.relative_path == "2026-01-02/old trip.jpg"
    assert (user_files_dir / migrated_asset.relative_path).read_bytes() == payload
    assert source_path.read_bytes() == payload
    assert (user_files_dir / source_relative_path).read_bytes() == payload
    with TestClient(app) as client:
        download = client.get(f"/api/assets/{asset_id}", headers=headers)
        assert download.status_code == 200, download.text
        assert download.content == payload


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


@pytest.mark.parametrize(
    ("status", "expected_status"),
    (
        ("AVAILABLE", 200),
        ("MISSING", 410),
        ("DELETE_PENDING", 404),
        ("DELETED", 404),
        ("UNKNOWN", 404),
    ),
)
def test_asset_status_whitelist_serves_only_available_assets(
    tmp_path: Path, status: str, expected_status: int
) -> None:
    client, headers, _ = client_for(tmp_path)
    asset_id = upload(client, headers, "status.jpg", b"image").json()["asset"]["id"]

    with get_connection(tmp_path / "private_send.db") as connection:
        connection.execute("UPDATE assets SET status = ? WHERE id = ?", (status, asset_id))
    response = client.get(f"/api/assets/{asset_id}", headers=headers)
    assert response.status_code == expected_status, (status, response.text)


def test_image_defaults_inline_and_download_is_attachment(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    asset_id = upload(client, headers, "picture.jpg", b"image").json()["asset"]["id"]

    normal = client.get(f"/api/assets/{asset_id}", headers=headers)
    download = client.get(f"/api/assets/{asset_id}?download=1", headers=headers)

    assert normal.headers["content-disposition"].startswith("inline")
    assert download.headers["content-disposition"].startswith("attachment")


def test_pdf_defaults_attachment_and_preview_is_inline(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    response = upload_generic_file(client, headers, "document.pdf", b"pdf", "application/pdf")
    assert response.status_code == 200, response.text
    asset_id = response.json()["asset"]["id"]

    normal = client.get(f"/api/assets/{asset_id}", headers=headers)
    preview = client.get(f"/api/assets/{asset_id}?preview=1", headers=headers)

    assert normal.headers["content-disposition"].startswith("attachment")
    assert preview.headers["content-disposition"].startswith("inline")


def test_html_defaults_attachment_and_preview_is_unsupported(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    response = upload_generic_file(client, headers, "notes.html", b"<h1>Hi</h1>", "text/html")
    assert response.status_code == 200, response.text
    asset_id = response.json()["asset"]["id"]

    normal = client.get(f"/api/assets/{asset_id}", headers=headers)
    preview = client.get(f"/api/assets/{asset_id}?preview=1", headers=headers)

    assert normal.headers["content-disposition"].startswith("attachment")
    assert preview.status_code == 415


def test_asset_download_and_preview_flags_are_mutually_exclusive(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    asset_id = upload(client, headers, "flags.jpg", b"image").json()["asset"]["id"]

    response = client.get(
        f"/api/assets/{asset_id}?download=1&preview=1", headers=headers
    )

    assert response.status_code == 400


def test_mutually_exclusive_flags_precede_asset_lookup_and_status(
    tmp_path: Path,
) -> None:
    client, headers, _ = client_for(tmp_path)
    asset_id = upload(client, headers, "flags.jpg", b"image").json()["asset"]["id"]

    unauthenticated = client.get("/api/assets/unknown?download=1&preview=1")
    assert unauthenticated.status_code == 401

    actual_statuses = []
    for status in ("AVAILABLE", "MISSING", "DELETE_PENDING", "DELETED"):
        with get_connection(tmp_path / "private_send.db") as connection:
            connection.execute(
                "UPDATE assets SET status = ? WHERE id = ?", (status, asset_id)
            )
        response = client.get(
            f"/api/assets/{asset_id}?download=1&preview=1", headers=headers
        )
        actual_statuses.append(response.status_code)

    missing_id = client.get(
        "/api/assets/unknown?download=1&preview=1", headers=headers
    )
    actual_statuses.append(missing_id.status_code)

    assert actual_statuses == [400, 400, 400, 400, 400]


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
