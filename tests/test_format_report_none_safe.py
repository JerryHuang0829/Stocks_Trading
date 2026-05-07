"""Regression: format_report must not crash when higher-moment stats are None.

compute_metrics sets skewness/kurtosis/jarque_bera_* to None when scipy
returns NaN on degenerate series. The previous format_report used
``metrics.get('skewness', 0):.2f`` which only defaults when the key is absent
— a present key with value None raised TypeError: unsupported format string
passed to NoneType.__format__.
"""

from __future__ import annotations

import pytest

from src.backtest.metrics import format_report


def _base_metrics(**overrides):
    m = {
        "annualized_return": 0.1,
        "total_return": 0.2,
        "years": 1.0,
        "trading_days": 252,
        "annualized_volatility": 0.15,
        "max_drawdown": -0.08,
        "skewness": None,
        "kurtosis": None,
        "jarque_bera_stat": None,
        "jarque_bera_pvalue": None,
        "sharpe_ratio": 1.2,
        "sortino_ratio": 1.5,
        "calmar_ratio": 1.3,
    }
    m.update(overrides)
    return m


def test_format_report_handles_none_skew_kurt_jb():
    # Key present with None — previous code would raise TypeError.
    report = format_report(_base_metrics())
    assert "偏態:           N/A" in report
    assert "峰度:           N/A" in report
    assert "Jarque-Bera p:  N/A" in report


def test_format_report_still_formats_valid_stats():
    report = format_report(
        _base_metrics(skewness=-0.42, kurtosis=3.1, jarque_bera_pvalue=0.01)
    )
    assert "-0.42" in report
    assert "3.10" in report
    assert "非常態" in report


def test_format_report_omits_skew_block_when_key_absent():
    m = _base_metrics()
    for k in ("skewness", "kurtosis", "jarque_bera_stat", "jarque_bera_pvalue"):
        m.pop(k)
    report = format_report(m)
    assert "偏態" not in report
    assert "Jarque-Bera" not in report
