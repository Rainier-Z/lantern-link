"""The canonical upload endpoint owns image-vs-file classification."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize(
    ("filename", "mime_type", "expected_kind"),
    [
        ("photo.jpg", "image/jpeg", "image"),
        ("photo.heic", "application/octet-stream", "image"),
        ("vector.svg", "image/svg+xml", "file"),
        ("photo.avif", "image/avif", "file"),
        ("scan.tiff", "image/tiff", "file"),
        ("archive.xyz", "application/octet-stream", "file"),
    ],
)
def test_canonical_upload_classifies_by_supported_filename_extension(
    tmp_path: Path, filename: str, mime_type: str, expected_kind: str
) -> None:
    app = create_app(
        data_dir=tmp_path / "app-data",
        user_files_dir=tmp_path / "Downloads" / "file_private_send",
        legacy_data_dir=tmp_path / "legacy",
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/assets",
            headers={"Authorization": f"Bearer {app.state.token}"},
            data={"sender": "pc"},
            files={"file": (filename, b"payload", mime_type)},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asset"]["kind"] == expected_kind
    assert body["archive"] == {
        "date": body["asset"]["relative_path"].split("/", 1)[0],
        "display_dir": "Windows Downloads\\file_private_send\\"
        + body["asset"]["relative_path"].split("/", 1)[0],
    }
    assert "C:\\" not in response.text
