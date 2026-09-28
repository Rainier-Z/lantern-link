"""Tests for the runtime application version contract."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core import version as version_module
from app.main import create_app


def test_version_endpoint_reports_v1_and_runtime_build(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/api/version")

    assert response.status_code == 200
    assert response.json() == {
        "version": "1.0 Preview",
        "build": version_module.APP_BUILD,
    }
    assert response.json()["build"]


def test_fastapi_version_matches_application_version(tmp_path: Path) -> None:
    application = create_app(data_dir=tmp_path)

    assert application.version == "1.0 Preview"


def test_resolve_build_returns_unknown_when_git_is_unavailable(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git unavailable")

    monkeypatch.setattr(version_module.subprocess, "run", unavailable)

    assert version_module.resolve_build() == "unknown"


def test_resolve_build_rejects_untrusted_git_output(monkeypatch) -> None:
    class Result:
        stdout = "<LOCAL_PATH>\n"

    monkeypatch.setattr(
        version_module.subprocess, "run", lambda *args, **kwargs: Result()
    )

    assert version_module.resolve_build() == "unknown"
