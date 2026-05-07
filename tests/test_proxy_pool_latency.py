"""V0.15 cache infra improvement — ProxyPool latency benchmark + backup pool tests.

Verifies:
- _verify_proxy_with_latency returns numeric latency on success / None on fail
- _fetch_working_proxy returns the FASTEST verified proxy (not first-OK)
- Backup pool populated with next BACKUP_POOL_SIZE proxies sorted by latency
- get_backup_proxy() pops from backup pool, returns None when empty
- patch_current_proxy() preserves _calls_on_current (token quota not reset)

Mock requests.get with patched latencies via monkeypatch to ensure determinism
without network dependency. Avoids hitting Proxifly or api.ipify.org.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def rotator(monkeypatch):
    """Build TokenRotator with 3 mock tokens (env vars patched)."""
    monkeypatch.setenv("FINMIND_TOKEN", "tok1_xxx")
    monkeypatch.setenv("FINMIND_TOKEN2", "tok2_xxx")
    monkeypatch.setenv("FINMIND_TOKEN3", "tok3_xxx")
    from scripts.cache_rebuild import TokenRotator
    return TokenRotator()


def test_verify_proxy_with_latency_success(rotator, monkeypatch):
    """Returns elapsed seconds on 200 OK."""
    class FakeResp:
        status_code = 200
        def json(self):
            return {"ip": "1.2.3.4"}

    times = iter([100.0, 100.5])  # t0=100, t1=100.5 → 0.5s
    monkeypatch.setattr("time.monotonic", lambda: next(times))
    monkeypatch.setattr("scripts.cache_rebuild._time.monotonic",
                        lambda: next(times) if False else 100.5, raising=False)

    with patch("requests.get", return_value=FakeResp()):
        latency = rotator._verify_proxy_with_latency("socks5://1.2.3.4:1080")
    assert latency is not None
    assert 0 <= latency < 5.0


def test_verify_proxy_with_latency_failure(rotator):
    """Returns None on connection failure."""
    with patch("requests.get", side_effect=ConnectionError("dead proxy")):
        latency = rotator._verify_proxy_with_latency("socks5://9.9.9.9:9999")
    assert latency is None


def test_fetch_working_proxy_returns_fastest(rotator, monkeypatch):
    """V0.15 core test: among 3 verified proxies, return the one with lowest latency."""
    proxy_list_text = (
        "socks5://1.1.1.1:1080\n"
        "socks5://2.2.2.2:1080\n"
        "socks5://3.3.3.3:1080\n"
        "socks5://4.4.4.4:1080\n"
        "socks5://5.5.5.5:1080\n"
    )

    class FakeListResp:
        text = proxy_list_text

    # Map proxy_url → latency. socks5://3.3.3.3 is fastest (0.5s).
    latency_map = {
        "socks5://1.1.1.1:1080": 2.0,
        "socks5://2.2.2.2:1080": None,  # fail
        "socks5://3.3.3.3:1080": 0.5,   # FASTEST
        "socks5://4.4.4.4:1080": 1.0,
        "socks5://5.5.5.5:1080": 3.0,
    }

    def fake_verify(self, proxy: str):
        return latency_map.get(proxy)

    monkeypatch.setattr("scripts.cache_rebuild.TokenRotator._verify_proxy_with_latency",
                        fake_verify)
    # Force shuffle to keep deterministic order (no shuffle).
    monkeypatch.setattr("random.shuffle", lambda x: None)

    with patch("requests.get", return_value=FakeListResp()):
        winner = rotator._fetch_working_proxy()

    assert winner == "socks5://3.3.3.3:1080", f"Expected fastest, got {winner}"


def test_fetch_working_proxy_populates_backup_pool(rotator, monkeypatch):
    """Backup pool gets the next BACKUP_POOL_SIZE fastest after the primary."""
    proxy_list_text = (
        "socks5://1.1.1.1:1080\n"
        "socks5://2.2.2.2:1080\n"
        "socks5://3.3.3.3:1080\n"
        "socks5://4.4.4.4:1080\n"
        "socks5://5.5.5.5:1080\n"
    )
    class FakeListResp:
        text = proxy_list_text

    latency_map = {
        "socks5://1.1.1.1:1080": 1.5,  # 2nd fastest
        "socks5://2.2.2.2:1080": 4.0,  # 5th
        "socks5://3.3.3.3:1080": 0.5,  # 1st (primary)
        "socks5://4.4.4.4:1080": 2.0,  # 3rd
        "socks5://5.5.5.5:1080": 3.0,  # 4th
    }

    monkeypatch.setattr("scripts.cache_rebuild.TokenRotator._verify_proxy_with_latency",
                        lambda self, p: latency_map.get(p))
    monkeypatch.setattr("random.shuffle", lambda x: None)

    with patch("requests.get", return_value=FakeListResp()):
        winner = rotator._fetch_working_proxy()

    assert winner == "socks5://3.3.3.3:1080"
    # Backup pool should have next 3 sorted by latency: 1.5 / 2.0 / 3.0
    assert rotator._backup_proxies == [
        "socks5://1.1.1.1:1080",  # 1.5s
        "socks5://4.4.4.4:1080",  # 2.0s
        "socks5://5.5.5.5:1080",  # 3.0s
    ]


def test_fetch_working_proxy_returns_none_when_all_fail(rotator, monkeypatch):
    proxy_list_text = "socks5://1.1.1.1:1080\nsocks5://2.2.2.2:1080\n"
    class FakeListResp:
        text = proxy_list_text

    monkeypatch.setattr("scripts.cache_rebuild.TokenRotator._verify_proxy_with_latency",
                        lambda self, p: None)
    monkeypatch.setattr("random.shuffle", lambda x: None)

    with patch("requests.get", return_value=FakeListResp()):
        result = rotator._fetch_working_proxy()
    assert result is None
    assert rotator._backup_proxies == []


def test_get_backup_proxy_pops_then_returns_none(rotator):
    rotator._backup_proxies = ["socks5://a", "socks5://b"]
    assert rotator.get_backup_proxy() == "socks5://a"
    assert rotator.get_backup_proxy() == "socks5://b"
    assert rotator.get_backup_proxy() is None


def test_patch_current_proxy_preserves_calls_on_current(rotator):
    """V0.15 hot-swap: patching proxy must NOT reset _calls_on_current.

    This is the core invariant: token quota window keeps counting; we only
    swap the proxy (HTTP path) without consuming a slot.
    """
    rotator._calls_on_current = 250
    rotator._slots[0] = ("tok1_xxx", "socks5://old:1080")
    rotator._loader = "fake_loader"  # placeholder

    rotator.patch_current_proxy("socks5://new:1080")

    assert rotator._slots[0] == ("tok1_xxx", "socks5://new:1080")
    assert rotator._current_proxy == "socks5://new:1080"
    assert rotator._calls_on_current == 250, "calls_on_current MUST be preserved"
    assert rotator._loader is None, "loader must be invalidated for rebuild"


def test_calls_on_current_property(rotator):
    """V0.15 public property exposes calls_on_current for hot-swap floor check."""
    rotator._calls_on_current = 42
    assert rotator.calls_on_current == 42
