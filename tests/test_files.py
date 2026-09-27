"""End-to-end coverage for the v0.3 generic file transfer contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.asset_service import FILE_POLICY, UploadError


def client_for(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    """Create an isolated app whose SQLite and asset files live under tmp_path."""

    from app.main import create_app

    user_files_dir = tmp_path / "Downloads" / "file_private_send"
    application = create_app(data_dir=tmp_path, user_files_dir=user_files_dir)
    client = TestClient(application)
    client.__enter__()
    return (
        client,
        {"Authorization": f"Bearer {application.state.token}"},
        user_files_dir,
    )


def upload_file(
    client: TestClient,
    headers: dict[str, str],
    filename: str,
    content: bytes,
    *,
    sender: str = "pc",
    content_type: str = "application/octet-stream",
):
    return client.post(
        "/api/assets/files",
        headers=headers,
        data={"sender": sender},
        files={"file": (filename, content, content_type)},
    )


GENERIC_FILES = (
    ("report.pdf", "application/pdf"),
    ("合同 中文.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("演示 🎉.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("预算表.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("archive.zip", "application/zip"),
    ("payload.json", "application/json"),
    ("unknown.custom", "application/octet-stream"),
    ("README", "application/octet-stream"),
)


@pytest.mark.parametrize(("filename", "content_type"), GENERIC_FILES)
def test_generic_file_formats_preserve_metadata_and_bytes(
    tmp_path: Path,
    filename: str,
    content_type: str,
) -> None:
    client, headers, user_files_dir = client_for(tmp_path)
    source = (f"original bytes for {filename}\x00\xff".encode("utf-8"))

    response = upload_file(
        client,
        headers,
        filename,
        source,
        content_type=content_type,
        sender="iphone",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    asset = body["asset"]
    message = body["message"]
    assert message["type"] == "file"
    assert message["sender"] == "iphone"
    assert asset["kind"] == "file"
    assert asset["original_filename"] == filename
    assert asset["extension"] == Path(filename).suffix.lower()
    assert asset["mime_type"] == content_type
    assert asset["size"] == len(source)
    assert asset["sha256"] == hashlib.sha256(source).hexdigest()
    stored_path = user_files_dir / Path(asset["relative_path"])
    assert stored_path.is_file()
    assert stored_path.read_bytes() == source

    downloaded = client.get(
        f"/api/assets/{asset['id']}?download=1",
        headers=headers,
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == source


def test_uploads_use_original_names_collision_suffix_and_user_root_relative_paths(
    tmp_path: Path,
) -> None:
    client, headers, user_files_dir = client_for(tmp_path)

    first = upload_file(
        client, headers, "report.txt", b"first bytes", content_type="text/plain"
    ).json()["asset"]
    second = upload_file(
        client, headers, "report.txt", b"second bytes", content_type="text/plain"
    ).json()["asset"]

    first_date = first["created_at"][:10]
    second_date = second["created_at"][:10]
    first_path = Path(first["relative_path"])
    second_path = Path(second["relative_path"])
    assert first_path.as_posix() == f"{first_date}/report.txt"
    assert second_path.as_posix() == f"{second_date}/report (1).txt"
    assert first_path.is_relative_to(Path(first_date))
    assert (user_files_dir / first_path).read_bytes() == b"first bytes"
    assert (user_files_dir / second_path).read_bytes() == b"second bytes"
    assert first["stored_filename"] == "report.txt"
    assert second["stored_filename"] == "report (1).txt"


def test_deleted_formal_path_is_never_reused_by_a_later_upload(tmp_path: Path) -> None:
    client, headers, user_files_dir = client_for(tmp_path)
    first = upload_file(
        client, headers, "report.pdf", b"first", content_type="application/pdf"
    ).json()["asset"]
    (user_files_dir / first["relative_path"]).unlink()

    second = upload_file(
        client, headers, "report.pdf", b"second", content_type="application/pdf"
    ).json()["asset"]

    assert first["relative_path"].endswith("/report.pdf")
    assert second["relative_path"].endswith("/report (1).pdf")
    assert first["relative_path"] != second["relative_path"]
    assert client.get(f"/api/assets/{first['id']}", headers=headers).status_code == 410
    assert client.get(f"/api/assets/{second['id']}", headers=headers).content == b"second"


@pytest.mark.parametrize(
    ("first_name", "second_name"),
    (
        ("Report.pdf", "report.pdf"),
        ("REPORT.PDF", "report.pdf"),
        ("R\u00e9sum\u00e9.pdf", "Re\u0301sume\u0301.pdf"),
    ),
)
def test_windows_equivalent_archive_paths_are_never_reused(
    tmp_path: Path, first_name: str, second_name: str
) -> None:
    client, headers, user_files_dir = client_for(tmp_path)
    first = upload_file(client, headers, first_name, b"first").json()["asset"]
    (user_files_dir / first["relative_path"]).unlink()

    second = upload_file(client, headers, second_name, b"second").json()["asset"]

    assert first["stored_filename"] == first_name
    assert second["stored_filename"].endswith(" (1).pdf")
    assert second["relative_path"] != first["relative_path"]


@pytest.mark.parametrize(
    ("first_name", "second_name"),
    (
        ("Report.pdf", "report.pdf"),
        ("REPORT.PDF", "report.pdf"),
        ("R\u00e9sum\u00e9.pdf", "Re\u0301sume\u0301.pdf"),
    ),
)
def test_deleted_asset_path_identity_remains_reserved(
    tmp_path: Path, first_name: str, second_name: str
) -> None:
    client, headers, user_files_dir = client_for(tmp_path)
    first = upload_file(client, headers, first_name, b"first").json()
    first_path = user_files_dir / first["asset"]["relative_path"]
    deleted = client.delete(f"/api/messages/{first['message']['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    first_path.unlink()

    second = upload_file(client, headers, second_name, b"second").json()["asset"]

    assert second["stored_filename"].endswith(" (1).pdf")


def test_upload_filename_is_normalized_to_a_safe_archive_basename(
    tmp_path: Path,
) -> None:
    client, headers, user_files_dir = client_for(tmp_path)

    response = upload_file(
        client,
        headers,
        r"..\..\report.txt",
        b"safe bytes",
        content_type="text/plain",
    )

    assert response.status_code == 200, response.text
    asset = response.json()["asset"]
    relative_path = Path(asset["relative_path"])
    assert relative_path.parts[-1] == "report.txt"
    assert len(relative_path.parts) == 2
    assert (user_files_dir / relative_path).read_bytes() == b"safe bytes"


def test_generic_file_history_survives_restart_and_reports_file_stats(
    tmp_path: Path,
) -> None:
    client, headers, _ = client_for(tmp_path)
    source = b"persist me across process restart"
    created = upload_file(
        client,
        headers,
        "history file 📦.bin",
        source,
        content_type="application/octet-stream",
    ).json()
    message_id = created["message"]["id"]
    asset_id = created["asset"]["id"]
    client.close()

    restarted, restarted_headers, _ = client_for(tmp_path)
    history = restarted.get("/api/messages", headers=restarted_headers)
    assert history.status_code == 200, history.text
    persisted = next(item for item in history.json()["messages"] if item["id"] == message_id)
    assert persisted["type"] == "file"
    assert persisted["asset"]["id"] == asset_id
    assert persisted["asset"]["original_filename"] == "history file 📦.bin"
    assert restarted.get(
        f"/api/assets/{asset_id}", headers=restarted_headers
    ).content == source

    stats = restarted.get("/api/storage/stats", headers=restarted_headers)
    assert stats.status_code == 200, stats.text
    assert stats.json()["file_bytes"] == len(source)


def test_generic_file_upload_requires_bearer_token(tmp_path: Path) -> None:
    client, _, _ = client_for(tmp_path)
    response = upload_file(client, {}, "private.zip", b"zip bytes")
    assert response.status_code == 401


def test_generic_file_limit_is_512_mib_without_allocating_a_large_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the HTTP boundary with the service's real 512 MiB contract.

    The multipart parser already owns the request body, so a boundary test can
    return the same service error without allocating a 512 MiB test fixture.
    The policy assertion keeps this test coupled to the advertised limit.
    """

    assert FILE_POLICY.max_bytes == 512 * 1024 * 1024
    client, headers, _ = client_for(tmp_path)
    service: Any = client.app.state.asset_service

    async def reject_oversized_upload(file: Any, sender: str):
        raise UploadError(413, "Upload exceeds the 512 MiB limit")

    monkeypatch.setattr(service, "upload_file", reject_oversized_upload)
    response = upload_file(client, headers, "too-large.bin", b"small request")
    assert response.status_code == 413
    assert response.json()["detail"] == "Upload exceeds the 512 MiB limit"


def test_no_format_specific_file_endpoints_are_exposed(tmp_path: Path) -> None:
    client, _, _ = client_for(tmp_path)
    paths = client.app.openapi()["paths"]
    assert "/api/assets/files" in paths
    assert "/api/assets/pdf" not in paths
    assert "/api/assets/docx" not in paths
    assert "/api/assets/pptx" not in paths


def test_generic_file_upload_rejects_empty_payload(tmp_path: Path) -> None:
    client, headers, _ = client_for(tmp_path)
    response = upload_file(client, headers, "empty.json", b"", content_type="application/json")
    assert response.status_code == 400
    assert response.json()["detail"] == "Upload cannot be empty"
