# 5 因子 IC — Pro Sprint 重現版

本目錄是 Pro Validation Sprint Phase B 的 **5 因子 IC 重現** evidence，**與 [reports/factor_ic/](../../../factor_ic/) 不同**：

| 目錄 | 性質 |
|---|---|
| [reports/factor_ic/](../../../factor_ic/) | **Phase A1 原版**（2026-04-16~20 第一次跑出來的 IC 結果）|
| 本目錄（B_repro/factor_ic/）| **Pro Sprint 重現版**（2026-05-04 commit `0d31572` 鎖定 anchor 重跑，post 2 P0 fixes）|

兩者**不重複**，是時序上不同 commit 的 IC 計算結果，用於：
1. 驗證 Pro Sprint 的 reproducibility（IC drift ≤ 1%）
2. 對照 Phase A1 → 2026-04-15 揭穿 overfit 後 P0 修法的 IC 真實變化

每個 `*_ic.md` 含 `**Reproducer commit**: 0d31572` 為 anchor。

`*_ic.json` 為對應的 raw 數據（per-period IC / bootstrap CI / FDR / DSR / effective_n）。
