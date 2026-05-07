"""V0.15 cache infra improvement — TokenRotator smart_sleep + record_call tests.

Verifies:
- record_call() records first call timestamp per slot
- _smart_sleep_until_quota_reset() calculates correct wait based on
  earliest token reset (= first_call + 60min)
- Smart sleep capped at 65 min (safety cap to never wait beyond quota window)
- Smart sleep floored at 60 sec (don't tight-loop)
- All-tokens-exhausted reset clears slot_first_call_at + backup_proxies

Mock datetime.now() to test timing logic deterministically.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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


def test_record_call_records_first_call_timestamp(rotator, monkeypatch):
    """record_call() should record datetime.now() the FIRST time per slot only."""
    fake_now_1 = datetime(2026, 5, 5, 14, 0, 0)
    fake_now_2 = datetime(2026, 5, 5, 14, 0, 30)

    times = iter([fake_now_1, fake_now_2])
    monkeypatch.setattr("scripts.cache_rebuild.datetime",
                        type("FakeDT", (), {"now": staticmethod(lambda: next(times))}))

    rotator._current_slot = 0
    rotator.record_call()
    rotator.record_call()

    assert 0 in rotator._slot_first_call_at
    # First call timestamp should be 14:00:00, not 14:00:30
    assert rotator._slot_first_call_at[0] == fake_now_1
    assert rotator._calls_on_current == 2


def test_record_call_per_slot_independent(rotator, monkeypatch):
    """Switching slot creates new first_call entry, doesn't overwrite earlier."""
    times = iter([
        datetime(2026, 5, 5, 14, 0, 0),
        datetime(2026, 5, 5, 14, 2, 30),
    ])
    monkeypatch.setattr("scripts.cache_rebuild.datetime",
                        type("FakeDT", (), {"now": staticmethod(lambda: next(times))}))

    rotator._current_slot = 0
    rotator.record_call()
    rotator._current_slot = 1
    rotator.record_call()

    assert rotator._slot_first_call_at[0] == datetime(2026, 5, 5, 14, 0, 0)
    assert rotator._slot_first_call_at[1] == datetime(2026, 5, 5, 14, 2, 30)


def test_smart_sleep_calculates_wait_to_earliest_reset(rotator, monkeypatch):
    """V0.15 core: sleep until earliest token quota window resets.

    Setup mimics 14:45:56 incident:
      Token1@14:00 → resets 15:00
      Token2@14:02 → resets 15:02
      Token3@14:42 → resets 15:42
    "now" = 14:45:56 → earliest reset = 15:00 → sleep = ~14 min + 30s buffer.
    """
    rotator._slot_first_call_at = {
        0: datetime(2026, 5, 5, 14, 0, 0),
        1: datetime(2026, 5, 5, 14, 2, 0),
        2: datetime(2026, 5, 5, 14, 42, 0),
    }
    fake_now = datetime(2026, 5, 5, 14, 45, 56)

    monkeypatch.setattr("scripts.cache_rebuild.datetime",
                        type("FakeDT", (), {
                            "now": staticmethod(lambda: fake_now),
                        }))

    sleep_calls: list[float] = []
    monkeypatch.setattr("scripts.cache_rebuild._time.sleep",
                        lambda s: sleep_calls.append(s))

    rotator._smart_sleep_until_quota_reset()

    assert len(sleep_calls) == 1
    actual_sleep = sleep_calls[0]
    # Token1 resets at 15:00, "now"=14:45:56 → 14m4s = 844s + 30s buffer = ~874s
    expected_sleep = (15 - 14) * 3600 - (45 * 60 + 56) + 30  # = 874s
    assert abs(actual_sleep - expected_sleep) < 5, (
        f"Expected ~{expected_sleep}s, got {actual_sleep}s"
    )


def test_smart_sleep_returns_floor_when_already_past(rotator, monkeypatch):
    """If now > all reset times, sleep returns 60s floor (don't tight-loop)."""
    rotator._slot_first_call_at = {
        0: datetime(2026, 5, 5, 14, 0, 0),  # resets 15:00
        1: datetime(2026, 5, 5, 14, 2, 0),  # resets 15:02
    }
    fake_now = datetime(2026, 5, 5, 15, 18, 0)  # already past all resets

    monkeypatch.setattr("scripts.cache_rebuild.datetime",
                        type("FakeDT", (), {"now": staticmethod(lambda: fake_now)}))

    sleep_calls: list[float] = []
    monkeypatch.setattr("scripts.cache_rebuild._time.sleep",
                        lambda s: sleep_calls.append(s))

    rotator._smart_sleep_until_quota_reset()
    assert sleep_calls[0] == 60.0, f"Expected 60s floor, got {sleep_calls[0]}"


def test_smart_sleep_capped_at_65_min(rotator, monkeypatch):
    """If first_call is far future (impossible but safety cap), sleep capped."""
    rotator._slot_first_call_at = {
        0: datetime(2026, 5, 5, 16, 0, 0),  # resets 17:00 (future)
    }
    fake_now = datetime(2026, 5, 5, 14, 0, 0)  # before first call?? clock skew

    monkeypatch.setattr("scripts.cache_rebuild.datetime",
                        type("FakeDT", (), {"now": staticmethod(lambda: fake_now)}))

    sleep_calls: list[float] = []
    monkeypatch.setattr("scripts.cache_rebuild._time.sleep",
                        lambda s: sleep_calls.append(s))

    rotator._smart_sleep_until_quota_reset()
    # Cap at QUOTA_WINDOW_MIN + 5 = 65 min = 3900s
    assert sleep_calls[0] <= 65 * 60 + 1, f"Expected ≤ 65 min cap, got {sleep_calls[0]}s"


def test_smart_sleep_no_first_call_uses_fallback(rotator, monkeypatch):
    """If _slot_first_call_at empty, fallback 60-min wait."""
    rotator._slot_first_call_at = {}

    sleep_calls: list[float] = []
    monkeypatch.setattr("scripts.cache_rebuild._time.sleep",
                        lambda s: sleep_calls.append(s))

    rotator._smart_sleep_until_quota_reset()
    # Fallback = QUOTA_WINDOW_MIN * 60 = 3600s
    assert sleep_calls[0] == 3600, f"Expected 3600s fallback, got {sleep_calls[0]}"


def test_smart_sleep_timestamps_reset_on_full_cycle(rotator, monkeypatch):
    """After full all-exhausted reset, _slot_first_call_at + backup_proxies cleared."""
    # Simulate state at all-exhausted moment
    rotator._slot_first_call_at = {
        0: datetime(2026, 5, 5, 14, 0, 0),
        1: datetime(2026, 5, 5, 14, 2, 0),
        2: datetime(2026, 5, 5, 14, 42, 0),
    }
    rotator._backup_proxies = ["socks5://x", "socks5://y"]
    rotator._current_slot = 2
    rotator._slots = [
        ("tok1", None),
        ("tok2", "socks5://p2"),
        ("tok3", "socks5://p3"),
    ]
    rotator._calls_on_current = rotator.QUOTA_PER_SLOT  # exhausted

    # Stub out the actual sleep + loader rebuild to avoid network
    monkeypatch.setattr("scripts.cache_rebuild._time.sleep", lambda s: None)
    monkeypatch.setattr(
        "scripts.cache_rebuild.TokenRotator._make_loader",
        lambda self: setattr(self, "_loader", "stub_loader"),
    )
    monkeypatch.setattr("scripts.cache_rebuild.datetime",
                        type("FakeDT", (), {
                            "now": staticmethod(lambda: datetime(2026, 5, 5, 15, 18, 0)),
                        }))

    rotator.get_loader()  # triggers all-exhausted branch

    # Post-reset state
    assert rotator._slot_first_call_at == {}, "Timestamps should be cleared"
    assert rotator._backup_proxies == [], "Backup pool should be cleared"
    assert rotator._current_slot == 0, "Should restart from slot 0"
    assert rotator._slots == [(rotator._tokens[0], None)], "Slots reset to Direct"
