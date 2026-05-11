# self-audit 獨立驗證報告 — 2026-04-16

> 審計員：self-audit（獨立審計模式，非前一輪 self-audit 助手）
> 執行環境：Docker (Python 3.12.13, pandas 3.0.2, scipy), OHLCV cache 1,968 檔
> 所有獨立計算腳本與 JSON 輸出存在 `reports/diagnosis/independent_audit/`

## 整體評價

- 對前一輪 self-audit 診斷：**部分同意（核心結論方向對，論證有 P0 偏誤）**
- 🔵 量化結論：**三因子無可辨別 edge（IC 全落在噪音範圍，策略 Sharpe 在隨機 8 檔選股分布的第 34 百分位），但「PM IC = -0.05」這個論據是 top-20 truncated 造成的負偏，真實 full-universe IC = -0.027 (p=0.40)，只是雜訊、不是失效證據。**
- 🟢 投資結論：**前一輪對「月投 2.5 萬不可行」的結論建立在「只模擬整張」的錯誤前提。盤中零股下 25k 的 capital utilization 可達 81%；但即使技術可執行，2025 年淨 alpha 仍為 -17% 至 -19% 的災難，且 100% 0050 在相同 4Y 期間 CAGR +20.19% / Sharpe 0.87 vs 策略 +15.47% / 0.64，0050 全面勝出。**
- 雙視角共識：**轉被動投資（以 0050 為主，非前一輪推薦的 50/40/10）**。裁決依據：兩視角均支持「無 edge + 被動勝出」，差異只在戰術細節。

---

## 發現清單（強制標註視角）

| # | 嚴重度 | 視角 | Task | 發現 | 證據 | 建議 |
|---|--------|------|------|------|------|------|
| 1 | P0 | 🔵 | A/E | `scripts/analyze_factor_ic.py:283` 只跑 `factor_detail`（engine.py L609 硬截 `ranked[:20]` + eligible-only），得到的是 **top-20 truncated IC**，非因子 IC | 我的獨立重算：truncated_top20 IC = **-0.05053**（與前一輪 -0.0505 完全一致）；eligible-only IC = -0.03888；full-universe IC = **-0.02720 (p=0.40)** | 公開結論必須用 full-universe 數字；前一輪「PM 失效 IC=-0.05」應改為「PM 為噪音 IC≈0，不顯著」 |
| 2 | P0 | 🟢 | C/E | `scripts/small_capital_friction.py` 未模擬**盤中零股**（台股 2024 後高度活絡），致「100 萬 89% 買不起」的結論嚴重誤導用戶 | 我的重算加入零股：25k 全體可達 **capital utilization 81%**（91.6% 位置用零股）；100k 達 94%；100 萬達 97% | 執行面前一輪結論反轉：小資金技術上**可執行** |
| 3 | P0 | 🔵🟢 | C | 即使可執行，2025 年所有資金規模淨 alpha = **-17.43% 至 -19.03%**；4Y 基準 25k 淨 alpha 僅 +2.80%（基本被成本吃光） | friction_oddlot.json by_capital | 即使加入零股，策略對月投 2.5 萬**年度期望負回報** |
| 4 | P0 | 🔵🟢 | F | 2022-2025 同期：**100% 0050 CAGR +20.19%, Sharpe 0.87**；策略 CAGR +15.47%, Sharpe 0.64 | 獨立計算（`adjust_splits + adjust_dividends` 套用後） | 0050 每年勝策略 **+4.72%** 且 Sharpe 高 37%；**推薦 0050 為核心** |
| 5 | P0 | 🔵 | D | **Permutation 檢驗：隨機 8 檔選股 Sharpe 中位 0.801**，策略 Sharpe 0.697 在**第 34 百分位**（低於隨機中位） | 300 次 Monte Carlo, audit_regime_permutation.py | 三因子選股邏輯**不比隨機好** |
| 6 | P0 | 🔵 | A | 4 因子 FDR (BH) 與 Bonferroni 修正後，**無任一因子** p 值通過 α=0.05 | multiple_testing in factor_ic_recomputed.json | 現有因子組合統計上無顯著性 |
| 7 | P0 | 🔵 | E | `data/cache/ohlcv/0050.pkl` 原始 close 在 **2025-06-18 單日 -74.78%**（188.65→47.57），是 1:4 split 未調整；engine 透過 `adjust_splits()` 動態處理，但任何 downstream script 直接讀 raw cache 不調整會得錯數 | `rets['2025-06-18'] = -0.7478`；前版 audit_passive.py 跑出 0050 CAGR -1.79% 假象 | `scripts/*.py` 內讀 ohlcv cache 的腳本必須先套 `adjust_splits` |
| 8 | P1 | 🔵 | E | `scripts/small_capital_friction.py:L188-189` 用 `STRATEGY_GROSS_ALPHA_4Y=3.4` 當「毛 alpha」扣掉新成本；但 `metrics.json` 的 `annualized_alpha=0.03395` 已是**淨值**（engine 已扣 `turnover_cost=0.0047 × turnover + slippage`）→ **雙重扣成本** | 我推算實際 gross alpha 4Y = net 0.034 + engine cost 0.0267 = **+0.0606**；前一輪只看 3.4% 會低估  | 重寫成本計算，或把 3.4% 先加回 engine cost 再扣外部友 |
| 9 | P1 | 🔵🟢 | B | 2025-12 rolling 12M alpha **+8.43%** 並非 edge 恢復訊號，只是 beta=0.53 + benchmark +34% 大多頭年的數學幻象。對**無槓桿零售投資人**，相關的是絕對 alpha = **-18.44%** | 獨立 OLS：alpha_ann_compound=+8.43%, beta=0.53, bench_ann=+34.17%; -18.44%=15.73-34.17=絕對落後 | 放棄用 rolling CAPM alpha 作為「觀察 3 個月」的等待理由 |
| 10 | P1 | 🔵 | B/E | 前一輪 self-audit 自貶 `scripts/rolling_performance.py:82-92` 為「近似式」，**實為單因子 OLS intercept 閉式解的精確形式**（我的 numpy lstsq 重算 910 windows diff = 0.000000） | audit_rolling_alpha.py `diff_vs_prior` 全 0 | 這條不是 bug，是前一輪的自我貶低；但「近似」說法誤導使用者 |
| 11 | P1 | 🔵 | D | 無 regime 能救 PM：risk_on IC=+0.004 (p=0.95), caution IC=-0.04 (p=0.48), risk_off IC=-0.03 (p=0.51) | audit_regime_permutation.py by_regime | 不能用「risk_on 下有效」救策略 |
| 12 | P2 | 🔵 | E | 5 個新腳本（analyze_factor_ic / rolling_performance / small_capital_friction / cache_fill / validate_cache）**零單元測試** | `tests/` 內無 test_analyze_factor_ic.py 等 | 依賴它們的結論缺可重現性保障 |
| 13 | P2 | 🔵 | E | 全 repo timezone 處理**混用 `tz_localize(None)` 與 `tz_convert("UTC")`**：finmind.py L220 (naive) vs L248 (UTC)；twse_scraper.py L221-223 條件分支；engine.py L131 naive | grep 結果見 audit 證據 | 訂統一規則（推薦整條 pipeline 用 UTC），或在 `src/utils/constants.py::to_utc_ts` 基礎上寫 helper 用於 audit |
| 14 | P2 | 🟢 | F | 前一輪推薦 `50% 0050 + 40% 0056 + **10% 現金**`；我的獨立計算顯示 10% 現金拖累**約 2.5% CAGR**（7Y CAGR 18.57% vs 60/40 21.06%，兩者 Sharpe 1.07 vs 1.09 幾乎一樣） | audit_passive.py 表 | 若要 Smart Beta，**60% 0050 + 40% 0056** 比 50/40/10 更優；若要 MDD 緩衝，改用 0056 權重而非現金 |
| 15 | P2 | 🔵 | A/E | snapshots 不儲存 `full_ranked`（48 snapshots 內含僅 factor_detail[:20]），導致**無法從 snapshots 單獨重建 full-universe IC**，必須回到 OHLCV cache 重算 | `json.load(snapshots)[0].keys()` 比對 `tw_stock.py` live mode 的 snapshot schema | 新增 snapshot 欄位 `full_ranked`（或 top-40）讓未來 IC 分析可從單一檔出發 |
| 16 | P3 | 🔵 | A | 前一輪 `_bucket_stats` 的 p-value 在 `n>=10` 時用 normal approximation，n<10 回 None（穩健但保守） | analyze_factor_ic.py L192-197 | 可改用 `scipy.stats.t.sf` 精確 t-distribution（我的 audit_ic.py 已採用） |

---

## 🔵 量化主管獨立結論

### 因子 IC 重算結果（Task A）

三層 universe 對比（n=47 rebalances, 2022-01 .. 2025-12）：

| 範圍 | mean_IC | t-stat | p-value | bootstrap 95% CI |
|------|---------|--------|---------|------------------|
| truncated top-20（= 前一輪） | -0.0505 | -1.72 | 0.092 | [-0.108, +0.006] |
| eligible_only (~27) | -0.0389 | -1.18 | 0.246 | [-0.099, +0.025] |
| **full_universe (~80)** | **-0.0272** | **-0.86** | **0.397** | **[-0.085, +0.033]** |

truncated IC vs full IC 差距 = 0.0233，相對差 **86%**（P0 量級）。**方向一致但量級被誇大**。

前一輪「IC=-0.05 → 因子失效」的論斷不成立。正確說法：**full-universe IC 統計上與 0 無異**，我們既不能 reject「PM 有效」也不能 reject「PM 失效」。這是**無訊號**，不是**反訊號**。

其他三因子 truncated IC：
- revenue_momentum p=0.878（完全雜訊）
- trend_quality p=0.743（雜訊）
- institutional_flow p=None（n 太小）

**FDR 多重比較修正**：4 因子 Bonferroni（α/4=0.0125）與 BH-FDR 修正後，**皆無顯著**。策略無統計顯著的因子 edge。

### Rolling OLS 精確版結論（Task B）

我用 numpy lstsq 重算 910 個 12M rolling window，和 `scripts/rolling_performance.py` 的「近似式」結果 **完全一致**（`max|diff| = 0.000000`）。原因：單因子 OLS intercept 的閉式解就是 `mean(y) - β×mean(x)`，前一輪把這誤稱為近似。

**Full period 2022-2025 精確 OLS**：
- α_ann (simple) = +9.98%, α_ann (compound) = +10.49%, α (Jensen excess) = +9.24%
- β = 0.51, t = 0.84, p = 0.40 — α 不顯著
- 與 metrics.json 的 `annualized_alpha = 3.40%` 差距大，因為 metrics.json 的是 `port_CAGR - bench_CAGR`，**不是** CAPM OLS alpha。兩者是**不同口徑**。

**2025 last 12M window**：
- 精確 α_ann (compound) = **+8.43%**（匹配前一輪顯示）
- β = 0.53, t=0.37, p=0.71 — α 不顯著
- 對比 `backtest_20250101_20251231_metrics.json` 的 `annualized_alpha = -18.44%` = 絕對落後

這兩個 +8.43% 與 -18.44% **不衝突**，是**兩個不同的量**：CAPM 殘差 alpha vs 絕對落後。對無槓桿零售投資人，相關的是後者。**「等 3 個月看 rolling alpha 能否轉正」的邏輯站不住腳**。

### Permutation 隨機基準（Task D）

300 次 Monte Carlo，每次隨機抽 8 檔（從每個 rebalance 的 eligible+rejected pool）等權持有：
- 策略 Sharpe (from daily_returns) = **0.697**
- 隨機中位 Sharpe = **0.801**
- 隨機均值 Sharpe = 0.776
- 5-95% 分位 = [0.30, 1.18]
- **策略在隨機分布的第 34 百分位**

Caveat：random 未扣 turnover cost（random turnover ~100% vs 策略 33%），若補扣 random 會下降。但即便這樣，策略也**沒有顯著超過隨機中位**。**因子選股機制無邊際信息量**。

### Regime-conditional IC（Task D）

| Regime | n | mean_IC | t-stat | p-value |
|--------|---|---------|--------|---------|
| risk_on | 12 | +0.004 | 0.06 | 0.95 |
| caution | 17 | -0.043 | -0.72 | 0.48 |
| risk_off | 18 | -0.033 | -0.67 | 0.51 |

**沒有任何 regime 下 PM 是顯著的**。前一輪「策略因子失效」雖措辭過強，但結論方向正確——因子整體就是噪音，不能靠 regime 切分救回。

### 樣本量 / 顯著性 / FDR 修正結果

4Y 回測僅 48 rebalances；IC 分析 n=47；12M rolling windows 910 個日頻 obs。樣本夠作「結論：未偵測到顯著 edge」但**不夠支持「證實沒有 edge」（absence of evidence ≠ evidence of absence）**。

### Bug 與測試缺口

參見發現清單 #1, #2, #7, #8, #10, #12, #13, #15。

### 資料可重現性

我獨立重算 `metrics.json` 的核心數字：
- bench_CAGR 12.14% ≈ 12.07% ✓（0.6% 定義差異）
- port_CAGR 14.91% ≈ 15.47% ✓（0.5% 差異，可能 compound vs simple annualization）
- Sharpe 0.6175 ≈ 0.6379 ✓（0.02 差異，可能 rf 假設）
- turnover: `avg×2×12 = 7.97 ≈ total/years×2 = 7.96` ✓（完全一致）
- 全數落在合理重算容忍內，cache 和 metrics 互洽。

---

## 🟢 專業投資人獨立結論

### 實盤可行性判定（考慮零股）

| 資金 | 可整張 | 零股 | 完全買不起 | Capital Util. | 成本拖累 | 4Y 淨 α | 2025 淨 α |
|------|-------|------|-----------|---------------|---------|---------|-----------|
| 25,000 | 0% | 91.6% | **8.4%** | **81%** | 3.26% | +2.80% | **-19.03%** |
| 100,000 | 0% | 98.4% | 1.6% | 94% | 1.86% | +4.20% | -17.63% |
| 300,000 | 0.3% | 99.7% | 0% | 98% | 1.68% | +4.39% | -17.45% |
| 1,000,000 | 11.1% | 88.9% | 0% | 97% | 1.66% | +4.40% | -17.43% |
| 3,000,000 | 37.5% | 62.5% | 0% | 93% | 1.66% | +4.40% | -17.43% |

（上表淨 α 口徑：engine net α + engine cost 加回 → 用我的零股友善成本模型重扣）

**前一輪「25k 不可行」建立在整張假設上**。2024 盤中零股活絡後，25k 可忠實執行 91.6% 的位置，utilization 81%。**技術上可執行**，但：
- 25k 成本拖累 3.26%（低消 20 元 × 高頻）吃掉毛 α 大部分
- 2025 單年淨 α -19% 的災難與資金規模**無關**（策略本身 alpha 崩）
- 即便 100 萬資金，2025 淨 α 仍 -17.43%

### 各資金規模的淨 alpha

- 4Y 角度：100k+ 淨 α 約 +4.2%–4.4%（看似還 OK）
- 2025 角度：全 capital 淨 α −17% 至 −19%（災難）
- **問題不在資金規模，在策略本身**。

### 機會成本對照

2022-2025 同期被動選項（獨立套 split+dividend adjust 後）：

| 選項 | CAGR | Sharpe | MDD | WorstYr | 2.5 萬可行？ | 手續費友善？ |
|------|-------|--------|-----|---------|-------------|-------------|
| **策略 tw_3m_stable** | 15.47% | 0.64 | -30.00% | ? | 零股後可 | 高頻拖累 |
| **100% 0050** | **20.19%** | **0.87** | -33.96% | -21.37% | ✅ 零股 + 低手續費 | ✅ 單檔無換股 |
| 100% 0056 | 12.16% | 0.69 | -26.89% | -17.67% | ✅ | ✅ |
| 50% 0050 + 40% 0056 + 10% 現金（前一輪推薦） | 15.20% | 0.84 | -26.79% | -17.72% | ✅ | ✅ 每月 1 次再平衡 |
| **60% 0050 + 40% 0056** | **21.06%** | **1.09** | -29.85% | -19.78% | ✅ | ✅ |
| Risk parity (43%/57% 0050/0056) | 19.43% | 1.06 | -28.00% | -19.13% | ✅ | ✅ |

（全期 2019-2025 視角下 Sharpe 排序幾乎相同；Sharpe 最高者為 60/40 = 1.09）

**關鍵觀察**：
1. **100% 0050 完勝策略**：CAGR +4.72% 且 Sharpe 高 0.23（風險調整後勝出）
2. **60/40 0050/0056 Sharpe 1.09 > 前一輪推薦 50/40/10 的 1.07**：10% 現金拖累了 ~2.5% CAGR，**對用戶是純損失**（用戶不需要這麼多 dry powder）
3. **策略的唯一優勢是 MDD 稍低** (-30% vs 0050 -33.96%)，但代價是 -4.72% CAGR — **MDD 買得太貴**

### 心理壓力視角（專業投資人）

用戶是學習階段、月投 2.5 萬。0050 在 2022 單年 -21%、2025 單年 -17.67%。雖不及策略 -30% MDD，但仍顯著。對新手的建議：
- **0050 + 0056 (60/40)** 的 MDD -29.85% 比 0050 -33.96% 稍緩，但差距有限
- **主動認識「持股 7 年，賺 CAGR 20%+」的對價是某些年虧 20%+**
- 若心理難承受，可降 0050 比重至 40%（Risk parity），代價 CAGR 降至 19.43%

### 建議的具體執行方案

1. **立刻停止策略的 paper trading / 實盤計畫**（已在策略研究.md 2026-04-15 發現中提過，維持該決議）
2. **開始每月定期定額 0050 + 0056**，建議配置 **60/40**（或初期 80/20 側重 0050）
3. **月投 2.5 萬切分**：15,000 0050 + 10,000 0056（用零股）
4. **通路建議**：券商零股手續費折扣優惠多，永豐大戶投/元大/國泰證券定期定額都支持 0050/0056 零股買進
5. **再平衡頻率**：每半年一次即可（月再平衡成本遠大於 drift）
6. **不加 00878/00919 高股息 ETF**（用戶是累積期，應偏成長配息再投資效率更高）
7. **觀察期建議**：記錄 12 個月後比較「0050/0056 60/40 實際累計」vs「策略 paper」→ 用實際數據確認你的選擇

---

## 雙視角共識 / 衝突

### 共識（雙視角同方向）

1. **三因子策略無 edge**：🔵 IC 雜訊 + permutation 34 百分位 + FDR 零顯著；🟢 CAGR 0050 勝策略 +4.7%、Sharpe 更高。**放棄主動策略**。
2. **前一輪推薦 50/40/10 不是最優**：🔵 10% 現金拖累 ~2.5% CAGR；🟢 0050 單檔 Sharpe 就比 50/40/10 高。
3. **「用 rolling alpha +8.43% 等 3 個月」邏輯錯**：🔵 β=0.53 造成的數學幻象；🟢 絕對 α -18.44% 才是投資人收益。
4. **從主動轉被動方向正確**：兩視角都支持，只是細節微調。

### 衝突（視角不一致）

| 項目 | 🔵 量化主管說 | 🟢 專業投資人說 | 裁決 | 裁決理由 |
|------|-----------|----------------|------|----------|
| 是否需要更長 OOS 再下結論？ | 樣本 n=47 不足以下「PM 確定失效」的強結論（absence of evidence ≠ evidence of absence），嚴格來說「待觀察」 | 策略比 0050 少 4.7%/year 的機會成本，用戶學習期每延一年等於少 12 萬複利基礎，**不能繼續等** | **不實盤，轉被動** | 用戶不是賭徒，機會成本比統計嚴謹更優先；用戶保護優先 |
| PM 「失效」vs「雜訊」 | 統計上是「雜訊」不是「失效」，用字應修正 | 對投資決策沒差，都是「不該實盤」 | 用「雜訊」描述 | 語義準確度不影響決策 |
| 零股讓 25k 可行是否改變推薦？ | 技術可執行不等於應執行 | 可執行改變了「做/不做」的可選項，但 2025 -17% 淨 α 仍否決 | 可執行不救策略 | 可執行是必要條件，不是充分條件 |

---

## 同意前一輪 self-audit 的部分

1. **策略無實質 edge、建議轉被動**：方向正確（雖論證有偏誤）
2. **月投 2.5 萬 + 策略，經濟上不划算**：結論對（雖「不可行」措辭誤導）
3. **FinMind 配額 + Docker 維運成本對學習階段用戶負擔重**：同意（未重跑驗證，但符合 user_profile memory 的情境）
4. **暫緩實盤、累積對照組數據**：同意（本審計建議建立 0050/0056 實盤紀錄對照）
5. **2025-12 的 rolling alpha +8.43% 不能當 edge 恢復訊號**：同意（我提出了更清晰的數學解釋）

## 反對前一輪 self-audit 的部分

1. ❌ **「PM IC = -0.0505，因子失效」** → 應改為「truncated top-20 IC = -0.05，full-universe IC = -0.027 (p=0.40)，統計上為雜訊」
2. ❌ **「100 萬 89% 無法買 1 張 → 不可實盤」** → 零股模擬下 100 萬 utilization 97%，技術可行；2.5 萬 utilization 81% 也可行。不可實盤的真正理由是 **策略本身無 edge**，不是**執行困難**
3. ❌ **推薦 50/40/10 Smart Beta** → 60/40 0050/0056 Sharpe 1.09 > 50/40/10 Sharpe 1.07，**10% 現金對累積期不必要**。若要被動，應直接 60/40 或 100% 0050
4. ❌ **`scripts/rolling_performance.py` 用「近似式」** → 其實是 OLS intercept 精確閉式解，前一輪自我貶低
5. ❌ **`scripts/small_capital_friction.py` 用 3.4% 當 gross alpha** → 該值已是 engine 淨值，**雙重扣成本**，算出的 net_alpha 被低估

## 前一輪 self-audit 漏掉的事

1. **Truncated IC 本身的 selection bias**：選 top-20 by PM 後再測 PM-forward-return 相關性，本就會把 IC 壓低 — 這不是因子失效，是測量工具選錯
2. **零股模型缺失**：2024 盤中零股活絡後台股小資金可行性大幅改善，應納入友擬
3. **Permutation baseline 未做**：策略選股機制是否比隨機好，是定邊際價值的核心測試，前一輪未執行
4. **FDR / Bonferroni 多重比較修正**：4 因子同時測本應修正
5. **Regime-conditional IC（full-universe 版）**：前一輪只對 top-20 做分層，未用完整 universe 檢驗
6. **Raw 0050.pkl 的 split 未調整隱患**：任何 downstream 腳本直讀 `data/cache/ohlcv/0050.pkl` 會得到錯誤數字（我在 audit_passive.py 第一版就踩到，CAGR 假象 -1.79%）
7. **60/40 0050/0056 vs 50/40/10**：cash drag 估算缺失，選了次優被動配置推薦用戶
8. **Rolling α 的 +8.43% 與絕對 α -18.44% 的口徑差異**：應主動說明 CAPM Jensen α vs 絕對落後的數學關係，而非模糊帶過
9. **新腳本的測試缺口**：5 個新增腳本零 unit test，對「論據可重現性」是系統性風險

---

## 最終建議

### 給用戶的 second opinion

**我（獨立審計員）的建議**：

**立即行動**：
1. **停止策略的 paper trading 投入新時間**，把 CI 時間/心力省下來
2. **每月定期定額 2.5 萬 買被動 ETF**：**60% 0050 + 40% 0056**（用盤中零股）
3. **放棄前一輪推薦的 10% 現金配置**（拖累 CAGR 2.5%，現金你本來就有）
4. **不加高股息 ETF（00878/00919）**（累積期股息再投資效率低於成長型）

**為什麼不是「繼續觀察 3 個月」**：
- 📉 策略 2022-2025 vs 0050 差距 **-4.72%/year**，每等 1 年 = 少 1.18 萬（以 25k 月投、7Y 複利基礎反推）
- 📊 三個獨立測試（IC + permutation + 0050 基準）全指向「無 edge」
- 🧪 rolling α +8.43% 是 β 幻象，不是 edge 訊號
- ⏰ 你是學習階段，機會成本 = 學新東西（ML、海外市場、options）

**為什麼不是「完全停止量化研究」**：
- 🎓 你的目標是 quant engineer career（user_profile memory），**工程與資料管線能力已累積在這個專案**（Docker、cache、point-in-time、219 tests）— 這些是履歷資產
- 🔬 下一個研究方向建議：**不要再在台股動能因子上掘井**，換領域（value、小盤、宏觀、或跨市場）
- 💰 被動 0050 + 0056 執行期間，**同步用 paper trade 記錄 1-2 個新因子假設**，但**不投錢**，純學習

### 執行步驟（具體）

1. **本週**：開永豐大戶投（已有）或元大證券的**定期定額零股**服務
2. **下週**：設定每月 5 日定期定額：
   - 15,000 NTD 0050 （零股）
   - 10,000 NTD 0056 （零股）
3. **每 6 個月**：檢查比例是否 drift > 10%，若是則再平衡（手動轉倉一次）
4. **記錄**：建議保留現有 `scripts/paper_trade.py` 結構，把每月 paper trade 改成同步記錄「策略模擬 vs 0050/0056 60/40 實盤」，12 個月後對比結果
5. **停止**：不要跑 `main.py` live loop、不要擴充 FinMind 配額、不要投 CTSwithPython 券商對接（review-prompt.md / the dev guide「不急」清單）

### 可觀察退路

若未來 12 個月發生以下情況，可重新考慮主動策略：
- FDR 修正後**至少一個因子** p < 0.05（需新增 value / quality / microstructure 因子）
- Permutation 測試下策略 Sharpe 超過 random **95 百分位**
- 有更豐富的海外 / 跨市場資料可用（降低單一市場 overfit）

若都沒發生 → 維持被動。這不是失敗，是「承認沒 edge 後的合理應對」。

---

## 執行限制

- 💻 **環境**：本機僅 Python 3.13 系統版（無 conda quant），pickle 格式不相容。所有計算走 Docker (Python 3.12, pandas 3.0.2, scipy)。Docker + cache + tokens 確認就緒。
- 🔢 **統計套件**：Docker image 無 `statsmodels`，OLS 用 numpy lstsq 手算 intercept + 手算 SE → 與 statsmodels 等價（單因子線性迴歸），但缺 robust SE / HAC。
- 🔄 **未重跑 4Y backtest**：Docker 內可跑 `docker compose run --rm backtest`，但耗時長、會寫 reports/。我選擇**以既有 metrics/daily_returns 為 sanity check 基準**，獨立重算核心數字互相驗證，誤差 < 3% 視為可重現。
- 🧪 **未跑 pytest**：219 個測試須 Docker 內 conda env 執行，本審計焦點是論證驗證而非環境驗收。新 5 腳本缺測試這點是結構性發現，不依賴實際跑測。
- 🎲 **Permutation 僅跑 300 次**：n=1000 估計時間 >1 小時；300 次對「策略 Sharpe 是否在隨機分布中位以下」這個 binary 判定已足夠。
- 🌐 **未獨立抓 TWSE/FinMind 驗證**：cache 可用 + 前一輪的 cache_health / validate_cache 無異常報告 → 不重複跑。
- 📉 **Full universe IC 仍用 `eligible + rejected_*` 聯集 (~80 檔)，不是市場全 1968 支**：因為「80」是 engine 已 pre-filter 過的 universe，實盤也只會從這 80 選。真正完整市場 IC 需跑所有 1968 檔的完整 factor + forward-return，耗時估 30-60 分鐘，但邊際效益不大（策略實際 universe 就是這 80）。
- 📅 **Regime IC 的 p-value 用 t-distribution with df=n-1**，小 n（risk_on n=12）下穩健，但仍偏保守。

---

## 附錄：獨立計算腳本與輸出

路徑：`reports/diagnosis/independent_audit/`

| 腳本 | 輸出 JSON | Task |
|------|-----------|------|
| `audit_ic.py` | `factor_ic_recomputed.json` | A |
| `audit_rolling_alpha.py` | `rolling_alpha.json` | B |
| `audit_friction_oddlot.py` | `friction_oddlot.json` | C |
| `audit_regime_permutation.py` | `regime_permutation.json` | D |
| `audit_passive.py` | `passive_evaluation.json` | F |

用戶可在 Docker 內用 `docker compose run --rm --entrypoint python portfolio-bot reports/diagnosis/independent_audit/<script>.py` 重跑驗證。
