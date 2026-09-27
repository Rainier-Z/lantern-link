"""Windows path resolution keeps user-visible archives in Downloads."""

from __future__ import annotations

from pathlib import Path

from app.core import config


def test_user_files_uses_known_downloads_before_profile_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "_resolve_windows_downloads", lambda: Path("D:/Downloads"))

    assert config.resolve_user_files_dir() == Path("D:/Downloads/lantern_link")


def test_explicit_user_files_profile_remains_injectable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "_resolve_windows_downloads", lambda: Path("D:/Downloads"))

    assert config.resolve_user_files_dir(tmp_path / "Profile") == (
        tmp_path / "Profile" / "Downloads" / "lantern_link"
    )
