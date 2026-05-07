"""Smoke test — verify import chain and basic logic without calling FinMind API."""

from __future__ import annotations

import sys
from datetime import datetime


def test_imports():
    """Verify all modules can be imported."""
    print("[1/5] Testing imports...")
    from src.backtest.engine import BacktestEngine, _DataSlicer
    from src.backtest.universe import HistoricalUniverse
    from src.backtest.metrics import compute_metrics, format_report
    from src.portfolio.tw_stock import (
        _analyze_symbol,
        _rank_analyses,
        _select_positions,
        _analyze_market_proxy,
        get_portfolio_config,
    )
    from src.data.finmind import FinMindSource
    from src.storage.database import compute_config_hash
    from src.utils.config import load_config
    print("  OK — all modules imported")


def test_config():
    """Verify config loads correctly."""
    print("[2/5] Testing config...")
    from src.utils.config import load_config
    from src.portfolio.tw_stock import get_portfolio_config

    config = load_config("config/settings.yaml")
    pc = get_portfolio_config(config)
    assert pc["top_n"] > 0, "top_n must be positive"
    assert pc["max_position_weight"] > 0, "max_position_weight must be positive"
    assert pc.get("auto_universe_size", 80) > 0, "auto_universe_size must be positive"
    print(f"  OK — profile={pc.get('profile_label', 'custom')}, top_n={pc['top_n']}")


def test_config_hash():
    """Verify config hash is deterministic."""
    print("[3/5] Testing config hash...")
    from src.storage.database import compute_config_hash
    from src.utils.config import load_config
    from src.portfolio.tw_stock import get_portfolio_config

    config = load_config("config/settings.yaml")
    pc = get_portfolio_config(config)
    h1 = compute_config_hash(pc)
    h2 = compute_config_hash(pc)
    assert h1 == h2, "Config hash must be deterministic"
    assert len(h1) == 16, f"Expected 16-char hash, got {len(h1)}"
    print(f"  OK — hash={h1}")


def test_metrics_empty():
    """Verify metrics handles empty input gracefully."""
    print("[4/5] Testing metrics with empty input...")
    import pandas as pd
    from src.backtest.metrics import compute_metrics, format_report

    empty = pd.Series(dtype="float64")
    m = compute_metrics(empty)
    assert m == {}, "Empty returns should give empty metrics"
    report = format_report(m)
    assert "Backtest" in report
    print("  OK — empty input handled")


def test_rebalance_dates():
    """Verify rebalance date generation."""
    print("[5/5] Testing rebalance date generation...")
    from src.backtest.engine import BacktestEngine

    dates = BacktestEngine._generate_rebalance_dates(
        datetime(2023, 1, 1), datetime(2023, 6, 30), day=12
    )
    assert len(dates) == 6, f"Expected 6 rebalance dates, got {len(dates)}"
    assert all(d.day == 12 for d in dates), "All dates should be on day 12"
    print(f"  OK — {len(dates)} rebalance dates generated: {[d.strftime('%Y-%m-%d') for d in dates]}")


def main():
    print("=" * 50)
    print("  Smoke Test — Quantitative Trading")
    print("=" * 50)
    print()

    tests = [test_imports, test_config, test_config_hash, test_metrics_empty, test_rebalance_dates]
    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as exc:
            print(f"  FAIL — {exc}")
            failed += 1
        print()

    print("=" * 50)
    print(f"  Result: {passed} passed, {failed} failed")
    print("=" * 50)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
