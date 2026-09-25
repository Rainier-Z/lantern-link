"""Archive naming and date rules for stable user-visible files."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import asset_service


def test_archive_date_uses_the_windows_host_local_date(monkeypatch) -> None:
    monkeypatch.setattr(
        asset_service, "_host_local_timezone", lambda: timezone(timedelta(hours=9))
    )
    created_at = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)

    assert asset_service.archive_date_for(created_at) == "2026-09-25"
