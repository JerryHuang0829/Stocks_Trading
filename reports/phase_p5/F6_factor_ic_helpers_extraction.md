# F6 R21 Backlog — `scripts/_factor_ic_helpers.py` 共用 utility 抽出

**完成日期**：2026-05-04（Phase P5 Session 1）
**對應 R21 Codex audit P3 finding**

---

## 問題

`scripts/phase_b0_lite_spike.py:50-57` 直接 import private functions from `scripts/run_factor_ic.py`：

```python
from scripts.run_factor_ic import (  # noqa: E402
    REGIME_SYMBOL,
    _compute_regimes,
    _forward_return,
    _load_industry_labels,
    _load_ohlcv,
    _load_universe_ohlcv,
)
```

**Pattern 14 Cross-script private import 反 pattern**（_前綴 = private convention）：
- `run_factor_ic.py` 任何 refactor → spike 跟 P5 main script silent break
- 工程慣例：private functions 應只在原 module 內 call；跨 script reuse 必須 抽 utility module

R21 finding F6 要求：抽 12 個 helper（line 104-398）成 `scripts/_factor_ic_helpers.py` shared utility，`run_factor_ic.py` 改用 thin shim re-export 保 backward-compat。

---

## 修法

### 抽出 12 functions

新建 `scripts/_factor_ic_helpers.py`（348 行）含：

| 常數 | 用途 |
|---|---|
| `REGIME_SYMBOL = "0050"` | regime detection benchmark |
| `MIN_UNIVERSE_SIZE = 50` | min symbols qualifying |
| `PANEL_DIRS_FOR_INTERSECTION` | 5-tuple of cache panels |
| `DEFAULT_MIN_OBS_PER_SYMBOL = 250` | ~1 trading year |
| `DEFAULT_MAX_GAP_DAYS = 5` | stale price tolerance |

| Function | 用途 |
|---|---|
| `_normalise_index` | 統一 tz strip + sort |
| `_load_ohlcv` | 單檔 OHLCV pickle load |
| `_load_universe_ohlcv` | 全 universe OHLCV scan + 4-digit filter |
| `_load_universe_revenue` | Revenue panel reuse |
| `_load_universe_timeseries` | Generic per-symbol pickle loader |
| `_load_issued_capital` | issued_shares per symbol |
| `_load_industry_labels` | industry_category dict |
| `_load_market_value` | latest market_value snapshot |
| `_resolve_price_asof` | Price + anchor at target_date with max_gap_days |
| `_forward_return` | (end / start - 1) |
| `_compute_intersection_universe` | per-panel intersection w/ min_obs |
| `_compute_regimes` | 0050 ADX / SMA → regime labels |

### thin shim 保 backward-compat

`scripts/run_factor_ic.py` 改：
```python
# Phase P5 Session 1 / R21 finding F6 fix (2026-05-03)
from scripts._factor_ic_helpers import (  # noqa: F401
    REGIME_SYMBOL, MIN_UNIVERSE_SIZE, PANEL_DIRS_FOR_INTERSECTION,
    DEFAULT_MIN_OBS_PER_SYMBOL, DEFAULT_MAX_GAP_DAYS,
    _normalise_index, _load_ohlcv, _load_universe_ohlcv,
    _load_universe_revenue, _load_universe_timeseries,
    _load_issued_capital, _load_industry_labels, _load_market_value,
    _resolve_price_asof, _forward_return,
    _compute_intersection_universe, _compute_regimes,
)
```

刪除 line 111-405 inline `def` blocks（295 lines）。`run_factor_ic.py` 706 → 411 lines。

### Caller migration

`scripts/phase_b0_lite_spike.py` import 路徑改：
```python
from scripts._factor_ic_helpers import (  # noqa: E402
    REGIME_SYMBOL,
    _compute_regimes, _forward_return,
    _load_industry_labels, _load_ohlcv, _load_universe_ohlcv,
)
```

`run_factor_ic.py` skill `/factor-ic` 跟 manual CLI 都不需改（thin shim 保 100% backward-compat）。

### Tests

新增 `tests/test_factor_ic_helpers.py` 6 tests：
- `test_normalise_index_strips_tz`
- `test_resolve_price_asof_within_gap`
- `test_resolve_price_asof_beyond_gap_returns_none`
- `test_forward_return_basic`
- `test_forward_return_missing_symbol_returns_none`
- `test_run_factor_ic_re_exports_helpers`（驗 thin shim 真 re-export 同一個 function 物件）

---

## 證據

```bash
$ conda run -n quant python -m pytest tests/test_factor_ic_helpers.py -v
6 passed in 0.85s
```

```bash
$ conda run -n quant python -c "from scripts import run_factor_ic; print(list(run_factor_ic.FACTOR_REGISTRY.keys()))"
['high_proximity', 'revenue_momentum_v2', 'margin_short_ratio', 'foreign_broker_v2', 'pead_eps']

$ conda run -n quant python scripts/phase_b0_lite_spike.py --start 2019-01-01 --end 2024-12-31 ...
mean rank IC = 0.0584 (F5+F6 後 100% 一致)
```

---

## Pro 紀律

- Pattern 14 Cross-frame parity：private import migration 對所有 caller（`run_factor_ic` / `phase_b0_lite_spike` / 未來 `p5_smart_beta_tilt`）一次到位
- Backward-compat 不破壞：thin shim re-export + import path migration + IC=0.0584 100% 一致
- Test mutation verify：`test_run_factor_ic_re_exports_helpers` 用 `is` operator 驗 同一 function 物件（不是 wrapper），mutation 改 thin shim 為 wrapper → test fail
- 進 P5 main plan 時 `p5_smart_beta_tilt.py` 直接 `from scripts._factor_ic_helpers import ...`，**不再** import private functions 跨 script
