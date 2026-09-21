from app.core.network import is_usable_lan_ipv4, select_lan_ipv4


def test_proxy_benchmark_range_is_not_treated_as_a_lan_address():
    assert not is_usable_lan_ipv4("198.18.0.1")
    assert is_usable_lan_ipv4("172.16.10.2")


def test_select_lan_ipv4_ignores_proxy_adapter_address():
    addresses = ["198.18.0.1", "172.16.10.2"]

    assert select_lan_ipv4(addresses) == "172.16.10.2"
