"""V0.17 connection-resilient _make_loader tests.

Trigger: V0.15 hot-swap pulled `socks5://104.200.152.30:4145` from backup
pool, but proxy was TCP-alive yet HTTPS-dead → loader.login_by_token() raised
uncaught ConnectionError → process crash at 16:16:23.

V0.17 fix:
- _make_loader wraps login_by_token in try/except
- On connection failure, drains backup_proxies and retries
- If all backups exhausted, raises ConnectionError → caller rotates token

Tests verify:
- Original proxy works → success on first try
- Original proxy fails + backup works → swap + success (drain that backup)
- All proxies fail → raises ConnectionError
- Non-connection exception → re-raised (real bugs not swallowed)
- get_loader() catches all-fail and force-advances token slot
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def rotator(monkeypatch):
    """Build TokenRotator with 3 mock tokens."""
    monkeypatch.setenv("FINMIND_TOKEN", "tok1_xxx")
    monkeypatch.setenv("FINMIND_TOKEN2", "tok2_xxx")
    monkeypatch.setenv("FINMIND_TOKEN3", "tok3_xxx")
    from scripts.cache_rebuild import TokenRotator
    return TokenRotator()


def _fake_dataloader_factory(login_results: list):
    """Factory that returns a DataLoader stub raising/succeeding per call.

    login_results: list of either Exception instance (login fails) or None (success).
    Each DataLoader() invocation pops one result from the queue.
    """
    results_iter = iter(login_results)

    def factory():
        loader = MagicMock()
        # Stub the proxy session attribute access loader._FinMindApi__session.proxies.update
        loader._FinMindApi__session = MagicMock()
        result = next(results_iter)
        if isinstance(result, Exception):
            loader.login_by_token = MagicMock(side_effect=result)
        else:
            loader.login_by_token = MagicMock(return_value=None)
        return loader

    return factory


def test_make_loader_original_proxy_works(rotator, monkeypatch):
    """Original proxy login succeeds → no fallback needed."""
    rotator._slots[0] = ("tok1", "socks5://orig:1080")
    rotator._backup_proxies = ["socks5://backup1:1080", "socks5://backup2:1080"]

    with patch("FinMind.data.DataLoader",
               side_effect=_fake_dataloader_factory([None])):
        rotator._make_loader()

    assert rotator._loader is not None
    assert rotator._current_proxy == "socks5://orig:1080"
    # Backup pool unchanged (no fallback consumed)
    assert rotator._backup_proxies == ["socks5://backup1:1080", "socks5://backup2:1080"]


def test_make_loader_falls_over_to_backup_on_connection_error(rotator, monkeypatch):
    """Original proxy ConnectionError → fall over to first backup.

    V0.17 core test: this is exactly the 16:16:23 crash scenario.
    """
    rotator._slots[0] = ("tok1", "socks5://dead:1080")
    rotator._backup_proxies = ["socks5://alive_backup:1080", "socks5://backup2:1080"]

    with patch("FinMind.data.DataLoader",
               side_effect=_fake_dataloader_factory([
                   ConnectionError("Max retries exceeded"),  # original fails
                   None,  # backup1 succeeds
               ])):
        rotator._make_loader()

    assert rotator._loader is not None
    assert rotator._current_proxy == "socks5://alive_backup:1080"
    # Backup we used should be removed from pool
    assert "socks5://alive_backup:1080" not in rotator._backup_proxies
    assert "socks5://backup2:1080" in rotator._backup_proxies
    # _slots[0] updated to new working proxy
    assert rotator._slots[0] == ("tok1", "socks5://alive_backup:1080")


def test_make_loader_all_proxies_fail_raises(rotator, monkeypatch):
    """If original + all backups + Direct fallback all fail → raise.

    V0.19 (2026-05-05) added Direct (None) fallback after all proxies fail,
    so we mock 4 attempts: 3 proxies + Direct, all dead.
    """
    rotator._slots[0] = ("tok1", "socks5://dead1:1080")
    rotator._backup_proxies = ["socks5://dead2:1080", "socks5://dead3:1080"]

    with patch("FinMind.data.DataLoader",
               side_effect=_fake_dataloader_factory([
                   ConnectionError("dead 1"),
                   ConnectionError("dead 2"),
                   ConnectionError("dead 3"),
                   ConnectionError("Direct also dead"),  # V0.19 Direct fallback
               ])):
        with pytest.raises(ConnectionError, match="all .* proxy attempts failed"):
            rotator._make_loader()


def test_make_loader_direct_fallback_when_all_proxies_ssl_fail(rotator, monkeypatch):
    """V0.19: if all proxies fail (e.g. SSL self-signed cert pool), fall back to Direct.

    User explicit request 2026-05-05 22:05: "掛掉時請用本機IP". Trade-off
    accepted: 3 tokens sharing workstation IP risks throttling but beats
    smart_sleep cycle on unusable Proxifly pool.
    """
    rotator._slots[0] = ("tok1", "socks5://dead_ssl:1080")
    rotator._backup_proxies = ["socks5://dead_backup1:1080"]

    with patch("FinMind.data.DataLoader",
               side_effect=_fake_dataloader_factory([
                   Exception("SSL: CERTIFICATE_VERIFY_FAILED"),  # original
                   Exception("SSL: CERTIFICATE_VERIFY_FAILED"),  # backup 1
                   None,  # Direct fallback succeeds
               ])):
        rotator._make_loader()

    # Should now be using Direct (proxy=None)
    assert rotator._current_proxy is None
    assert rotator._slots[0] == ("tok1", None)


def test_make_loader_max_retry_error_treated_as_connection_failure(rotator):
    """Max retries / Timeout / SSL errors all trigger fall-over (V0.17 broad catch)."""
    rotator._slots[0] = ("tok1", "socks5://orig:1080")
    rotator._backup_proxies = ["socks5://backup:1080"]

    with patch("FinMind.data.DataLoader",
               side_effect=_fake_dataloader_factory([
                   Exception("Max retries exceeded with url: /v2/user_info"),
                   None,
               ])):
        rotator._make_loader()

    assert rotator._current_proxy == "socks5://backup:1080"


def test_make_loader_non_connection_exception_reraised(rotator):
    """V0.17 invariant: real bugs (not connection issues) must NOT be swallowed."""
    rotator._slots[0] = ("tok1", "socks5://orig:1080")
    rotator._backup_proxies = []

    class WeirdBug(Exception):
        pass

    with patch("FinMind.data.DataLoader",
               side_effect=_fake_dataloader_factory([
                   WeirdBug("some real bug like KeyError or TypeError"),
               ])):
        with pytest.raises(WeirdBug, match="some real bug"):
            rotator._make_loader()


def test_get_loader_catches_make_loader_connection_error(rotator, monkeypatch):
    """V0.17: when _make_loader raises ConnectionError on first call,
    get_loader force-advances to next slot via QUOTA_PER_SLOT trick.
    """
    rotator._slots[0] = ("tok1", "socks5://dead:1080")
    rotator._backup_proxies = []
    rotator._loader = None
    rotator._calls_on_current = 0

    def make_loader_raises():
        raise ConnectionError("simulated all-fail")

    monkeypatch.setattr(rotator, "_make_loader", make_loader_raises)
    # Stub _build_remaining_slots so it doesn't actually fetch proxies
    monkeypatch.setattr(rotator, "_build_remaining_slots", lambda: None)
    # Stub the eventual second _make_loader call after slot rotation
    rebuild_called = []
    def stub_rebuild():
        rebuild_called.append(True)
        rotator._loader = "stub_loader"

    # Pre-populate slot 1 so rotation doesn't try to build
    rotator._slots.append(("tok2", None))

    # Replace _make_loader after the first failure
    call_count = [0]
    def alternating(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("simulated all-fail")
        rotator._loader = "stub_loader_after_rotate"

    monkeypatch.setattr(rotator, "_make_loader", alternating)

    rotator.get_loader()

    # After force-advance + rotation to slot 1, loader should exist
    assert rotator._current_slot == 1
    assert rotator._loader == "stub_loader_after_rotate"
