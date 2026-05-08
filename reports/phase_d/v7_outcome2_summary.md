# Phase D v7 Outcome-2 Closeout

日期：2026-05-07  
審計 anchor：`6e3bde0` + canonical run output  
Canonical output：`reports/phase_d/cell_sweep_v7_2026_05_06/`

## 結論

Phase D v7 正式結案為 **CONFIRM-NO-GO / Outcome-2 Partial**。

- 18 個事前鎖定 cells 全部完成：6 candidates x 3 top_n `{8, 12, 16}`
- `n_outcome_1_cells = 0 / 18`
- `sole_survivor = null`
- 所有 cells 都沒有通過 L1-L6 全部 hard gates
- 因此不啟動 active top-N paper trade，也不應包裝成可實盤策略

這不是 evaluator bug。官方 `cell_summary.json` 與 gate aggregation 一致，真正問題是 alpha 強度不足以在 retail-realistic gate 下穩定勝過 0050。

## Hard Gate Definition

L5 是一個 aggregate A1 gate，不是單看 active correlation。

| Gate | Pass 條件 |
|---|---|
| L1 | IR vs 0050 >= 0.20 |
| L2 | mean monthly alpha >= 0.005 |
| L3 | TE in `[0.10, 0.30]` |
| L4 | max drawdown diff vs 0050 <= 0.05 |
| L5 | active_corr <= 0.50 AND TE >= 0.10 AND beta_adj_alpha_t > 1.5 |
| L6 | stationary block bootstrap 80% CI lower > 0 |

## 18-Cell Gate Result

`P/F` 以官方 evaluator 的 L1-L6 定義重算；L5 使用 aggregate A1 gate。

| Cell | L1 | L2 | L3 | L4 | L5 A1 | L6 | Pass |
|---|---|---|---|---|---|---|---|
| D-B \| 8 | F | F | P | P | F | F | 2/6 |
| D-B \| 12 | F | F | P | F | F | F | 1/6 |
| D-B \| 16 | F | F | P | F | F | F | 1/6 |
| D-C \| 8 | F | F | F | P | F | F | 1/6 |
| D-C \| 12 | P | P | P | P | F | F | 4/6 |
| D-C \| 16 | F | F | P | P | F | F | 2/6 |
| D-D \| 8 | F | F | P | F | F | F | 1/6 |
| D-D \| 12 | F | F | P | F | F | F | 1/6 |
| D-D \| 16 | F | F | P | F | F | F | 1/6 |
| D-E \| 8 | P | P | F | P | F | F | 3/6 |
| D-E \| 12 | P | P | F | P | P | F | 4/6 |
| D-E \| 16 | P | P | P | P | F | F | 4/6 |
| D-F \| 8 | P | P | F | P | F | F | 3/6 |
| D-F \| 12 | P | P | F | P | F | F | 3/6 |
| D-F \| 16 | F | F | F | P | F | F | 1/6 |
| D-G \| 8 | F | F | P | F | P | F | 2/6 |
| D-G \| 12 | F | F | P | F | P | F | 2/6 |
| D-G \| 16 | F | F | P | F | P | F | 2/6 |

## Closest Cells

| Cell | What looked good | Why it still fails |
|---|---|---|
| D-E \| 12 | IR 0.337, monthly alpha 0.876%, L5 passed | TE 0.312 exceeds L3 upper bound; L6 CI lower = -0.0060 |
| D-C \| 12 | IR 0.236, monthly alpha 0.549%, TE in range | L5 fails because beta-adjusted alpha t = 1.241 < 1.5; L6 CI lower = -0.0063 |
| D-E \| 16 | IR 0.238, monthly alpha 0.524%, TE in range | L5 fails because beta-adjusted alpha t = 1.363 < 1.5; L6 CI lower = -0.0062 |
| D-F \| 8 | Highest monthly alpha at 1.270% | TE 0.446 too high; L5 fails; L6 CI lower = -0.0079 |

## Root Cause

1. 60 months is too short for stable active-return inference under stationary block bootstrap.
2. The best active return series still has a CI lower below zero.
3. Several high-alpha cells reach the return gates only by taking too much active risk.
4. L5 filters out cells whose active return is not sufficiently robust after beta adjustment.
5. `n_trials = 18` is correctly recorded for DSR, but DSR is diagnostic here; the binding NO-GO comes from L1-L6 hard gates.

## Decision

Phase D v7 is closed. The research result should be treated as:

- **Capital path**：do not allocate capital to the active top-N v7 strategy; use 0050 DCA as the practical baseline.
- **Research path**：v8 may proceed only as a new pre-registered hypothesis, not as post-hoc retuning of v7.
- **Engineering path**：harden the research platform before v8, especially import reliability, conda testability, evaluator/document consistency, and formal BacktestEngine usage.

## V8 Entry Conditions

Do not start v8 until these are true:

- README / reports no longer imply any v7 cell passed 5/6 by counting only L5 correlation.
- `BacktestEngine` can import and run synthetic integration tests in conda without `pandas_ta` blocking collection.
- Full or documented-targeted pytest has a reproducible command in conda.
- v8 has its own preregistration: sample window, universe, engine path, costs, gates, and max trials are fixed before running.

