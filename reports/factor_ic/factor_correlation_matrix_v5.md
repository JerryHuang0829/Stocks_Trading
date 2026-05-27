# v5.0 ML Feature Correlation Matrix（7 features）

**Date**: 2026-05-25

**Method**: per-period Spearman ρ, averaged across periods (≥10 common symbols).

**Purpose**: v5.0 ML pre-registration checkpoint — verify 5 raw + 2 SN features are not redundant before feeding into XGBoost / LambdaMART / Optuna nested CV.

## 7×7 Correlation (Spearman ρ)

| Feature | IdioVol | 52W_High | PEAD | Value_EP | Rev_1m | Value_SN | PEAD_SN |
|---|---|---|---|---|---|---|---|
| IdioVol | **1.00** | +0.135 | -0.154 | +0.247 | +0.222 | +0.187 | -0.142 |
| 52W_High | +0.135 | **1.00** | +0.213 | +0.033 | -0.417 | +0.014 | +0.205 |
| PEAD | -0.154 | +0.213 | **1.00** | +0.073 | -0.064 | +0.069 | +0.927 🔴 |
| Value_EP | +0.247 | +0.033 | +0.073 | **1.00** | +0.063 | +0.912 🔴 | +0.062 |
| Rev_1m | +0.222 | -0.417 | -0.064 | +0.063 | **1.00** | +0.060 | -0.060 |
| Value_SN | +0.187 | +0.014 | +0.069 | +0.912 🔴 | +0.060 | **1.00** | +0.072 |
| PEAD_SN | -0.142 | +0.205 | +0.927 🔴 | +0.062 | -0.060 | +0.072 | **1.00** |

**Legend**:🔴 |ρ|>0.7 redundant(consider drop) / ⚠️ |ρ|>0.5 correlated


## Period overlap (n periods used per pair)

| Feature | IdioVol | 52W_High | PEAD | Value_EP | Rev_1m | Value_SN | PEAD_SN |
|---|---|---|---|---|---|---|---|
| IdioVol | 71 | 71 | 71 | 71 | 71 | 71 | 71 |
| 52W_High | 71 | 71 | 71 | 71 | 71 | 71 | 71 |
| PEAD | 71 | 71 | 71 | 71 | 71 | 71 | 71 |
| Value_EP | 71 | 71 | 71 | 71 | 71 | 71 | 71 |
| Rev_1m | 71 | 71 | 71 | 71 | 71 | 71 | 71 |
| Value_SN | 71 | 71 | 71 | 71 | 71 | 71 | 71 |
| PEAD_SN | 71 | 71 | 71 | 71 | 71 | 71 | 71 |

## Symbol overlap (avg symbols per period per pair)

| Feature | IdioVol | 52W_High | PEAD | Value_EP | Rev_1m | Value_SN | PEAD_SN |
|---|---|---|---|---|---|---|---|
| IdioVol | 1788 | 1636 | 1607 | 1300 | 1644 | 1300 | 1608 |
| 52W_High | 1636 | 1636 | 1605 | 1294 | 1636 | 1294 | 1605 |
| PEAD | 1607 | 1605 | 1609 | 1270 | 1608 | 1270 | 1609 |
| Value_EP | 1300 | 1294 | 1270 | 1303 | 1302 | 1303 | 1270 |
| Rev_1m | 1644 | 1636 | 1608 | 1302 | 1647 | 1302 | 1609 |
| Value_SN | 1300 | 1294 | 1270 | 1303 | 1302 | 1303 | 1270 |
| PEAD_SN | 1608 | 1605 | 1609 | 1270 | 1609 | 1270 | 1609 |

## Pairs of interest（high correlation）

| Pair | ρ | Severity |
|---|---:|---|
| PEAD × PEAD_SN | +0.927 | 🔴 redundant |
| Value_EP × Value_SN | +0.912 | 🔴 redundant |

## ML interpretation

- |ρ|>0.7 → ML 會難分辨,SHAP importance 失真;考慮 drop 一個
- 0.5<|ρ|<0.7 → ML 可處理但 regularization 要注意
- |ρ|<0.3 → 高度互補,加進 feature set 有 diversification 效益
- raw vs SN(自我配對)→ 預期中度正相關(SN 是 raw 的清洗版)

## Pre-registration checkpoint

依此矩陣 + 7 因子 IC 數據 → user review:
1. 確認 7 個 feature 全留(無 drop)
2. 或建議 drop 哪些 → 更新 plan + pre-registration
3. sign-off 後進 Step 3:撰寫 `H_v5_0_ml_oddlot_preregistration.md`