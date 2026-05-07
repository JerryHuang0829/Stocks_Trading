# F5 R21 Backlog — low_vol_v2 close > 0 Filter + Diagnostics

**完成日期**：2026-05-04（Phase P5 Session 1）
**對應 R21 Codex audit P3 finding**

---

## 問題

`src/features/low_vol_v2.py` 在 R21 audit 跑 spike 時出現 `RuntimeWarning: divide by zero encountered in log`。

**Root cause**：4 個 stocks / 12 rows / 0.2% OHLCV cache 含 close=0（疑似停牌 / 下市殘留 / cache 髒），`np.log(0) = -inf` 進 std 計算。

**舊行為**：std_val 變 -inf，被 `if not np.isfinite(std_val)` 守住 drop 該 symbol；但**沒明示計數**，warning 噪音不可追蹤。

---

## 修法

### Code

`src/features/low_vol_v2.py:compute_low_vol_v2_universe` 加：
1. **顯式 `close > 0` filter**（在 `_normalise_close` 後 `valid = close[close.index <= as_of]` 之後）
2. **`return_diagnostics: bool = False`** keyword-only argument 保 backward-compat：
   - `False`（default）→ 回 `pd.Series`（既有 B0-Lite spike + tests 不需改）
   - `True` → 回 `(pd.Series, dict)` tuple，dict 含 5 個 counts:
     - `dropped_for_no_close`：ohlcv None / empty / no `close` column
     - `dropped_for_zero_close`：filter 掉 close ≤ 0 但仍 process
     - `dropped_for_insufficient_history`：< min_history (預設 200) 或 window_slice 不足
     - `dropped_for_zero_std`：std_val ≤ 0 或 non-finite
     - `bad_data_count`：= sum 上面 4 個

### Tests

`tests/test_low_vol_v2.py` 加 2 tests：
- `test_compute_low_vol_v2_filters_zero_close`：注入 5 個 close=0 rows，verify symbol 仍 process（剩 history 足夠）+ diagnostics 正確記 1
- `test_compute_low_vol_v2_diagnostics_count`：4 種 input（None / empty / short / long），verify counts 對齊 + backward-compat default 返回 `pd.Series`

### Backward-compat 驗證

- `phase_b0_lite_spike.py` 不需改（用 default `return_diagnostics=False`）
- B0-Lite spike replay：`IC = 0.0584` 完全一致；coverage 96.4% → 96.5%（+0.1pp）僅因 1 stock close=0 filter 後 history 仍夠進 universe，**不算 numerical drift**

---

## 證據

```bash
$ conda run -n quant python -m pytest tests/test_low_vol_v2.py -v
test_252d_std_numerical PASSED
test_shift_1_pit_excludes_anchor_close PASSED
test_min_history_200_drops_short_series PASSED
test_dropna_no_nan_imputation PASSED
test_high_vol_lower_score PASSED
test_compute_low_vol_v2_filters_zero_close PASSED
test_compute_low_vol_v2_diagnostics_count PASSED
test_score_low_vol_v2_per_symbol_wrapper PASSED
8 passed
```

```bash
$ conda run -n quant python scripts/phase_b0_lite_spike.py --start 2019-01-01 --end 2024-12-31 ...
mean rank IC = 0.0584 (B0-Lite v3.0 record 0.0584；F5 後 0.0584 完全相同)
L4 coverage_mean = 0.965 (B0-Lite v3.0 record 0.964；F5 +0.1pp)
```

---

## Pro 紀律

- Pattern 6 silent fallback fix：原本 `np.isfinite` guard silently drop bad symbols，現在 explicit count 進 diagnostics
- Backward-compat：B0-Lite hypothesis lock IC=0.0584 不破壞（plan V3 verification gate IC ± 1e-4 達標）
- F5 mutation test verify：拿掉 `close[close > 0]` filter → 既有 test 仍 pass（filter 對既有 synthetic test 不觸發）但加的 `test_compute_low_vol_v2_filters_zero_close` 會 fail
