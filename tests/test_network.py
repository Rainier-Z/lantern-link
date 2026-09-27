from dataclasses import FrozenInstanceError

import pytest

from app.core import network
from app.core.network import (
    LanNetworkInfo,
    get_lan_network_info,
    get_local_ipv4_candidates,
    get_route_ipv4,
    is_usable_lan_ipv4,
    select_lan_ipv4,
)


def test_usable_lan_ipv4_accepts_only_rfc1918_unicast_addresses():
    assert is_usable_lan_ipv4("172.16.10.2")
    assert is_usable_lan_ipv4("192.168.1.12")
    assert not is_usable_lan_ipv4("8.8.8.8")
    assert not is_usable_lan_ipv4("127.0.0.1")
    assert not is_usable_lan_ipv4("169.254.10.4")
    assert not is_usable_lan_ipv4("198.18.0.1")
    assert not is_usable_lan_ipv4("2001:db8::1")


def test_select_lan_ipv4_ignores_proxy_adapter_address():
    addresses = ["198.18.0.1", "172.16.10.2"]

    assert select_lan_ipv4(addresses) == "172.16.10.2"


def test_get_route_ipv4_uses_short_udp_probe_and_always_closes_socket(monkeypatch):
    class FakeSocket:
        timeout = None
        destination = None
        closed = False

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, destination):
            self.destination = destination

        def getsockname(self):
            return ("192.168.1.20", 54321)

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    monkeypatch.setattr(network.socket, "socket", lambda *args: fake_socket)

    assert get_route_ipv4() == "192.168.1.20"
    assert fake_socket.timeout == 0.5
    assert fake_socket.destination == ("8.8.8.8", 80)
    assert fake_socket.closed


def test_get_route_ipv4_rejects_non_private_route_and_closes_socket(monkeypatch):
    class FakeSocket:
        closed = False

        def settimeout(self, timeout):
            pass

        def connect(self, destination):
            pass

        def getsockname(self):
            return ("8.8.8.8", 54321)

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    monkeypatch.setattr(network.socket, "socket", lambda *args: fake_socket)

    assert get_route_ipv4() is None
    assert fake_socket.closed


def test_local_ipv4_candidates_filter_and_deduplicate_resolver_results(monkeypatch):
    results = [
        (None, None, None, None, (address, 0))
        for address in ("198.18.0.1", "192.168.1.5", "192.168.1.5", "8.8.8.8")
    ]
    monkeypatch.setattr(network.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(network.socket, "getaddrinfo", lambda *args: results)

    assert get_local_ipv4_candidates() == ["192.168.1.5"]


def test_route_selected_ip_precedes_hostname_candidates(monkeypatch):
    monkeypatch.delenv("LANTERN_LINK_LAN_IP", raising=False)
    monkeypatch.setattr(network, "get_local_ipv4_candidates", lambda: ["192.168.56.1"])
    monkeypatch.setattr(network, "get_route_ipv4", lambda: "10.0.0.24")

    info = get_lan_network_info(port=9876)

    assert info.selected_ip == "10.0.0.24"
    assert info.source == "route"
    assert info.candidates == ("192.168.56.1",)
    assert info.port == 9876
    assert not info.loopback_only


def test_hostname_candidate_is_fallback_when_route_is_unavailable(monkeypatch):
    monkeypatch.delenv("LANTERN_LINK_LAN_IP", raising=False)
    monkeypatch.setattr(network, "get_local_ipv4_candidates", lambda: ["192.168.1.9"])
    monkeypatch.setattr(network, "get_route_ipv4", lambda: None)

    info = get_lan_network_info()

    assert info.selected_ip == "192.168.1.9"
    assert info.source == "candidate"
    assert not info.loopback_only


def test_loopback_is_used_only_when_no_lan_candidate_exists(monkeypatch):
    monkeypatch.delenv("LANTERN_LINK_LAN_IP", raising=False)
    monkeypatch.setattr(network, "get_local_ipv4_candidates", lambda: [])
    monkeypatch.setattr(network, "get_route_ipv4", lambda: None)

    info = get_lan_network_info()

    assert info.selected_ip == "127.0.0.1"
    assert info.source == "loopback"
    assert info.loopback_only


def test_valid_override_has_highest_priority(monkeypatch):
    monkeypatch.setenv("LANTERN_LINK_LAN_IP", " 192.168.10.8 ")
    monkeypatch.setattr(network, "get_local_ipv4_candidates", lambda: ["192.168.1.9"])
    monkeypatch.setattr(
        network,
        "get_route_ipv4",
        lambda: pytest.fail("route probing must not override explicit configuration"),
    )

    info = get_lan_network_info()

    assert info == LanNetworkInfo(
        selected_ip="192.168.10.8",
        source="override",
        candidates=("192.168.1.9",),
        port=9527,
        loopback_only=False,
    )


@pytest.mark.parametrize(
    "override",
    ["8.8.8.8", "127.0.0.1", "169.254.1.3", "198.18.0.1", "2001:db8::1", "invalid", ""],
)
def test_invalid_override_is_rejected_instead_of_silently_ignored(monkeypatch, override):
    monkeypatch.setenv("LANTERN_LINK_LAN_IP", override)
    monkeypatch.setattr(network, "get_local_ipv4_candidates", lambda: [])
    monkeypatch.setattr(
        network,
        "get_route_ipv4",
        lambda: pytest.fail("invalid explicit configuration must be reported"),
    )

    with pytest.raises(ValueError, match="LANTERN_LINK_LAN_IP"):
        get_lan_network_info()


def test_lan_network_info_is_immutable():
    info = LanNetworkInfo("192.168.1.2", "candidate", (), 9527, False)

    with pytest.raises(FrozenInstanceError):
        info.selected_ip = "192.168.1.3"
