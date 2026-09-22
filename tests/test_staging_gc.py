"""Regression tests for startup cleanup of abandoned image uploads."""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.asset_service import AssetService


def _partial_path(staging_dir: Path) -> Path:
    return staging_dir / f"{uuid4()}.partial"


def test_startup_gc_removes_only_partials_older_than_24_hours(
    tmp_path: Path,
) -> None:
    """Old abandoned uploads are removed without touching unrelated files."""

    database_path = tmp_path / "rainier.db"
    service = AssetService(database_path)
    old_partial = _partial_path(service.staging_dir)
    new_partial = _partial_path(service.staging_dir)
    staging_note = service.staging_dir / "keep.txt"
    stored_asset = service.assets_dir / "2026" / "09" / "22" / "already-stored.jpg"

    old_partial.write_bytes(b"abandoned")
    new_partial.write_bytes(b"still uploading")
    staging_note.write_text("not a partial upload", encoding="utf-8")
    stored_asset.parent.mkdir(parents=True)
    stored_asset.write_bytes(b"committed asset")
    old_time = time.time() - (24 * 60 * 60 + 60)
    os.utime(old_partial, (old_time, old_time))

    AssetService(database_path)

    assert not old_partial.exists()
    assert new_partial.read_bytes() == b"still uploading"
    assert staging_note.read_text(encoding="utf-8") == "not a partial upload"
    assert stored_asset.read_bytes() == b"committed asset"


def test_startup_gc_ignores_recent_partial_uploads(tmp_path: Path) -> None:
    """A partial younger than 24 hours remains available for the active run."""

    database_path = tmp_path / "rainier.db"
    service = AssetService(database_path)
    recent_partial = _partial_path(service.staging_dir)
    recent_partial.write_bytes(b"recent")

    AssetService(database_path)

    assert recent_partial.read_bytes() == b"recent"


def test_startup_gc_survives_physical_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed cleanup is logged/ignored and must not prevent startup."""

    database_path = tmp_path / "rainier.db"
    service = AssetService(database_path)
    old_partial = _partial_path(service.staging_dir)
    old_partial.write_bytes(b"abandoned")
    old_time = time.time() - (24 * 60 * 60 + 60)
    os.utime(old_partial, (old_time, old_time))
    original_unlink = Path.unlink
    attempted = False

    def fail_for_old_partial(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal attempted
        if path.resolve() == old_partial.resolve():
            attempted = True
            raise OSError("simulated staging cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_for_old_partial)

    started = AssetService(database_path)

    assert started.staging_dir == service.staging_dir
    assert attempted
    assert old_partial.exists()
