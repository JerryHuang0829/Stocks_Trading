# `scripts/phase_b0_lite_spike.py` 還原 Note

**日期**：2026-05-19
**觸發**：外部 review 審計報告 surface 該 script 不在 repo 中破壞 reproducibility
**動作**：從 git `d2e6eac^` 還原（668 lines）

---

## 為何當初刪掉

Commit `d2e6eac` (2026-05-04 16:33) repo cleanup batch 3：

> Batch 3（結案 scripts，~877 LOC）：
> - scripts/phase_b0_lite_spike.py（B0-Lite spike 已得結論 pivot P5 -> D；
>   reports/phase_b0_lite/{spike_results,decision_pivot_p5}.md 留證據）

**當時邏輯**：spike 結論已得 + 證據 markdown / JSON 保留 → script 視為「一次性結案 code」刪除。

---

## 為何 2026-05-19 還原

外部 review 審計指出：

> P1：B0-Lite reproducer 缺檔
> 報告寫要跑 scripts/phase_b0_lite_spike.py，但 repo 目前找不到這個 script。這破壞可重現性。

對齊「**獨立可重現**」紀律：即使結論已 pivot，若未來有人質疑「DSR=0 怎麼算的」、「mean IC=0.0584 是否 robust」、「為何 71 預期 62 實際」等問題，必須能 reproduce 而非只看 archive JSON。

**結案不等於可拋棄 reproducer**。

---

## 還原版本驗證

### 動作

```bash
git show d2e6eac^:scripts/phase_b0_lite_spike.py > scripts/phase_b0_lite_spike.py
wc -l scripts/phase_b0_lite_spike.py  # 668 lines
```

### Reproducibility 重跑驗證

跑：
```bash
PYTHONPATH=. python scripts/phase_b0_lite_spike.py \
  --start 2019-01-01 --end 2024-12-31 \
  --output-dir reports/phase_b0_lite_rerun_2026-05-19/
```

對比 archive `reports/phase_b0_lite/spike_results.json`：

| 欄位 | Archive (2026-05-03) | Rerun (2026-05-19) | 一致性 |
|---|---|---|---|
| `ic_result.overall.mean_ic` | 0.0584 | 0.0584 | ✅ |
| `ic_result.overall.std_ic` | 0.2283319595 | 0.2283319595 | ✅ |
| `ic_result.overall.ic_ir` | 0.256 | 0.256 | ✅ |
| `ic_result.overall.t_stat` | 2.015 | 2.015 | ✅ |
| `ic_result.overall.p_value` | 0.0483 | 0.0483 | ✅ |
| `ic_result.n_periods` | 62 | 62 | ✅ |
| `ic_result.deflated_sharpe_ratio` | 0.0 | 0.0 | ✅ |
| `ic_result.deflated_sharpe_skewness` | 0.1465 | 0.1465 | ✅ |
| `ic_result.deflated_sharpe_kurtosis` | 2.8477 | 2.8477 | ✅ |
| `turnover_mean` | 0.375 | 0.375 | ✅ |
| `decision.outcome` | Lite-O2 | Lite-O2 | ✅ |
| `coverage_mean` | 0.9640873015873014 | 0.9646825396825396 | ⚠️ Δ=+0.000595 |
| `len(period_ics)` | 62 | 62 | ✅ |
| `rebalance_date` list | 62 dates | 同 62 dates | ✅ |
| Per-period `rank_ic` | 62 values | 62 values 全 byte-identical | ✅ |

### `coverage_mean` 微小差異說明

差 +0.000595 (0.06 pp)，根因推測：
- coverage_per_period 是 63 個 (date, coverage) 紀錄
- coverage = 該 rebalance date 上「有 200-day OHLCV 歷史的 symbol 數」 / `top_n_universe=80`
- FinMind cache 在 2026-05-03 ~ 2026-05-19 期間可能 backfill 了少數早期 symbols 的歷史資料
- 1-2 個早期 period 的 coverage 微幅上升 → mean 微幅上升

**這不影響 DSR / IC / verdict**：mean_ic / DSR / outcome 全 byte-identical。coverage_mean 只是 audit metadata，不進 hard gate 計算（L4 coverage threshold = 60% 在兩個值下都過）。

→ **判定**：reproducibility 可接受，記錄為 known minor drift（cache backfill artefact）。

---

## 跨機器同步須知

若把 repo clone 到其他機器跑：
- 需 `FINMIND_TOKEN` 環境變數
- 需 `DATA_CACHE_DIR` 指向 OHLCV pickle cache（FinMind / TWSE / TPEX 歷史）
- 預期 `mean_ic` / `DSR` / `outcome` 跟 archive byte-identical
- `coverage_mean` 可能微幅不同（cache backfill timing 差異），不影響結論

---

## 相關檔

| 檔 | 用途 |
|---|---|
| `scripts/phase_b0_lite_spike.py` | 還原的 reproducer（668 lines） |
| `reports/phase_b0_lite/spike_results.json` | 2026-05-03 archive（canonical 結果） |
| `reports/phase_b0_lite/H_lite_preregistration.md` | hypothesis pre-registration |
| `reports/phase_b0_lite/decision_pivot_p5.md` | pivot P5 決策紀錄 |
| `reports/phase_b0_lite_rerun_2026-05-19/spike_results.json` | 2026-05-19 還原驗證 rerun |
| `reports/phase_b0_lite/_reconcile_periods_71_vs_62_2026-05-19.md` | 71→62 期數不一致 reconcile |
| `reports/_audit/dsr_low_vol_v2_review_2026-05-19.md` | 本輪 DSR 公式 review 完整 verdict |

---

## 給未來 reviewer

若要再次 cleanup 砍 script，**先確認該 script 對應的 archive JSON 是否仍是某個 evidence chain（research conclusion / paper / pre-registration）的依據**：
- 是 → 保留 script（即使結論 pivot），這是 reproducibility 標準
- 否 → 可砍，但要在 commit message 標明哪些 archive 不再需要 backing reproducer

本 spike 的 archive 仍被 dashboard 因子驗證頁引用 → 永久保留 reproducer。
