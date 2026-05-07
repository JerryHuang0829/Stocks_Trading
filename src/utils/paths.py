"""Filesystem path helpers (shared cache-dir resolution).

Consolidates the DATA_CACHE_DIR resolution used by finmind.py, twse_scraper.py
and backtest.universe. Previously each call site duplicated the env-lookup and
Docker-path default (``/app/data/cache``); only twse_scraper had a project-root
fallback for local (Windows) development. That asymmetry caused
``HistoricalUniverse`` to silently see 0 cached OHLCV files when ``DATA_CACHE_DIR``
was unset on a workstation — the universe then fell back to stock_info order
and produced garbage rankings without any error.

Phase 0 V0.2 (R24 Codex audit P0-2 fix, 2026-05-04):
    On Windows, ``Path("/app/data/cache").exists()`` resolves to
    ``E:\\app\\data\\cache`` (current drive root) which may be a stale partial
    cache from prior Docker volume mounts. That partial cache historically
    lacked ``institutional_v2 / issued_capital / margin_short / quarterly_eps``
    panels, causing IC research to silently use incomplete data.

    Fix: on Windows, ``/app/data/cache`` is NEVER auto-selected because there
    is no Docker mount semantically; only honour it on POSIX. Local repo
    fallback wins on Windows. Explicit ``DATA_CACHE_DIR`` env var still works
    on either platform.
"""

from __future__ import annotations

import os
import pathlib
import platform


def _is_posix() -> bool:
    """True on Linux/Docker, False on Windows. Used to gate ``/app/data/cache``."""
    return platform.system() != "Windows"


def resolve_cache_dir() -> pathlib.Path:
    """Return the canonical data cache directory.

    Resolution order:
      1. ``$DATA_CACHE_DIR`` env var, if the path exists.
      2. (POSIX only) Docker mount default ``/app/data/cache`` if it exists.
      3. Project-root fallback ``<repo>/data/cache`` (always preferred on
         Windows; canonical local-dev path that the repo creates on first run).

    Phase 0 V0.2 fix: ``/app/data/cache`` is NOT consulted on Windows because
    Windows path semantics resolve ``/app/...`` to the current drive root
    (e.g. ``E:\\app\\data\\cache``), which may exist as a stale Docker-mount
    artefact missing 4 of the 11 cache panels. Returning that partial cache
    silently corrupts IC research / backtest results.
    """
    env = os.environ.get("DATA_CACHE_DIR")
    if env:
        p = pathlib.Path(env)
        if p.exists():
            return p
    if _is_posix():
        docker_default = pathlib.Path("/app/data/cache")
        if docker_default.exists():
            return docker_default
    # Project-root fallback: this file is src/utils/paths.py → parents[2] = repo root
    return pathlib.Path(__file__).resolve().parents[2] / "data" / "cache"
