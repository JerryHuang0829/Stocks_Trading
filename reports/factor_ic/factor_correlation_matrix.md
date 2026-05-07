# Phase A1 Factor Correlation Matrix

**Method**: per-period Spearman rank correlation, averaged across all overlapping periods with ≥10 common symbols.

**產出**：closes `phase_a1_summary.md` 「相關性矩陣 — skip」 known limitation（Phase A2 Step 4-prep canonical fix 2026-04-21）。

## 5×5 相關係數 (Spearman ρ)

| Factor | 52W_High | PEAD_EPS | Margin_Short | Rev_v2 | Foreign_v2 |
|---|---|---|---|---|---|
| 52W_High | **1.00** | +0.213 | +0.160 | +0.161 | +0.098 |
| PEAD_EPS | +0.213 | **1.00** | -0.072 | +0.384 | +0.108 |
| Margin_Short | +0.160 | -0.072 | **1.00** | -0.062 | -0.123 |
| Rev_v2 | +0.161 | +0.384 | -0.062 | **1.00** | +0.075 |
| Foreign_v2 | +0.098 | +0.108 | -0.123 | +0.075 | **1.00** |

**標示**：🔴 高相關（|ρ|>0.7，冗餘）/ ⚠️ 中相關（|ρ|>0.5，需注意）


## 每對 factor 的採樣期數

| Factor | 52W_High | PEAD_EPS | Margin_Short | Rev_v2 | Foreign_v2 |
|---|---|---|---|---|---|
| 52W_High | 71 | 71 | 71 | 71 | 71 |
| PEAD_EPS | 71 | 71 | 71 | 71 | 71 |
| Margin_Short | 71 | 71 | 71 | 71 | 71 |
| Rev_v2 | 71 | 71 | 71 | 71 | 71 |
| Foreign_v2 | 71 | 71 | 71 | 71 | 71 |

## Weight 建議原則

1. **|ρ|>0.7 的兩 factor**：二選一（選 IR 較高者），另者 weight=0 省成本
2. **|ρ|<0.3 的兩 factor**：可放心同時賦權，diversification 效益明顯
3. **0.3 ≤ |ρ| ≤ 0.5**：共用權重分配，但總 weight 不宜過集中於這對
4. **|ρ| > 0.5**：若一定要都用，考慮其中一個給 0.5× 權重以示減量

## 下一步

依此 correlation 矩陣 + 5 factor IR 數據進 Step 4 weight 討論；
user 決定 config D1-D5 後跑 Step 5 IS/OOS backtest + walk-forward。
