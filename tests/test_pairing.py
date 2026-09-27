import logging

from app.core.network import LanNetworkInfo
from app.core.security import get_access_token


def _network_info(
    selected_ip: str,
    source: str,
    candidates: tuple[str, ...],
    *,
    loopback_only: bool = False,
) -> LanNetworkInfo:
    return LanNetworkInfo(
        selected_ip=selected_ip,
        source=source,
        candidates=candidates,
        port=9527,
        loopback_only=loopback_only,
    )


def test_pairing_response_exposes_safe_display_url_and_network_info(
    client, auth_headers, app_module, monkeypatch
):
    network = _network_info(
        "192.168.1.23", "route", ("172.22.64.1", "192.168.1.23")
    )
    monkeypatch.setattr(app_module, "get_lan_network_info", lambda port: network)

    response = client.get("/api/pairing", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    token = get_access_token()
    assert body["pairing_url"] == (
        f"http://192.168.1.23:9527/?token={token}"
    )
    assert body["pairing_display_url"] == "http://192.168.1.23:9527/"
    assert "token=" not in body["pairing_display_url"]
    assert body["qr_image_url"] == "/api/pairing/qr"
    assert body["pairing_available"] is True
    assert body["network"] == {
        "selected_ip": "192.168.1.23",
        "source": "route",
        "port": 9527,
        "loopback_only": False,
    }


def test_loopback_pairing_does_not_generate_or_return_token_url(
    client, auth_headers, app_module, monkeypatch
):
    network = _network_info("127.0.0.1", "loopback", (), loopback_only=True)
    monkeypatch.setattr(app_module, "get_lan_network_info", lambda port: network)
    monkeypatch.setattr(
        app_module,
        "get_access_token",
        lambda: (_ for _ in ()).throw(AssertionError("token must not be read")),
    )

    response = client.get("/api/pairing", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["pairing_url"] == ""
    assert body["pairing_display_url"] == ""
    assert body["qr_image_url"] == "/api/pairing/qr"
    assert body["pairing_available"] is False
    assert body["network"] == {
        "selected_ip": "127.0.0.1",
        "source": "loopback",
        "port": 9527,
        "loopback_only": True,
    }


def test_loopback_pairing_qr_is_explicitly_unavailable_without_token(
    client, auth_headers, app_module, monkeypatch
):
    network = _network_info("127.0.0.1", "loopback", (), loopback_only=True)
    monkeypatch.setattr(app_module, "get_lan_network_info", lambda port: network)
    monkeypatch.setattr(
        app_module,
        "get_access_token",
        lambda: (_ for _ in ()).throw(AssertionError("token must not be read")),
    )
    monkeypatch.setattr(
        app_module,
        "generate_pairing_qr",
        lambda *args: (_ for _ in ()).throw(AssertionError("QR must not be generated")),
    )

    response = client.get("/api/pairing/qr", headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Pairing QR is unavailable because no LAN IPv4 address was found"
    }


def test_pairing_qr_uses_selected_route_address(
    client, auth_headers, app_module, monkeypatch
):
    network = _network_info("192.168.1.23", "route", ("192.168.1.23",))
    generated_for = []
    monkeypatch.setattr(app_module, "get_lan_network_info", lambda port: network)
    monkeypatch.setattr(
        app_module,
        "generate_pairing_qr",
        lambda selected: generated_for.append(selected) or b"png-bytes",
    )

    response = client.get("/api/pairing/qr", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"png-bytes"
    assert generated_for == [network]


def test_startup_logs_network_diagnostics_without_pairing_token(
    app_module, monkeypatch, caplog
):
    import uvicorn

    network = _network_info(
        "192.168.1.23", "route", ("172.22.64.1", "192.168.1.23")
    )
    monkeypatch.setattr(app_module, "get_lan_network_info", lambda port: network)
    monkeypatch.setattr(app_module, "generate_pairing_qr", lambda selected: b"png")
    monkeypatch.setattr(app_module.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    with caplog.at_level(logging.INFO, logger="app.main"):
        app_module.start_server()

    assert "Local: http://127.0.0.1:9527/" in caplog.text
    assert "LAN: http://192.168.1.23:9527/" in caplog.text
    assert "Pairing availability: available" in caplog.text
    assert "LAN source: route" in caplog.text
    assert "LAN candidates: 172.22.64.1, 192.168.1.23" in caplog.text
    assert get_access_token() not in caplog.text


def test_startup_warns_when_pairing_is_loopback_only(
    app_module, monkeypatch, caplog
):
    import uvicorn

    network = _network_info("127.0.0.1", "loopback", (), loopback_only=True)
    monkeypatch.setattr(app_module, "get_lan_network_info", lambda port: network)
    monkeypatch.setattr(
        app_module,
        "generate_pairing_qr",
        lambda *args: (_ for _ in ()).throw(AssertionError("QR must not be generated")),
    )
    monkeypatch.setattr(app_module.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    with caplog.at_level(logging.INFO, logger="app.main"):
        app_module.start_server()

    assert "Pairing availability: unavailable" in caplog.text
    assert "No usable LAN IPv4 address was found; iPhone pairing is unavailable" in caplog.text
