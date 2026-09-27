"""Helpers for discovering the host's LAN address and service URLs."""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Literal

_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_ROUTE_PROBE_ADDRESS = ("8.8.8.8", 80)
_ROUTE_PROBE_TIMEOUT_SECONDS = 0.5
_DEFAULT_PORT = 9527


@dataclass(frozen=True)
class LanNetworkInfo:
    """Immutable details about the address selected for a pairing URL."""

    selected_ip: str
    source: Literal["override", "route", "candidate", "loopback"]
    candidates: tuple[str, ...]
    port: int
    loopback_only: bool


def is_usable_lan_ipv4(address: str) -> bool:
    """Return whether an address is a usable RFC 1918 host IPv4 address."""

    try:
        candidate = ipaddress.ip_address(address)
    except ValueError:
        return False

    return (
        candidate.version == 4
        and candidate not in _BENCHMARK_NETWORK
        and any(candidate in network for network in _RFC1918_NETWORKS)
        and not candidate.is_loopback
        and not candidate.is_link_local
        and not candidate.is_unspecified
        and not candidate.is_multicast
        and not candidate.is_reserved
    )


def select_lan_ipv4(addresses: list[str]) -> str | None:
    """Return the first usable private IPv4 address from local interfaces."""

    return next((address for address in addresses if is_usable_lan_ipv4(address)), None)


def get_route_ipv4() -> str | None:
    """Return the usable IPv4 selected by the operating system's default route."""

    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(_ROUTE_PROBE_TIMEOUT_SECONDS)
        sock.connect(_ROUTE_PROBE_ADDRESS)
        address = sock.getsockname()[0]
        return address if is_usable_lan_ipv4(address) else None
    except OSError:
        return None
    finally:
        if sock is not None:
            sock.close()


def get_local_ipv4_candidates() -> list[str]:
    """Return usable hostname IPv4 candidates in resolver order, without duplicates."""

    try:
        addresses = (
            info[4][0]
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except OSError:
        return []

    return list(dict.fromkeys(address for address in addresses if is_usable_lan_ipv4(address)))


def get_lan_network_info(port: int = _DEFAULT_PORT) -> LanNetworkInfo:
    """Select a pairing address, preferring override, route, hostname, then loopback."""

    override = os.environ.get("PRIVATE_SEND_LAN_IP")
    if override is not None:
        normalized = override.strip()
        if not is_usable_lan_ipv4(normalized):
            raise ValueError("PRIVATE_SEND_LAN_IP must be a usable private IPv4 address")
        candidates = tuple(get_local_ipv4_candidates())
        return LanNetworkInfo(normalized, "override", candidates, port, False)

    route_address = get_route_ipv4()
    candidates = tuple(get_local_ipv4_candidates())
    if route_address:
        return LanNetworkInfo(route_address, "route", candidates, port, False)

    candidate_address = select_lan_ipv4(candidates)
    if candidate_address:
        return LanNetworkInfo(candidate_address, "candidate", candidates, port, False)

    return LanNetworkInfo("127.0.0.1", "loopback", candidates, port, True)


def get_lan_ip() -> str:
    """Return the selected IPv4 address suitable for a same-network device."""

    return get_lan_network_info().selected_ip


def build_service_url(host: str, port: int, token: str | None = None) -> str:
    """Build an HTTP URL, optionally including the pairing token."""

    url = f"http://{host}:{port}/"
    if token:
        return f"{url}?token={token}"
    return url
