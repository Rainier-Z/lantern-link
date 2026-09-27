"""Archive naming and date rules for stable user-visible files."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import asset_service


@pytest.mark.parametrize(
    ("first", "second"),
    (
        ("2026-09-25/Report.pdf", "2026-09-25/report.pdf"),
        ("2026-09-25\\REPORT.pdf", "2026-09-25/report.PDF"),
        ("2026-09-25/R\u00e9sum\u00e9.pdf", "2026-09-25/Re\u0301sume\u0301.pdf"),
    ),
)
def test_archive_path_identity_uses_nfc_casefold_and_windows_separators(first, second):
    assert asset_service.archive_path_identity(first) == (
        asset_service.archive_path_identity(second)
    )


def test_archive_date_uses_the_windows_host_local_date(monkeypatch) -> None:
    monkeypatch.setattr(
        asset_service, "_host_local_timezone", lambda: timezone(timedelta(hours=9))
    )
    created_at = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)

    assert asset_service.archive_date_for(created_at) == "2026-09-25"
