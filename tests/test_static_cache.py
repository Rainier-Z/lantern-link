"""Regression tests for the 1.0 static-resource and composer contract."""

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
        app_js = client.get("/app.js")
        style_css = client.get("/style.css")

        for response in (root, app_js, style_css):
            _assert_no_store(response)

        # The HTML shell must reference the same cache-busting version as the
        # browser requests above; otherwise a stale shell can load stale code.
        html = root.text
        assert '/app.js' in html and '?v=' not in html
        assert '/style.css' in html and '?v=' not in html


def test_version_endpoint_is_public_and_reports_v1(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/api/version")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"version", "build"}
        assert body["version"] == "1.0"
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
    assert '"/api/assets/images"' not in script
    assert '"/api/assets/files"' not in script
    assert 'request.setRequestHeader("Authorization"' in script

    # One unrestricted picker keeps image formats and generic files selectable.
    assert html.count('type="file"') == 1
    assert 'id="file-input" type="file" multiple' in html
    assert 'id="image-input"' not in html
    assert 'id="image-pick-button"' not in html
    assert 'id="file-pick-button"' in html
    assert 'accept="' not in html
    assert "isImageFile" not in script
    assert "Number.isNaN(date.getTime())" in script

    assert 'id="history-toggle"' in html
    assert 'aria-label="Open history"' in html
    assert '<main class="app-shell" id="app-shell">' in html
    assert '"app-shell"' in script
    assert 'role="dialog" aria-modal="true"' in html
    assert 'id="history-close"' in html
    assert 'id="history-overlay"' in html
    assert 'id="history-tab-date"' in html and ">By Date<" in html
    assert 'id="history-tab-file"' in html and ">By File<" in html
    assert all(f'value="{kind}"' in html for kind in ("all", "image", "file"))
    assert 'id="history-search" type="search"' in html
    assert 'id="history-format"' in html
    assert 'id="history-more"' in html
    assert 'id="batch-toast" role="status" aria-live="polite"' in html
    assert 'historyHasMore' in script
    assert 'historyController: null' in script
    assert 'state.historyController.abort()' in script
    assert 'if (el.history_drawer.hidden) return;' in script
    assert 'if (!reset && state.historyLoading) return;' in script
    assert 'if (reset && state.historyController)' in script
    assert 'signal: controller.signal' in script
    assert 'if (request !== state.historyRequest || el.history_drawer.hidden) return;' in script
    assert 'state.historyRequest += 1; if (state.historyController) state.historyController.abort(); state.historyController = null; state.historyLoading = false;' in script
    assert '"/api/assets"' in script
    assert 'IMAGE_MIME_TYPES' not in script
    assert 'event.isComposing' in script
    assert 'function openHistory()' in script
    assert 'function closeHistory()' in script
    assert 'function trapHistoryFocus(event)' in script
    assert 'event.key === "Escape"' in script
    assert 'el.app_shell.inert = true' in script
    assert 'el.app_shell.setAttribute("aria-hidden", "true")' in script
    assert 'el.app_shell.removeAttribute("aria-hidden")' in script
    assert 'item.availability === "AVAILABLE"' in script
    assert 'MISSING: "Missing from archive"' in script
    assert 'PENDING: "Pending"' in script
    assert 'item.availability === "AVAILABLE" && safeHistoryUrl(item.asset_url)' in script
    assert 'if (actions.childElementCount) row.append(actions)' in script
    assert 'Retry the failed item below.' in script
    assert 'failedCount ? 8000 : 4000' in script
    assert 'History couldn\'t be loaded. Try again.' in script
    assert "response.json()).detail" not in script
    assert 'min-width: 360px' in css
    assert 'min(88vw, 420px)' in css
    assert 'bottom: calc(118px + env(safe-area-inset-bottom))' in css
    assert 'white-space: pre-line' in css


def test_pairing_ui_displays_only_safe_address_and_warns_on_loopback() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "app" / "web" / "app.js").read_text(encoding="utf-8")
    html = (root / "app" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'api("/api/pairing")' in script
    assert 'id="pairing-address"' in html
    assert 'id="pairing-copy"' in html
    assert "info.pairing_display_url" in script
    assert "state.pairingUrl = info.pairing_url" in script
    assert 'el.pairing_address.textContent = info.pairing_url' not in script
    assert "network.loopback_only" in script
    assert "same Wi-Fi or VPN" in script
    assert "Windows Firewall" in script
    assert "Guest Wi-Fi" in script


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
