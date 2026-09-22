"""End-to-end tests for authenticated image persistence."""

from __future__ import annotations

from pathlib import Path
import asyncio

from fastapi.testclient import TestClient

from app.main import create_app
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


def test_multipart_file_part_can_span_multiple_chunks(tmp_path: Path) -> None:
    service = AssetService(tmp_path / "rainier.db")
    boundary = b"test-boundary"
    image = b"x" * (1024 * 1024 + 17)
    body = (
        b"--" + boundary + b"\r\nContent-Disposition: form-data; name=\"sender\"\r\n\r\npc\r\n"
        b"--" + boundary + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"large.jpg\"\r\n"
        b"Content-Type: image/jpeg\r\n\r\n" + image + b"\r\n--" + boundary + b"--\r\n"
    )

    async def chunks():
        for start in range(0, len(body), 8191):
            yield body[start : start + 8191]

    staged = service.staging_dir / "test.partial"
    sender, filename, extension, mime, size, sha256 = asyncio.run(
        service._parse_multipart(chunks(), f"multipart/form-data; boundary={boundary.decode()}", staged)
    )
    assert (sender, filename, extension, mime, size) == ("pc", "large.jpg", "jpg", "image/jpeg", len(image))
    assert sha256 and staged.read_bytes() == image
