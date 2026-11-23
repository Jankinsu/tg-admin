import pytest

from tg_admin.__main__ import build_application, read_proxy


def test_proxy_is_optional(monkeypatch):
    monkeypatch.delenv("TG_ADMIN_PROXY", raising=False)
    assert read_proxy() is None


@pytest.mark.parametrize("proxy", ["http://127.0.0.1:2080", "socks5://127.0.0.1:2080"])
def test_proxy_valid(monkeypatch, proxy):
    monkeypatch.setenv("TG_ADMIN_PROXY", proxy)
    assert read_proxy() == proxy


@pytest.mark.parametrize("proxy", ["file:///tmp/x", "socks5://127.0.0.1", "http://", "abc"])
def test_proxy_invalid(monkeypatch, proxy):
    monkeypatch.setenv("TG_ADMIN_PROXY", proxy)
    with pytest.raises(ValueError):
        read_proxy()


def test_application_builds_with_proxy():
    # Building the application does not initiate a network call.
    app = build_application("123456:TEST_TOKEN", "socks5://127.0.0.1:2080")
    assert app is not None
