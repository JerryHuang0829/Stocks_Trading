# reports/ — 研究 evidence 索引

本目錄收錄整個研究歷程的 pre-registration / audit / 結果 evidence。
**按時間軸 + demo 價值**列出建議閱讀順序：

---

## 1️⃣ 揭穿過去 alpha 為 overfit（2026-04-15）

科學紀律 demo：**敢於揭穿自己過去的成果**。

- [diagnosis/2026-04-16_edge_diagnosis.md](diagnosis/2026-04-16_edge_diagnosis.md) ⭐
  揭穿三因子 `tw_3m_stable` 4 年 Sharpe 1.73 / α +39% 為 overfit
  根因：(1) `finmind.py` timezone bug；(2) universe pre-filter 用 `STOCK_DAY_ALL` 對歷史日期永遠失敗
  修後：Sharpe 1.73 → 0.64 / α +39% → +3.4% → 過去研究結論全部需重驗
- [diagnosis/2026-04-16_independent_audit.md](diagnosis/2026-04-16_independent_audit.md)
  獨立審計（含 5 個 audit script + IC 重算 / regime permutation / friction / passive 評估）

---

## 2️⃣ Phase A1 — 5 因子 IC（2026-04-16~20）

Pro statistical methodology 入門 demo（DSR / Block Bootstrap / FDR）。

- [factor_ic/phase_a1_summary.md](factor_ic/phase_a1_summary.md) ⭐
  5 因子綜合結論（2/5 過中道：52W High / PEAD；0/5 過嚴格）
- [factor_ic/factor_correlation_matrix.md](factor_ic/factor_correlation_matrix.md)
  5 因子 cross-correlation 矩陣
- `factor_ic/*_ic.json` — 5 個因子 IC raw 數據（per-period IC + bootstrap distribution）

---

## 3️⃣ Phase B0-Lite spike + P5 pivot（2026-05-03）

pivot 決策紀錄：**強 IC ≠ 強策略**的判斷力 demo。

- [phase_b0_lite/H_lite_preregistration.md](phase_b0_lite/H_lite_preregistration.md)
  low_vol_v2 spike 假設 pre-registration
- [phase_b0_lite/spike_results.md](phase_b0_lite/spike_results.md) ⭐
  IC 0.0584（看起來強）但 DSR=0 + 4 systemic warnings → reject
- [phase_b0_lite/decision_pivot_p5.md](phase_b0_lite/decision_pivot_p5.md)
  spike fail 後 pivot 邏輯

---

## 4️⃣ Pro Validation Sprint（2026-05-04，9 phase）

多視角 multi-perspective audit demo。

- [sprint_pro_validation/J_multi_perspective_audit.md](sprint_pro_validation/J_multi_perspective_audit.md) ⭐⭐
  **21 個攻擊角度全答**（7 角色：量化主管 / 策略員 / vol trader / CRO / 面試官 / 資料工程師 / 台灣 retail + Codex audit）
- [sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md](sprint_pro_validation/CANONICAL_MANIFEST_2026-05-04.md)
  upstream canonical evidence anchor（綁 commit hash 防漂移）
- 子目錄：
  - `A_env/` — 環境驗證 evidence
  - `B_repro/` — 5 因子 IC + D1_v2 IS/OOS 重現（**Pro Sprint 重現版**，與 §2 Phase A1 原版互補對照）
  - `C_pit_mutation/` — PIT mutation tests evidence
  - `J_multi_perspective/` — 21 attack 詳細子目錄

---

## 5️⃣ Phase D v7 — 最終 18-cell sweep（2026-05-06，CONFIRM-NO-GO）

整個 repo 最重要的 demo evidence——pre-registration 紀律 + 18-cell 真實結果 + 雙重 audit。

### Pre-registration（事前鎖定）
- [phase_d/H_d_v6_preregistration.md](phase_d/H_d_v6_preregistration.md) ⭐⭐
  **6 candidates × 3 top_n = 18 cells 鎖定**
  6 hard reject criteria（IR / 月 α / TE / Max DD / A1 active gate / 80% bootstrap CI）
  13 條 pre-commit + 3 條 code-level enforcement assertion
  D-A 預先 disqualify per Phase A2 D6 OOS IR collapse 99.4%
- [phase_d/v6_validation_manifest.md](phase_d/v6_validation_manifest.md)
  Phase 0 baseline 規格 + cache caveat
- [phase_d/A11_l6_ci_comparison.md](phase_d/A11_l6_ci_comparison.md)
  L6 80% vs 95% bootstrap CI 實證對照（為什麼降標 80%）

### Codex audit + 自審
- [phase_d/R24_resolution.md](phase_d/R24_resolution.md)
  5 個 P0 + 7 個設計修法（Codex Round 24）
- [phase_d/pre_implementation_review_2026-05-04.md](phase_d/pre_implementation_review_2026-05-04.md)
  Claude in-house Pro Review（multi-perspective + self-audit + forensic-sweep）verdict GO-WITH-CAVEATS

### 18-cell canonical 結果（NO-GO 證據）
- [phase_d/cell_sweep_v7_2026_05_06_round3/cell_summary.json](phase_d/cell_sweep_v7_2026_05_06_round3/cell_summary.json) ⭐⭐
  **outcome_classification: "Outcome-2 Partial"**
  **n_outcome_1_cells: 0 / 18**（無 cell 過全部 6 條 hard gate）
  **sole_survivor: null**
- `phase_d/cell_sweep_v7_2026_05_06_round3/cell_metrics.json` — per-cell IR / 月 α / TE / max_dd_diff / DSR
- `phase_d/cell_sweep_v7_2026_05_06_round3/cell_bootstrap_ci_lowers.json` — L6 80% CI lowers
- `phase_d/cell_sweep_v7_2026_05_06_round3/cell_monthly_active_returns.json` — per-cell 月超額報酬序列
- `phase_d/18cell_run_2026_05_06.log.err` — 18-cell run 完整 stderr（3.5 hr）

---

## 6️⃣ 架構 audit

- [architecture_audit_2026_05_02.md](architecture_audit_2026_05_02.md)
  整個架構 audit + ~32 MB 清理計畫

---

## 為什麼這些對量化研究 demo 有用

每份 evidence 展示**研究紀律的某個面向**：

| 面向 | 對應 evidence |
|---|---|
| Pre-registration 紀律（事前鎖死防 p-hacking）| phase_d / phase_b0_lite |
| 多視角 audit（防 confirmation bias）| sprint_pro_validation/J_multi_perspective |
| 揭穿自己舊結果（科學誠實）| diagnosis/2026-04-16_edge_diagnosis |
| Pro 統計方法論（DSR / Bootstrap / FDR / PIT）| factor_ic / sprint_pro_validation/B_repro |
| Codex 外部 audit（last line of defense）| phase_d/R24_resolution + cell_summary.json |
| 接受 NO-GO（不硬要找 alpha）| phase_d/cell_summary.json Outcome-2 |

→ 合起來證明這不是隨便跑 backtest，是 institutional 等級流程。
