"""Unit tests for src.utils.thresholds (follow-up-2)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.utils import thresholds


@pytest.fixture(autouse=True)
def _reset_threshold_cache():
    """Ensure each test sees a fresh cache state."""
    thresholds._cache = None
    thresholds._cache_source = None
    yield
    thresholds._cache = None
    thresholds._cache_source = None


def test_defaults_available_without_yaml(monkeypatch, tmp_path):
    # Point loader at a directory with no yaml → DEFAULTS only
    missing = tmp_path / "nope" / "factor_thresholds.yaml"
    monkeypatch.setattr(thresholds, "_yaml_path", lambda: missing)
    data = thresholds.load_factor_thresholds(reload=True)
    assert data["factor_ic"]["bootstrap"]["n"] == 1000
    assert thresholds.source() == "defaults"


def test_get_threshold_nested_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        thresholds, "_yaml_path", lambda: tmp_path / "missing.yaml"
    )
    thresholds.load_factor_thresholds(reload=True)
    # Hard-coded defaults
    assert thresholds.get_threshold("factor_ic", "dsr", "n_trials_default") == 5
    # Unknown path falls back to default arg
    assert thresholds.get_threshold("no", "such", "key", default=99) == 99


def test_per_panel_min_obs_returns_panel_specific(monkeypatch, tmp_path):
    monkeypatch.setattr(
        thresholds, "_yaml_path", lambda: tmp_path / "missing.yaml"
    )
    thresholds.load_factor_thresholds(reload=True)
    # Quarterly panel configured to 12 in DEFAULTS
    assert thresholds.per_panel_min_obs("quarterly_eps") == 12
    # Daily panels default to 250
    assert thresholds.per_panel_min_obs("ohlcv") == 250
    # Unknown panel falls back to the `default` entry
    assert thresholds.per_panel_min_obs("does_not_exist") == 250


def test_yaml_override_deep_merges(monkeypatch, tmp_path):
    """User yaml should override leaves while untouched defaults survive."""
    yaml_text = textwrap.dedent(
        """
        factor_ic:
          bootstrap:
            n: 555
            avg_block_len: 9.0
        universe:
          min_obs_per_symbol:
            quarterly_eps: 20
        """
    ).strip()
    fake_path = tmp_path / "factor_thresholds.yaml"
    fake_path.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(thresholds, "_yaml_path", lambda: fake_path)

    data = thresholds.load_factor_thresholds(reload=True)
    # Overridden leaves
    assert data["factor_ic"]["bootstrap"]["n"] == 555
    assert data["factor_ic"]["bootstrap"]["avg_block_len"] == 9.0
    assert data["universe"]["min_obs_per_symbol"]["quarterly_eps"] == 20
    # Untouched defaults survive
    assert data["factor_ic"]["bootstrap"]["seed"] == 42
    assert data["factor_ic"]["dsr"]["n_trials_default"] == 5
    assert data["universe"]["min_obs_per_symbol"]["ohlcv"] == 250
    assert thresholds.source() == f"yaml:{fake_path}"


def test_yaml_parse_error_falls_back(monkeypatch, tmp_path, caplog):
    bad = tmp_path / "factor_thresholds.yaml"
    bad.write_text("not: a: valid: yaml: :\n", encoding="utf-8")
    monkeypatch.setattr(thresholds, "_yaml_path", lambda: bad)
    with caplog.at_level("WARNING"):
        data = thresholds.load_factor_thresholds(reload=True)
    assert data["factor_ic"]["bootstrap"]["n"] == 1000  # fell back
    # Source recorded as defaults because yaml parse failed
    assert thresholds.source() == "defaults"


def test_cache_returned_on_second_call(monkeypatch, tmp_path):
    call_count = {"n": 0}
    yaml_text = "factor_ic:\n  bootstrap:\n    n: 777\n"
    fake_path = tmp_path / "factor_thresholds.yaml"
    fake_path.write_text(yaml_text, encoding="utf-8")

    original = fake_path.read_text

    def spy_read_text(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(thresholds, "_yaml_path", lambda: fake_path)
    thresholds.load_factor_thresholds(reload=True)
    # Mutating the file after first load must not be reflected unless reload=True
    fake_path.write_text("factor_ic:\n  bootstrap:\n    n: 111\n", encoding="utf-8")
    data = thresholds.load_factor_thresholds()
    assert data["factor_ic"]["bootstrap"]["n"] == 777
    # reload=True picks up the new value
    data2 = thresholds.load_factor_thresholds(reload=True)
    assert data2["factor_ic"]["bootstrap"]["n"] == 111
