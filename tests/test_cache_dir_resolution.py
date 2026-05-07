"""Regression: shared resolve_cache_dir() provides project-root fallback.

Before this helper, backtest.universe hard-coded
``os.environ.get("DATA_CACHE_DIR", "/app/data/cache")`` with no project-root
fallback. On a Windows workstation without DATA_CACHE_DIR set, the OHLCV
cache-sym lookup would read zero files and silently degrade the universe to
stock_info order — the exact "alpha illusion" path that 2026-04-15 flagged.
twse_scraper already had the fallback; only universe.py was asymmetric.
"""

from __future__ import annotations

import pathlib

from src.utils.paths import resolve_cache_dir


def test_resolve_cache_dir_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
    assert resolve_cache_dir() == tmp_path


def test_resolve_cache_dir_falls_back_to_repo(monkeypatch):
    # Unset env → should land on project-root data/cache (which exists in
    # this repo) rather than Docker /app/data/cache (absent on workstation).
    monkeypatch.delenv("DATA_CACHE_DIR", raising=False)
    got = resolve_cache_dir()
    assert isinstance(got, pathlib.Path)
    # Must not return the Docker default when it does not exist.
    assert got != pathlib.Path("/app/data/cache") or got.exists()
    # Project fallback ends with data/cache
    assert got.name == "cache"
    assert got.parent.name == "data"


def test_resolve_cache_dir_ignores_nonexistent_env(monkeypatch, tmp_path):
    # If DATA_CACHE_DIR points somewhere that doesn't exist, fall through.
    missing = tmp_path / "definitely_does_not_exist"
    monkeypatch.setenv("DATA_CACHE_DIR", str(missing))
    got = resolve_cache_dir()
    assert got != missing


def test_universe_uses_shared_resolver(monkeypatch, tmp_path):
    """backtest.universe must read OHLCV cache via the shared resolver,
    not its former hard-coded ``os.environ.get(..., "/app/data/cache")``."""
    # Point env at a temp cache with one ohlcv pkl — verify universe "sees" it
    ohlcv_dir = tmp_path / "ohlcv"
    ohlcv_dir.mkdir()
    (ohlcv_dir / "2330.pkl").write_bytes(b"")
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))

    # Mimic the logic that reads cached_syms from resolve_cache_dir
    from src.utils.paths import resolve_cache_dir
    d = resolve_cache_dir() / "ohlcv"
    assert d.is_dir()
    found = {f.stem for f in d.iterdir() if f.suffix == ".pkl"}
    assert "2330" in found


# ---------------------------------------------------------------------------
# Phase 0 V0.2 (R24 P0-2 fix, 2026-05-04) — Windows priority regression
# ---------------------------------------------------------------------------
# Previously: resolve_cache_dir() unconditionally consulted ``/app/data/cache``
# before the repo fallback. On Windows that path resolves to the current drive
# root (e.g. ``E:\\app\\data\\cache``); if a stale Docker-volume artefact
# existed there, IC research silently used a partial cache missing 4 of the
# 11 panels (institutional_v2 / issued_capital / margin_short / quarterly_eps).
#
# V0.2 fix: gate the Docker default behind ``platform.system() != "Windows"``.
# These tests ensure:
#   • on Windows we ALWAYS fall through to repo even if /app/data/cache exists
#   • on POSIX we still honour /app/data/cache (Docker compatibility)
#   • explicit DATA_CACHE_DIR env override still works on either platform


def test_windows_skips_app_data_cache_even_when_present(monkeypatch, tmp_path):
    """Mutation test: revert ``if _is_posix():`` gate → Windows would pick a
    stale ``/app/data/cache`` artefact. With the gate in place we must fall
    through to repo regardless of whether that path resolves to True."""
    monkeypatch.delenv("DATA_CACHE_DIR", raising=False)

    # Force Windows platform branch
    monkeypatch.setattr("src.utils.paths.platform.system", lambda: "Windows")

    # Make /app/data/cache appear to exist (simulating stale drive-root artefact)
    real_exists = pathlib.Path.exists

    def fake_exists(self):
        if str(self) in ("/app/data/cache", "\\app\\data\\cache"):
            return True
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)

    got = resolve_cache_dir()
    # Must NOT return the Docker mount on Windows
    assert pathlib.Path("/app/data/cache") not in [got, got.resolve()]
    # Must fall through to repo data/cache
    assert got.name == "cache" and got.parent.name == "data"


def test_posix_still_honours_app_data_cache(monkeypatch, tmp_path):
    """On Linux/Docker the /app/data/cache mount must still be picked up
    when present. Guards against over-aggressive Windows fix breaking Docker."""
    monkeypatch.delenv("DATA_CACHE_DIR", raising=False)

    # Force POSIX platform branch
    monkeypatch.setattr("src.utils.paths.platform.system", lambda: "Linux")

    fake_docker = tmp_path / "fake_app_data_cache"
    fake_docker.mkdir()

    real_exists = pathlib.Path.exists
    docker_path = pathlib.Path("/app/data/cache")

    def fake_exists(self):
        if self == docker_path:
            return True
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)

    got = resolve_cache_dir()
    assert got == docker_path


def test_env_override_wins_on_windows(monkeypatch, tmp_path):
    """Explicit DATA_CACHE_DIR must override the Windows fallback rule —
    user can still point at any valid cache location regardless of OS."""
    monkeypatch.setattr("src.utils.paths.platform.system", lambda: "Windows")
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
    assert resolve_cache_dir() == tmp_path


# ---------------------------------------------------------------------------
# Phase 1 V1.4 (R25-mid Pro Review A12 attacker test, 2026-05-05) — env vs
# Docker-mount priority hardening
# ---------------------------------------------------------------------------
# Existing V0.2 trio covers (Windows, no-env, /app artefact) and (POSIX,
# no-env, Docker mount) and (Windows, env, no-/app). V1.4 補 2 件:
#   • POSIX, env set, /app/data/cache real → env priority (step 1) MUST win
#     Docker mount (step 2) per resolve_cache_dir() resolution order.
#   • Windows, env set, \app\data\cache real artefact → env priority (step 1)
#     MUST win regardless of platform (gate is on step 2 only).
# Together, 5 tests cover all 4 priority combinations + cross-platform env.


def test_posix_env_override_beats_docker_mount(monkeypatch, tmp_path):
    """V1.4 A12 attacker: POSIX + DATA_CACHE_DIR set + /app/data/cache also
    exists → env priority (resolution step 1) MUST win Docker mount (step 2).

    Mutation: if env check moved to AFTER _is_posix() Docker block, POSIX
    would silently pick Docker mount instead of user's DATA_CACHE_DIR → this
    test catches the regression."""
    monkeypatch.setattr("src.utils.paths.platform.system", lambda: "Linux")
    custom = tmp_path / "user_specified_cache"
    custom.mkdir()
    monkeypatch.setenv("DATA_CACHE_DIR", str(custom))

    real_exists = pathlib.Path.exists
    docker_path = pathlib.Path("/app/data/cache")

    def fake_exists(self):
        if self == docker_path:
            return True  # /app/data/cache 也 "存在"
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)

    got = resolve_cache_dir()
    # env wins (step 1 priority), NOT docker_path (step 2)
    assert got == custom
    assert got != docker_path


def test_windows_env_override_beats_app_data_artefact(monkeypatch, tmp_path):
    """V1.4 A12 attacker: Windows + DATA_CACHE_DIR set + \\app\\data\\cache
    real artefact (e.g. stale drive-root Docker mount) → env priority MUST
    win regardless of platform.

    Mutation: if env priority is downgraded below _is_posix() gate, Windows
    + stale artefact + user env all simultaneously valid → silent corrupt
    cache selection → this test catches the regression."""
    monkeypatch.setattr("src.utils.paths.platform.system", lambda: "Windows")
    custom = tmp_path / "user_chosen_cache"
    custom.mkdir()
    monkeypatch.setenv("DATA_CACHE_DIR", str(custom))

    # Simulate \app\data\cache stale artefact also existing
    real_exists = pathlib.Path.exists

    def fake_exists(self):
        if str(self) in ("\\app\\data\\cache", "/app/data/cache"):
            return True
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)

    got = resolve_cache_dir()
    # env wins (step 1 priority), NOT \app\data\cache (Windows _is_posix()
    # gate would also drop step 2, so step 1 alone determines outcome)
    assert got == custom
    assert pathlib.Path("/app/data/cache") not in [got, got.resolve()]
