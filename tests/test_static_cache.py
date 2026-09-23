"""Regression tests for the v0.3.1 static-resource and browser contract."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import get_access_token
from app.main import create_app


def _assert_no_store(response) -> None:
    """Require the browser-facing response to disable intermediary caching."""

    assert response.status_code == 200, response.text
    assert "no-store" in response.headers.get("cache-control", "").lower()
    assert response.headers.get("pragma", "").lower() == "no-cache"
    assert response.headers.get("expires", "").strip() == "0"


def test_root_and_versioned_static_resources_disable_cache(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        root = client.get("/", params={"token": get_access_token()})
        app_js = client.get("/app.js?v=0.3.1")
        style_css = client.get("/style.css?v=0.3.1")

        for response in (root, app_js, style_css):
            _assert_no_store(response)

        # The HTML shell must reference the same cache-busting version as the
        # browser requests above; otherwise a stale shell can load stale code.
        html = root.text
        assert '/app.js?v=0.3.1' in html
        assert '/style.css?v=0.3.1' in html


def test_version_endpoint_is_public_and_reports_v031(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/api/version")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"version", "build"}
        assert body["version"] == "0.3.1"
        assert isinstance(body["build"], str)
        assert body["build"].strip()


def test_static_shell_has_queue_storage_and_safe_pdf_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "app" / "web" / "app.js").read_text(encoding="utf-8")
    html = (root / "app" / "web" / "index.html").read_text(encoding="utf-8")
    css = (root / "app" / "web" / "style.css").read_text(encoding="utf-8")

    assert "while (true)" in script and 'find((item) => item.status === "QUEUED")' in script
    assert "async function processEntry(entry)" in script
    assert "name: file.name" in script and "size: file.size" in script
    assert "entry.file = null" in script
    assert 'asset.extension?.toLowerCase() === ".pdf"' in script
    assert 'asset.mime_type?.toLowerCase() === "application/pdf"' in script
    assert "?preview=1" in script
    assert all(f">{label}</span>" in html for label in ("Total", "Images", "Files", "Assets"))
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in css
    assert "@media (max-width: 768px)" in css


def test_asset_endpoint_keeps_bearer_authentication(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/api/assets/not-an-asset")

        assert response.status_code == 401


def test_authenticated_asset_read_returns_uploaded_bytes(tmp_path: Path) -> None:
    application = create_app(tmp_path)
    headers = {"Authorization": f"Bearer {application.state.token}"}
    source = b"v0.3.0-cache-regression-image"

    with TestClient(application) as client:
        uploaded = client.post(
            "/api/assets/images",
            headers=headers,
            data={"sender": "pc"},
            files={"file": ("cache-check.png", source, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()["asset"]["id"]

        assert client.get(f"/api/assets/{asset_id}").status_code == 401
        fetched = client.get(f"/api/assets/{asset_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.content == source
