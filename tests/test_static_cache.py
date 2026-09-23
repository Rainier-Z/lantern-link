"""Regression tests for the v0.3.2 static-resource and composer contract."""

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
        app_js = client.get("/app.js?v=0.3.2")
        style_css = client.get("/style.css?v=0.3.2")

        for response in (root, app_js, style_css):
            _assert_no_store(response)

        # The HTML shell must reference the same cache-busting version as the
        # browser requests above; otherwise a stale shell can load stale code.
        html = root.text
        assert '/app.js?v=0.3.2' in html
        assert '/style.css?v=0.3.2' in html


def test_version_endpoint_is_public_and_reports_v032(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/api/version")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"version", "build"}
        assert body["version"] == "0.3.2"
        assert isinstance(body["build"], str)
        assert body["build"].strip()


def test_static_shell_uses_composer_batch_contract_without_storage_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "app" / "web" / "app.js").read_text(encoding="utf-8")
    html = (root / "app" / "web" / "index.html").read_text(encoding="utf-8")
    css = (root / "app" / "web" / "style.css").read_text(encoding="utf-8")

    assert "composerEntries: []" in script
    assert "uploadRunning: false" in script
    assert "clientEntrySeq: 0" in script
    assert "crypto.randomUUID" not in script
    assert "private_send" in script
    assert "Rainier" + " Link" not in html
    assert "rainier" + "-link-" not in script
    assert "return `${Date.now()}-${state.clientEntrySeq}`" in script
    assert "function currentBatchIsTerminal()" in script
    assert '["COMPLETED", "FAILED"].includes(entry.status)' in script
    assert "if (state.uploadRunning)" in script
    assert 'feedback("Wait for the current upload to finish before selecting files.", true)' in script
    assert "el.message_input.disabled = uploading || state.textSending" in script
    assert 'retry.disabled = state.uploadRunning' in script
    assert script.count('asset.original_filename || "private_send"') == 2
    assert all(
        f"function {name}(" in script
        for name in (
            "nextClientEntryId",
            "stageFiles",
            "renderComposerBatch",
            "handleFileSelection",
            "sendComposer",
            "sendTextMessage",
            "uploadEntry",
            "processEntry",
            "processCurrentBatch",
            "scrollToBottom",
        )
    )
    assert all(
        legacy_name not in script
        for legacy_name in ("uploadQueue", "queueFiles", "processQueue", "stats(")
    )
    assert "storage" not in script.lower()
    assert "storage" not in html.lower()
    assert "storage" not in css.lower()
    assert "requestAnimationFrame" in script
    assert 'scrollIntoView({ block: "end", behavior: "auto" })' in script
    assert '"/api/assets/images"' in script
    assert '"/api/assets/files"' in script
    assert 'request.setRequestHeader("Authorization"' in script


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
