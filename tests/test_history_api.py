"""End-to-end tests for the authenticated attachment history API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.database import get_connection
from app.main import create_app


def _client_for(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    user_files_dir = tmp_path / "Downloads" / "lantern_link"
    app = create_app(data_dir=tmp_path, user_files_dir=user_files_dir)
    client = TestClient(app)
    client.__enter__()
    return (
        client,
        {"Authorization": f"Bearer {app.state.token}"},
        user_files_dir,
    )


def _upload(
    client: TestClient,
    headers: dict[str, str],
    *,
    filename: str,
    content: bytes,
    mime_type: str,
    kind: str,
    sender: str = "pc",
) -> dict[str, object]:
    endpoint = "/api/assets/images" if kind == "image" else "/api/assets/files"
    response = client.post(
        endpoint,
        headers=headers,
        data={"sender": sender},
        files={"file": (filename, content, mime_type)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_history_requires_authentication(tmp_path: Path) -> None:
    client, _, _ = _client_for(tmp_path)

    response = client.get("/api/history")

    assert response.status_code == 401


def test_history_filters_sorts_and_returns_only_relative_urls(
    tmp_path: Path,
) -> None:
    client, headers, user_files_dir = _client_for(tmp_path)
    uploaded = [
        _upload(
            client,
            headers,
            filename="old camera.jpg",
            content=b"old-image",
            mime_type="image/jpeg",
            kind="image",
        ),
        _upload(
            client,
            headers,
            filename="Quarterly Report.pdf",
            content=b"pdf-data",
            mime_type="application/pdf",
            kind="file",
        ),
        _upload(
            client,
            headers,
            filename="latest.png",
            content=b"new-image",
            mime_type="image/png",
            kind="image",
        ),
    ]
    timestamps = (
        "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00",
        "2026-01-03T00:00:00+00:00",
    )
    with get_connection(tmp_path / "lantern_link.db") as connection:
        for result, timestamp in zip(uploaded, timestamps):
            message_id = result["message"]["id"]
            asset_id = result["asset"]["id"]
            connection.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                (timestamp, message_id),
            )
            connection.execute(
                "UPDATE assets SET created_at = ? WHERE id = ?",
                (timestamp, asset_id),
            )

    all_items = client.get("/api/history", headers=headers)
    assert all_items.status_code == 200, all_items.text
    body = all_items.json()
    items = body["items"]
    assert body["count"] == 3
    assert [item["filename"] for item in items] == [
        "latest.png",
        "Quarterly Report.pdf",
        "old camera.jpg",
    ]
    assert items[0]["kind"] == "image"
    assert items[0]["display_name"] == "latest.png"
    assert items[0]["size"] == len(b"new-image")
    assert items[0]["created_at"] == timestamps[2].replace("+00:00", "Z")
    assert items[0]["availability"] == "AVAILABLE"
    assert items[0]["asset_url"] == f"/api/assets/{items[0]['asset_id']}"
    assert items[0]["download_url"] == (
        f"/api/assets/{items[0]['asset_id']}?download=1"
    )
    opened = client.get(items[0]["asset_url"], headers=headers)
    downloaded = client.get(items[0]["download_url"], headers=headers)
    assert opened.content == b"new-image"
    assert downloaded.content == b"new-image"

    images = client.get("/api/history?type=image", headers=headers).json()
    assert [item["kind"] for item in images["items"]] == ["image", "image"]
    files = client.get("/api/history?type=file", headers=headers).json()
    assert [item["filename"] for item in files["items"]] == ["Quarterly Report.pdf"]
    matches = client.get("/api/history?q=REPORT", headers=headers).json()
    assert [item["filename"] for item in matches["items"]] == ["Quarterly Report.pdf"]
    assert client.get("/api/history?type=video", headers=headers).status_code == 422

    serialized = all_items.text
    assert str(user_files_dir) not in serialized
    assert str(tmp_path) not in serialized
    assert client.app.state.token not in serialized
    assert "staging" not in serialized
    assert "C:\\" not in serialized


def test_history_search_uses_unicode_casefold_for_filename_matching(
    tmp_path: Path,
) -> None:
    client, headers, _ = _client_for(tmp_path)
    _upload(
        client,
        headers,
        filename="Überblick.pdf",
        content=b"pdf-data",
        mime_type="application/pdf",
        kind="file",
    )

    response = client.get("/api/history?q=über", headers=headers)

    assert response.status_code == 200, response.text
    assert [item["filename"] for item in response.json()["items"]] == [
        "Überblick.pdf"
    ]


def test_history_marks_manually_removed_archive_as_missing_and_keeps_message(
    tmp_path: Path,
) -> None:
    client, headers, user_files_dir = _client_for(tmp_path)
    created = _upload(
        client,
        headers,
        filename="keep-history.txt",
        content=b"user-file",
        mime_type="text/plain",
        kind="file",
    )
    message_id = created["message"]["id"]
    asset_id = created["asset"]["id"]
    archived_file = user_files_dir / created["asset"]["relative_path"]
    archived_file.unlink()

    response = client.get("/api/history", headers=headers)

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["message_id"] == message_id
    assert item["asset_id"] == asset_id
    assert item["availability"] == "MISSING"
    assert item["asset_url"] is None
    assert item["download_url"] is None
    with get_connection(tmp_path / "lantern_link.db") as connection:
        status = connection.execute(
            "SELECT status FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()[0]
    assert status == "MISSING"
    messages = client.get("/api/messages", headers=headers).json()["messages"]
    assert any(message["id"] == message_id for message in messages)


def test_history_normalizes_archive_pending_to_pending_availability(
    tmp_path: Path,
) -> None:
    client, headers, _ = _client_for(tmp_path)
    created = _upload(
        client,
        headers,
        filename="awaiting-archive.bin",
        content=b"pending",
        mime_type="application/octet-stream",
        kind="file",
    )
    asset_id = created["asset"]["id"]
    with get_connection(tmp_path / "lantern_link.db") as connection:
        connection.execute(
            "UPDATE assets SET status = 'ARCHIVE_PENDING' WHERE id = ?",
            (asset_id,),
        )

    item = client.get("/api/history", headers=headers).json()["items"][0]

    assert item["availability"] == "PENDING"
    assert item["asset_url"] is None
    assert item["download_url"] is None


def test_history_returns_format_direction_archive_date_and_cursor_pages(
    tmp_path: Path,
) -> None:
    client, headers, _ = _client_for(tmp_path)
    first = _upload(
        client, headers, filename="report.pdf", content=b"pdf", mime_type="application/pdf", kind="file", sender="pc"
    )
    second = _upload(
        client, headers, filename="archive.zip", content=b"zip", mime_type="application/zip", kind="file", sender="iphone"
    )
    _upload(
        client, headers, filename="photo.jpg", content=b"jpg", mime_type="image/jpeg", kind="image", sender="pc"
    )

    page = client.get("/api/history?limit=2", headers=headers)

    assert page.status_code == 200, page.text
    body = page.json()
    assert body["count"] == 2
    assert body["has_more"] is True
    assert body["next_cursor"] == body["items"][-1]["message_id"]
    assert body["items"][0]["sender"] == "pc"
    assert body["items"][1]["file_format"] == "zip"
    assert body["items"][1]["extension"] == ".zip"
    assert body["items"][1]["archive_date"] == second["asset"]["relative_path"].split("/", 1)[0]
    assert body["items"][0]["file_format"] is None

    next_page = client.get(
        f"/api/history?limit=2&before={body['next_cursor']}", headers=headers
    )
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["has_more"] is False
    assert [item["message_id"] for item in body["items"] + next_page.json()["items"]] == [
        first["message"]["id"], second["message"]["id"],
        body["items"][0]["message_id"],
    ][::-1]

    pdf_only = client.get("/api/history?format=pdf", headers=headers)
    assert [item["filename"] for item in pdf_only.json()["items"]] == ["report.pdf"]


def test_history_display_name_is_the_actual_archived_filename(tmp_path: Path) -> None:
    client, headers, _ = _client_for(tmp_path)
    _upload(
        client, headers, filename="report.pdf", content=b"first",
        mime_type="application/pdf", kind="file",
    )
    _upload(
        client, headers, filename="report.pdf", content=b"second",
        mime_type="application/pdf", kind="file",
    )

    items = client.get("/api/history?format=pdf", headers=headers).json()["items"]

    assert items[0]["filename"] == "report.pdf"
    assert items[0]["display_name"] == "report (1).pdf"

    collision_matches = client.get(
        "/api/history?q=report%20(1)", headers=headers
    ).json()["items"]

    assert len(collision_matches) == 1
    assert collision_matches[0]["filename"] == "report.pdf"
    assert collision_matches[0]["display_name"] == "report (1).pdf"
