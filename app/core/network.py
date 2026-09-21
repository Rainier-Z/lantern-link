"""Helpers for discovering the host's LAN address and service URLs."""

from __future__ import annotations

import ipaddress
import socket


def is_usable_lan_ipv4(address: str) -> bool:
    """Return whether an address is suitable for a local-device pairing URL."""

    try:
        candidate = ipaddress.ip_address(address)
    except ValueError:
        return False

    benchmark_network = ipaddress.ip_network("198.18.0.0/15")
    return (
        candidate.version == 4
        and candidate.is_private
        and not candidate.is_loopback
        and not candidate.is_link_local
        and candidate not in benchmark_network
    )


def select_lan_ipv4(addresses: list[str]) -> str | None:
    """Return the first usable private IPv4 address from local interfaces."""

    return next((address for address in addresses if is_usable_lan_ipv4(address)), None)


def _local_ipv4_addresses() -> list[str]:
    """Return IPv4 addresses assigned to this computer's host name."""

    try:
        return list(
            dict.fromkeys(
                info[4][0]
                for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            )
        )
    except OSError:
        return []


def get_lan_ip() -> str:
    """Return a private IPv4 address suitable for a same-Wi-Fi device."""

    local_address = select_lan_ipv4(_local_ipv4_addresses())
    if local_address:
        return local_address

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
        if is_usable_lan_ipv4(address):
            return address
    except OSError:
        pass
    finally:
        sock.close()

    return "127.0.0.1"


def build_service_url(host: str, port: int, token: str | None = None) -> str:
    """Build an HTTP URL, optionally including the pairing token."""

    url = f"http://{host}:{port}/"
    if token:
        return f"{url}?token={token}"
    return url
