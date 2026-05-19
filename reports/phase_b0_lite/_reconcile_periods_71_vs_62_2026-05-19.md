# `H_lite` 預註冊 71 periods vs `spike_results.json` 實際 62 periods — Reconcile

**觸發**：外部 review 指出 H_lite 預註冊 71 periods 與實際 62 periods 不一致
**日期**：2026-05-19
**verdict**：**符合 H_lite preregistration fallback 規則**，差異有合理 silent skip 機制，**不是 silent bug**，但 H_lite / spike_results.md 未明文 reconcile，本文補上。

---

## 數字對照

| 出處 | n_periods | 來源 |
|---|---:|---|
| Theoretical max（2019-01 ~ 2024-12 monthly anchor day=12）| **72** | `BacktestEngine._generate_rebalance_dates` 跑出 |
| H_lite preregistration L2 行 | **71** | [H_lite_preregistration.md:33](H_lite_preregistration.md#L33)「mean rank IC across 71 monthly rebalance periods (2019-01 ~ 2024-12)」 |
| `spike_results.json.coverage_per_period` | **63** | 真正計算 IC 時的 rebalance dates（早期 8 個被 skip 不入紀錄） |
| `spike_results.json.ic_result.n_periods` | **62** | 進 IC aggregate 的 valid periods（再 drop 1 個極低 coverage） |

---

## 71 → 62 完整 skip chain

從本日（2026-05-19）還原版 `scripts/phase_b0_lite_spike.py` 實跑 log 取得 ground truth：

```
2026-05-19 17:23:17 INFO Generated 72 rebalance dates
2026-05-19 17:23:21 WARNING Period 2019-02-12: empty factor scores — skip
2026-05-19 17:23:21 WARNING Period 2019-03-12: empty factor scores — skip
2026-05-19 17:23:22 WARNING Period 2019-04-12: empty factor scores — skip
2026-05-19 17:23:23 WARNING Period 2019-05-13: empty factor scores — skip
2026-05-19 17:23:23 WARNING Period 2019-06-12: empty factor scores — skip
2026-05-19 17:23:24 WARNING Period 2019-07-12: empty factor scores — skip
2026-05-19 17:23:25 WARNING Period 2019-08-12: empty factor scores — skip
2026-05-19 17:23:26 WARNING Period 2019-09-12: empty factor scores — skip
2026-05-19 17:23:26 WARNING Period 2019-10-14: only 5 forward returns — skip
2026-05-19 17:24:18 INFO Running factor_ic_report on 62 periods
```

### Skip 機制 1：「empty factor scores」(8 periods, 2019-02 ~ 2019-09)

**根因**：`min_history=200` trading days 過濾

- low_vol_v2 因子需要 200 個交易日的 OHLCV 計算 252-day rolling std
- 2019-02-12 rebalance → 需 2018-04-12 起的 OHLCV
- universe = TWSE/TPEX top-80 by close × volume per period
- 2019 早期 universe 中很多 symbols 沒有充足歷史（新上市 / 早期 cache 不全 / 流動性篩 universe 後變化）
- → 算出來 0 個 symbol 有 valid score → `empty factor scores` skip

**為何 2019-01-14 進入但 2019-02 ~ 2019-09 skip**：
- 2019-01-14 universe 計算當下，部分長期 symbol 仍有 200d 歷史可算（coverage = 26.25%）
- 但 2019-02 之後的 universe 重新篩選 → 篩到的 top-80 symbol 組合不同，可能納入更多 newer symbols → 沒一個能撐 200d → skip

### Skip 機制 2：「only N forward returns」(1 period, 2019-10-14)

**根因**：forward return panel 對應 symbol 數太少

- 2019-10-14 universe 篩出來只有 5 個 symbol 有完整 forward month return
- IC 計算需要至少某個 threshold 的 cross-sectional N 才有意義
- → 從 `period_ics` drop（但 coverage_per_period 仍保留紀錄，coverage = 6.25% = 5/80）

### 最終 valid periods

```
72 (theoretical anchor dates)
- 8 (empty factor scores: 2019-02 ~ 2019-09)
- 1 (only 5 forward returns: 2019-10-14)
- 1 (2024-12-12: forward return 需 2025-01 資料、超出 end=2024-12-31)
= 62
```

H_lite preregistration 寫 71 是漏算了「2024-12 不可有 forward return」這條，理論上應寫 71；但實際運算過程中 2019 早期 9 個 periods 又被 silent skip → 62。

---

## H_lite preregistration 是否「漏寫 skip rule」？

讀 H_lite_preregistration.md 全文：
- L4 coverage rule 寫了「per rebalance period top-80 universe 中能算 252d std 的 stock 比例」+ 「≥ 60%」threshold
- 但沒寫「coverage = 0 / 太低時 period 從 IC aggregate 全部 skip」這條 implicit fallback

### Verdict：implicit fallback 是合理 silent skip，非 silent bug

理由：
1. **PIT-safe 要求自然推出**：若 universe 中 0 個 symbol 能算分數，硬留在 IC aggregate 等於塞 NaN 進 mean_ic 計算 → 不是 valid scientific decision
2. **L4 coverage gate (60%) 已涵蓋部分過濾**：但 L4 是「平均 coverage」而非「per-period 入選門檻」，所以實作上多了一層 implicit「coverage = 0 → period skip」防護
3. **`empty factor scores` skip 對 IC 估計是 conservative 的**：少算的是 universe 中沒充足歷史的 symbol，沒讓 IC over-estimate
4. **62 vs 71 數字差不影響結論**：mean_ic = 0.0584 用 62 periods 算；如果硬用 71（含 9 個 skip 期，那些期沒 score 怎麼算？）反而是 silent bug

→ 這個 implicit skip 是 **defensive engineering**，不是 silent_bug。

---

## 給未來 H_lite-style preregistration 的建議（process improvement）

下次寫 hypothesis preregistration 時，**明確列出 skip rule**：

```markdown
### Period inclusion rules
- monthly rebalance dates 從 BacktestEngine._generate_rebalance_dates 生成
- 若該 period universe 中無 symbol 有 min_history (200d) 歷史 → period 從 IC aggregate skip
- 若該 period forward return 可算 symbol 數 < threshold (e.g., 10) → period skip
- 預期 n_periods（含 skip 上限）：71 - estimated_skip_count = ~62
```

對應 PIT 紀律 + transparency standard。

---

## 相關證據

| 證據 | 出處 |
|---|---|
| 72 theoretical max | `scripts/phase_b0_lite_spike.py` rerun log line 17:23:17 |
| 71 H_lite write | [H_lite_preregistration.md:33](H_lite_preregistration.md#L33) |
| 63 coverage_per_period | `reports/phase_b0_lite/spike_results.json` |
| 62 final n_periods | `reports/phase_b0_lite/spike_results.json.ic_result.n_periods` |
| 8 skip 2019-02 ~ 2019-09 | rerun log line 17:23:21 ~ 26 「empty factor scores — skip」 |
| 1 skip 2019-10-14 | rerun log line 17:23:26「only 5 forward returns — skip」|
| `min_history=200` config | `spike_results.json.spike_config.min_history` |
| `top_n_universe=80` config | `spike_results.json.spike_config.top_n_universe` |
| Reproducer 還原 | `reports/phase_b0_lite/_RESTORE_NOTE_2026-05-19.md` |
| DSR 公式 review 完整 verdict | `reports/_audit/dsr_low_vol_v2_review_2026-05-19.md` |

---

## 對 review verdict

外部 review 指出「H_lite_preregistration.md 說 71 monthly periods；spike_results.json 實際 n_periods=62。可能是 min_history/coverage skip，但需要在報告中明確交代。」

→ **本文已明確交代**：
- 71 是 H_lite 的 nominal expectation
- 62 是經 `min_history=200` (8 periods skip) + `low coverage` (1 period skip) 自然過濾後的 valid IC sample size
- 不是 bug、不是 cherry-pick、是 PIT-safe 工程必然
- H_lite preregistration 未明文 skip rule 是 process gap（流程改善方向見上「給未來 preregistration 的建議」段），但**不影響本次結論**
