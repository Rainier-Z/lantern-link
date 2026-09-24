"""Boundary tests for streamed asset uploads."""

from __future__ import annotations

import asyncio
import sqlite3
from io import BytesIO

from fastapi import UploadFile
import pytest
from starlette.datastructures import Headers

from app.core.database import initialize_database
from app.services.asset_service import AssetService, UploadError, UploadPolicy


def _upload(payload: bytes) -> UploadFile:
    return UploadFile(
        filename="boundary.bin",
        file=BytesIO(payload),
        headers=Headers({"content-type": "application/octet-stream"}),
    )


def test_upload_accepts_exactly_maximum_bytes(tmp_path) -> None:
    app_data_dir = tmp_path / "app-data"
    user_files_dir = tmp_path / "Downloads" / "file_private_send"
    database_path = app_data_dir / "database.sqlite3"
    initialize_database(database_path)
    service = AssetService(
        database_path,
        staging_dir=app_data_dir / "staging",
        user_files_dir=user_files_dir,
    )
    policy = UploadPolicy(kind="file", max_bytes=8)

    message, asset = asyncio.run(
        service.persist_upload(_upload(b"12345678"), "pc", policy)
    )

    stored_path = service.resolve_asset_path(asset)
    assert stored_path == user_files_dir / asset.relative_path
    assert stored_path.parent.name == asset.created_at.strftime("%Y-%m-%d")
    assert stored_path.name == "boundary.bin"
    assert stored_path.read_bytes() == b"12345678"
    assert asset.size == 8
    assert message.asset_id == asset.id
    assert list(service.staging_dir.iterdir()) == []
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_upload_rejects_one_byte_over_limit_without_persisting_anything(tmp_path) -> None:
    app_data_dir = tmp_path / "app-data"
    user_files_dir = tmp_path / "Downloads" / "file_private_send"
    database_path = app_data_dir / "database.sqlite3"
    initialize_database(database_path)
    service = AssetService(
        database_path,
        staging_dir=app_data_dir / "staging",
        user_files_dir=user_files_dir,
    )
    policy = UploadPolicy(kind="file", max_bytes=8)

    with pytest.raises(UploadError) as error:
        asyncio.run(service.persist_upload(_upload(b"123456789"), "pc", policy))
    assert error.value.status_code == 413

    assert list(service.staging_dir.glob("*.partial")) == []
    assert [path for path in service.assets_dir.rglob("*") if path.is_file()] == []
    assert [path for path in user_files_dir.rglob("*") if path.is_file()] == []
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
