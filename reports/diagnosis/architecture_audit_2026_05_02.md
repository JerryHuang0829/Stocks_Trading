# Architecture Audit — Quantitative-Trading × Options_Trading 跨 repo Pro Standard Sweep

**Audit 日期**：2026-05-02
**Auditor**：self-audit (D.0 — read-only)
**範圍**：本 repo `src/` 全核心檔 + `Options_Trading` repo 工程紀律對照
**Scope 限制**：read-only，不改任何 code；不重做 Phase A1-A3 結論
**對照基準**：Options_Trading R11.x → R12.13 連 13 輪 獨立 audit 累積的 19-pattern self-audit + skill chain + audit_doc_drift architectural fix

---

## TL;DR — Verdict

**🟡 GO-WITH-CAVEATS**：本 repo PIT discipline 與 silent-fallback 防線**整體達 Pro Retail 標準**（修前 vs 修後 Sharpe 1.88 → 0.66 那 3 個 silent bug 已落實到計算路徑 raise）。但**工程紀律（self-audit / forensic-sweep / doc drift gate）落後 Options_Trading 約 9 個月**——本 repo 還停在 6 步 SOP，Options 已升到 19-pattern + 自動化 architectural gate。

**重啟新研究（D.2）前的最低必修 P1**（~3-4 hr）：
1. **遷移 `audit_doc_drift.py`** + `self-audit` 19-pattern + `forensic-sweep` 兩 skill（本 repo 已有 `multi-perspective` 對應 `dual-audit`，但 self-audit / forensic-sweep 完全缺）
2. **修 1 個 silent-imputation 計算路徑**（`_metric_ranks` 把缺資料股票補 0.5 median rank）
3. **新增 1 個 PIT discipline test**（覆蓋 line 678-691 兩個 `as_of` warning 點，避免重啟 IF 因子時遺漏）

**修不修不影響「能不能跑」，影響「結論能不能信」**——D.2 long/short market neutral 工程量大，現在底層補強的 ROI 比未來修補高。

---

## Section A — Architectural Risk 候選（≥5 條，含 file:line + ROI 估）

### A.1 ⚠️ HIGH — `_metric_ranks` 缺資料股票補 0.5 median rank（silent imputation）

**位置**：[src/portfolio/tw_stock.py:1518-1539](../src/portfolio/tw_stock.py#L1518-L1539) + [:1542-1601](../src/portfolio/tw_stock.py#L1542-L1601)

**Risk**：`output = {item["symbol"]: 0.5 for item in items}`（line 1536/1561）為所有 items 預設 median rank 0.5。當個別股票 factor=NaN/Inf（< 50% 觸發 guard 之下），它會默默拿到「不偏不倚」的中位數 → 還能進入 `_select_positions` 的 top_n。

對 Pro 來說這是 **Pattern 6 silent fallback 假通過**：
- 本應的行為：缺資料 → 從該因子的 ranking 排除 → 不影響其他股票排名 → 該股不進 top_n（或標記 `data_quality_low`）
- 現行行為：缺資料 → 給 median 50 percentile → 仍可進 top_n 因其他因子高分 → 「其實這支根本不該被打分」的事實被 mask

**搭配實證**：
- Phase A3.1 D1_v3a / D1_v3b 全 gate fail 的根因 plan 假設是「sector_neutral 分散傷 alpha」，但 silent imputation 也可能是**次因**（小 sector 進 pool 時補 0.5 → 排序汙染）
- `_metric_ranks_sector_neutral` 在 R3.1.4 second-pass pool fix 已補強（pool_items 重新 ranking），但 cross-sectional path（`sector_neutral=False`）的 0.5 median 沒動

**修法 ROI**：低工程量、高研究信度收益。建議改為：
```python
output = {item["symbol"]: None for item in items}  # 不打分 = sentinel
# ranks.items() 只填有資料的
# _rank_analyses 收 None 後排除 from active_weights normalization（per-symbol level）
```
**工程量估**：~2 hr（含改 `_rank_analyses` consume None 邏輯 + 補 unit test 驗 NaN 不 mask）。

---

### A.2 ⚠️ HIGH — 兩個因子 fetch 路徑沒接 `_DataSlicer` 截斷（隱性 look-ahead 待爆）

**位置**：[src/portfolio/tw_stock.py:678-682](../src/portfolio/tw_stock.py#L678-L682) + [:689-700](../src/portfolio/tw_stock.py#L689-L700)

**Risk**：
- `source.fetch_institutional(symbol)`（line 681）— legacy 法人因子直接抓，**未透過 slicer.set_as_of 截斷**
- `source.fetch_financial_quality(symbol)`（line 692）— quality 因子同樣未截斷
- 兩個 callsite 都有警語 `# WARNING: ... 未傳遞 as_of 截斷。目前 weight=0% 安全隔離。若啟用此因子，必須先實作 as_of 注入（P4.7）`

**為什麼是 architectural risk 不是「只是 weight=0」**：
1. **重啟條件不會自動 enforce**：未來改 yaml 把 `institutional_flow` weight 拉回 > 0（或新增 quality factor），**沒有任何 runtime guard 會擋下** look-ahead — 因為 fetch 路徑還是繞過 slicer
2. **依賴 caller 看註解**：Pattern 0 教訓「pre-design attack 必列 ≥5 attacker tests」沒被釘進 architecture
3. **TestFixture 沒覆蓋這個路徑**：[tests/test_zero_weight_skip.py](../tests/test_zero_weight_skip.py) 驗的是「weight=0 跳過」，**不是「weight>0 時是否 PIT-correct」**

**對應 Options_Trading 經驗**：R12.2 P14 升級加了 sub-rule **(c) cross-frame state lifetime** — 「Producer (slicer) go out of scope **之前** snapshot 到 dataclass; 不可事後 retrieve」——本 repo 是反過來「Producer (slicer) 在但 Consumer (fetch_institutional) 不用」。

**修法 ROI**：中。即使現在 weight=0 不啟用，也應該：
- 把 `fetch_institutional` / `fetch_financial_quality` 加進 `_DataSlicer` cover 範圍（從 `self._source` 透過 `_truncate_by_date_col` 過 as_of）
- 增 unit test：`weight>0 + as_of=2022-01-01` 時 fetch 回來的 max(date) <= as_of

**工程量估**：~3 hr（兩個 fetch method + slicer cover + 2 個 PIT test）。

---

### A.3 🟡 MEDIUM — `tw_stock.py` 1750 行單檔過巨 + `_analyze_symbol` / `_rank_analyses` / `_select_positions` 跨 700 行混雜

**位置**：[src/portfolio/tw_stock.py](../src/portfolio/tw_stock.py)（1750 行，本 repo 單檔最大）

**對照**：Options_Trading 同層級檔案最大：
- `src/data/taifex_loader.py` 623 行
- `src/options/pricing.py` 260 行
- `src/strategies/iron_condor.py`（單策略 file 獨立）

**Risk**：
- 修一個 factor 邏輯需要在同檔讀懂 `build_tw_stock_universe → _analyze_symbol → _compute_universe_batch_factors → _batch_precompute_and_analyze → _rank_analyses → _select_positions` 整鏈才不會踩雷
- 「Plan 假設錯」（A3.1 sector_neutral）的根因之一是 `_metric_ranks` 跟 `_select_positions` 距離 600 行，後者的 `max_same_industry=3` 約束跟前者的 sector_neutral 並不一致 — 但檔案結構讓這個矛盾很難看到
- 獨立 audit 對單檔 1750 行的 cognitive load 太高，2026-04-15 兩個 silent bug（universe pre-filter / timezone）就是因為跨函式範圍很大才漏抓

**修法 ROI**：低（純 refactor，不改邏輯，但拆成 `portfolio/{universe.py, analyze.py, ranking.py, selection.py}` 4 檔可以讓 audit 與 external audit 都能聚焦）。**不建議現在做**——D.2 新方向若選 long/short 等同要重寫 `_select_positions`，refactor 後立即又要改。建議拍板 D.2 後一併處理。

---

### A.4 🟡 MEDIUM — Test fixtures 完全是 synthetic，缺真資料邊界覆蓋（Pattern 9）

**位置**：[tests/conftest.py](../tests/conftest.py)（88 行；fixtures 全 synthetic：固定 10 支股 / 寫死 portfolio_config / 寫死 market_view）

**對照**：[Options_Trading/tests/conftest.py](../../Options_Trading/tests/conftest.py)
- `synthetic_chain` fixture 走真實 `generate_chain` 並產出 24-col enriched schema（與 production schema 一致）
- 已 acknowledge Pattern 9 限制（`pytest.skip("mock_broker fixture pending Phase 2")`）
- R11.4 P1 ：tmp_path override 把 cleanup 落 repo 內 `tests/_tmp/` 避開 external audit env Windows AV 鎖

**Risk**：
- `make_analysis(...)` （line 51-71）寫死 `momentum_12_1=pm/100.0`、`industry="電子工業"` 等預設值 — Pattern 16 「helper 預設值偷工」風險
- 沒有 fixture 抽真 cache 邊界（最早可用日 / 早期 IPO / 假日前後 / split 日 / pandas 2.x tz_convert 邊界）
- Phase A1 422 tests 雖綠，但「real-data boundary」幾乎全不在 fixtures 裡 — 真要 Pro 級研究，需要 1-2 個 fixture 從 `data/cache/ohlcv/2330.pkl` / `0050.pkl` 抽真實邊界片段（小檔 + commit 進 repo）

**修法 ROI**：中。建議補 3 個 real-data fixtures：
1. `real_ohlcv_2330_pre_2020`（早期 IPO + 252-day buffer 邊界）
2. `real_ohlcv_2024_06_concentration`（A3 大權值股獨舞月真實片段，可直接 reproduce 過去結論）
3. `real_dividends_2022_split_combo`（splits + dividends 同期測 metrics adjust）

**工程量估**：~4 hr（含資料切片、entry to .gitignore 反向白名單、補 `test_real_fixtures.py`）。

---

### A.5 🟡 MEDIUM — `_DataSlicer.fetch_ohlcv` 內 `df.tail(limit)` 可能 silently truncate 早期歷史

**位置**：[src/backtest/engine.py:179-192](../src/backtest/engine.py#L179-L192)

**Risk**：
```python
def fetch_ohlcv(self, symbol, timeframe, limit=100):
    ...
    df = df[df.index <= self._as_of]
    return df.tail(limit) if not df.empty else None
```
- `limit` 預設 100，但 caller 例如 `_compute_universe_batch_factors` line 1038 傳 `500`、`_compute_daily_returns` 傳 `self._ohlcv_min_fetch=2000`
- 若 caller 傳 `limit=100` 但 period_start 在 100 個 trading day 之前 → 早期報酬被 silently 截掉 → daily_returns 短缺 → KPI annualization 失準
- 沒有 assertion 「if period_start < df.index[0] → raise」

**對照 Options_Trading**：R12.2 P0 sub-rule 第 (a) 條 **Lookback prerequisite**: 「任何 regime/HMM/percentile gate lookback >= N → 真實 load 必 pre-load N 天 BEFORE backtest start (`pre_start_returns < lookback → raise`)」— 本 repo `_compute_daily_returns` 沒這層。

**現實風險評估**：低（caller 大都傳 ohlcv_min_fetch=2000，hit edge case 機率低），但屬於 latent silent bug，**不到 P1 但建議補 assertion**。

**工程量估**：~30 min（`fetch_ohlcv` 內補 lookback warning + 1 unit test）。

---

### A.6 ⚠️ HIGH — 沒有 `audit_doc_drift.py` 這類 architectural automated gate（Pattern 13 第二類）

**位置**：本 repo `scripts/` 完全缺對應 script

**對照**：[Options_Trading/scripts/audit_doc_drift.py](../../Options_Trading/scripts/audit_doc_drift.py)（376 行，5 類 drift 自動偵測）

**Risk**：本 repo 過去 8 個月累積大量「文件 vs 現實」漂移：
- the dev guide line 152 寫「**402 個測試**（Docker ~4m03s 全綠，2026-04-23 Phase A3.1 收尾後）」、line 162 寫「= 342」、line 230「**Tests 422 passed**」— **單檔三個數字**
- `策略研究.md` / `優化紀錄.md` / `HANDOFF.md` 不知有多少 stale baseline / stale audit ref
- 2026-04-15 三個 silent bug 之所以累積 8 個月不被抓，根因之一就是「文件寫了 PASS 但 reality 沒驗證」（Pattern 18 claim vs reality）

**對照 Options 經驗**：R11.19 觸發 Pattern 13 第二類 architectural fix — 「manual grep 紀律連 3 輪失守 → 必跳級 architectural 自動化」。本 repo 還在 manual grep。

**修法 ROI**：高（直接複製 + 改 5 處 path constant 即可）。**這是 D.0 結論的最強建議**——在 D.2 新方向開跑前先把 audit gate 接好，避免「研究跑得快但文件髒得也快」。

**工程量估**：~30 min（複製 + path 改 + 跑一次清理初始 drift）。

---

## Section B — PIT Discipline Grep 結果

### B.1 `_DataSlicer` cover 範圍 — ✅ 大致 PASS

```
src/backtest/engine.py:92         class _DataSlicer
src/backtest/engine.py:179        fetch_ohlcv          # PIT cover
src/backtest/engine.py:196        fetch_institutional  # PIT cover (R6 fix coverage warn)
src/backtest/engine.py:232        fetch_month_revenue  # PIT cover
src/backtest/engine.py:246        fetch_market_value   # PIT cover
src/backtest/engine.py:260        fetch_stock_info     # static, no slice
src/backtest/engine.py:263        fetch_delisting      # static, no slice
src/backtest/engine.py:269        __getattr__          # 透傳其他 method （潛在 PIT bypass）
```

⚠️ `__getattr__` 透傳是已知 escape hatch — `fetch_quarterly_eps` / `fetch_margin_short` / `fetch_three_institutional` / `fetch_dividends` 全走透傳。

### B.2 透傳路徑的實際 callers — 🟡 部分透傳但有 callsite-level 保護

`grep` 結果（`portfolio/tw_stock.py:1043-1066`）顯示批次 factor 的 fetch：

| Factor | Fetch via | as_of 防護 |
|---|---|---|
| `high_proximity` | `fetch_ohlcv`（透過 slicer，500 limit）| ✅ slicer 截 + factor module 內 `compute_high_proximity_universe(..., as_of=as_of_ts)` 二次截 |
| `pead_eps` | `fetch_quarterly_eps`（透傳）| 🟡 caller 把 `as_of=as_of_ts` 傳給 `compute_pead_eps_universe` — factor module 內截，但 fetch 出來的 raw 已含 future row → 依賴 factor module 內截「不會漏」 |
| `margin_short_ratio` | `fetch_margin_short`（透傳）| 同上 |
| `revenue_momentum_v2` | `fetch_month_revenue`（slicer cover）| ✅ slicer 截 |
| `foreign_broker_v2` | `fetch_three_institutional`（透傳）| 同 pead_eps 模式 |

**結論**：透傳路徑全部依賴 **factor module 內部 `_truncate_by_date(as_of=)` 二次截**。這是合法的 PIT 設計（compute_xxx_universe 函式都有 `as_of` 參數）— **但缺一個 architectural assertion**：「factor module 必有 `as_of` 必傳參數」。

### B.3 Look-ahead 真實風險評估 — ✅ PASS

跑 mental mutation：「如果 caller 漏傳 `as_of` 給 factor module 會怎樣？」
- `compute_high_proximity_universe(... as_of=as_of_ts)` — `as_of` 是 keyword-only 必填（line 1039 confirmed）
- `compute_pead_eps_universe(... as_of=as_of_ts)` — 同上
- 其他三個批次 factor 都是 keyword-only `as_of`

→ 「漏傳」會 raise TypeError 不會 silent leak。**PIT discipline 整體 PASS。**

### B.4 Benchmark fetch 直接從 `self._source` 不過 slicer — ✅ Acceptable

[engine.py:342](../src/backtest/engine.py#L342) `_bench_for_dates = self._source.fetch_ohlcv(...)` 拿 trading days
[engine.py:381](../src/backtest/engine.py#L381) `bench_df = self._source.fetch_ohlcv(...)` 全期 benchmark

兩者皆用於「整段回測 range」的計算（trading day index、benchmark daily return），by design 不應 PIT-truncate（如果截了，每個 rebal 期間 benchmark 都要重抓 ≈ 慢 30x）。**Dividend cutoff 已用 end_date 防護**（line 374-375）。Acceptable 但建議在 docstring 加 ADR 標明此例外。

---

## Section C — Silent Fallback 計算路徑點

掃描 `np.where / fillna / try-except-pass / replace` 在 `src/` 找到 ~40 個命中，**多數在 IO 層或 string 解析（合法）**。計算路徑上需要關注的：

| File:Line | Pattern | 評估 |
|---|---|---|
| [tw_stock.py:1536](../src/portfolio/tw_stock.py#L1536) | `output = {sym: 0.5 for ...}` | ⚠️ A.1 已點名 silent imputation |
| [tw_stock.py:1561](../src/portfolio/tw_stock.py#L1561) | 同上（sector_neutral path）| ⚠️ A.1 同類 |
| [foreign_broker_v2.py:108](../src/features/foreign_broker_v2.py#L108) | `pd.to_numeric(..., errors="coerce").fillna(0)` | 🟡 信號計算路徑，0 等於「無資金流」— 可接受但不嚴謹（缺資料 ≠ 零流量）|
| [foreign_broker_v2.py:309](../src/features/foreign_broker_v2.py#L309) | `_zscore_with_tolerance(...).fillna(0.0)` | 🟡 composite 加總路徑 fillna(0) — 缺資料子訊號的權重沉默被吃掉 |
| [institutional.py:36-37](../src/features/institutional.py#L36-L37) | `to_numeric(...).fillna(0)` | 🟢 legacy weight=0 已停用 |
| [engine.py:657](../src/backtest/engine.py#L657) | `portfolio_daily.reindex(...).fillna(0.0)` | 🟢 空倉日填 0% return — 合法（drift-aware P4.6 已解釋）|
| [engine.py:772](../src/backtest/engine.py#L772) | `ret_df.loc[date, common].fillna(0.0)` | 🟢 day_rets 填 0 — 等價於「該股當日無資料 → 不貢獻 return」，PIT-safe |
| [universe.py:191](../src/backtest/universe.py#L191) | `working["stock_id"].map(...).fillna(0.0)` | 🟡 但有 `coverage < min_coverage → raise` 前置防護（line 184-189）|
| [universe.py:307](../src/backtest/universe.py#L307) | 同上 size_proxy fillna(0.0) | 🟡 有三層防護（line 282-306）但 `cached_total < 10 且 ≥ 1 成功` 視為「小樣本 best effort」依然接受 |

**結論**：**1 個 critical**（A.1 `_metric_ranks` 0.5 imputation）+ **2 個 minor**（foreign_broker_v2 fillna(0) 信號和 composite 路徑 — Phase A1 結論為「不顯著」，影響有限但若未來 weight 上去要重審）。

---

## Section D — Test Fixture 真實邊界覆蓋率

| 維度 | 評估 |
|---|---|
| Fixture 數量 | 4 個 in conftest（全 synthetic）+ 各 test 內 ad-hoc DataFrame |
| Real cache 抽樣 | **0%** — 沒有 fixture 從 `data/cache/` 抽真實片段 |
| Boundary case fixture | 部分（`test_tz_safety.py` 10 tests / `test_dividends_strict.py` 4 tests / `test_p7_universe.py` 12 tests 有覆蓋）|
| Schema drift fixture | 沒有 |
| 早期 IPO fixture | 沒有（依賴 `_analyze_symbol` 274-bar guard 隱含過濾）|
| Pandas 2.x tz fixture | `test_tz_safety.py` ✅ 有覆蓋 |
| Stock split + dividend 共現 | `test_metrics.py:33` known-answer test ✅|
| external audit env (Windows AV) | ❌ 未防護 — Options 已升 `tests/_tmp/` 解 |

**Pattern 9 評估**：🟡 中等通過。比 Phase A0 強（已抓 timezone / split / dividend 邊界），但**真資料邊界**完全不在 fixture 裡——這是 2026-04-15 三個 silent bug 8 個月不被發現的根因之一。

---

## Section E — Cross-Repo Engineering Discipline 對照

### E.1 Skill / Agent 對照

| 工具 | Quantitative-Trading | Options_Trading | 落差 |
|---|---|---|---|
| Self-Audit SOP | 6 步 + Checklist template | **19-pattern + Skill Chain + Evidence FAIL** | ⚠️ 落後 13 patterns |
| Forensic Sweep | 無（合併在 SOP Step 4）| **獨立 skill + 11 keyword pattern table** | ⚠️ 完全缺 |
| Multi-Perspective | 7+1 personas（`/dual-audit` skill 部分覆蓋）| 8 角度 skill 化 | 🟡 接近但 dual-audit 只 spawn 2 agent |
| Architectural gate | 無 | **`audit_doc_drift.py` 5 類自動偵測** | ⚠️ 完全缺 |
| Pre-design Attack Gate (Pattern 0) | 無 | 強制：**寫 plan 前列 ≥5 attackers + 跑 1-2 個** | ⚠️ 完全缺 |
| Hollow PASS Detector (Pattern 17) | 無 | **9 個 sub-rule（含 institutional gate threshold = 0）** | ⚠️ 完全缺 |
| Doc Drift Sweep (Pattern 18) | 無 | **Automated gate + grep 禁 path filter** | ⚠️ 完全缺 |
| `PostToolUse` SOP hook | ✅ `(internal SOP hook)`（提示 SOP）| ❌ R11.x 已刪（實證 hook reminder 對 silent bug 無實質防線）| 🟢 本 repo 留 hook 但 hook 功效已被 Options 反證 |

### E.2 commands 對照

| Quant `(internal commands)/` | Options `(internal skills)/` | 對應 |
|---|---|---|
| `factor-ic.md` | — | Quant 特有（Phase A1 累積） |
| `ic-aggregate.md` | — | Quant 特有 |
| `smart-beta-paper.md` | — | Quant 特有（被動追蹤）|
| `dual-audit.md` | `multi-perspective` skill | 🟡 dual-audit 只 spawn 2 agent vs multi-perspective 全 8 角度 |
| `code-review-quant.md` | — | Quant 特有但偏舊 |
| `run-backtest / walk-forward / monthly-rebalance` | — | LEGACY (HANDOFF 已標) |
| — | `self-audit` (19-pattern) | ⚠️ 缺 |
| — | `forensic-sweep` | ⚠️ 缺 |

### E.3 Pro 紀律差距總結

| 紀律維度 | Quant | Options | 落差程度 |
|---|---|---|---|
| Pre-design Attack（動手前攻擊）| 0% | 100% | 高 |
| audit chain 累積 | Round 1-18（已停）| R11.x → R12.13 連 13 輪持續 | 中（Quant 已停 ≠ 沒做） |
| Architectural automated gate | 0 件 | 1 件（doc_drift）+ 候選 hook/CI | 高 |
| Evidence Missing = FAIL | SOP template 寫了但靠手動 | Skill 強制輸出格式 + Skill Chain | 中 |
| 跨 module mutation 紀律 | 部分（`_BacktestCacheMissError` raise + silent renormalize raise）| Pattern 14 全鏈 enforced | 中 |
| Hollow PASS 防線 | 0 件 | 9 個 sub-rule | 高 |
| Cross-frame state lifetime（R12.2 教訓）| 未提 | Pattern 14 sub-rule (c) | 高 |

---

## Section F — silent bug pattern 對照（2026-04-15 三件 vs 同型搜獵）

### F.1 Bug 1 — pandas 2.x tz_convert error（已修 b78a70c / 85df06a）

**Sibling 搜獵**：grep `pd\.Timestamp\(.*tz=|tz_convert|tz_localize` in src/

`tz_localize(None)` 在 [universe.py:100](../src/backtest/universe.py#L100) / [universe.py:99](../src/backtest/universe.py#L99) — **已防護**（Naive 比較先 strip）
`tz_convert` 沒命中 src/ — **已清零** ✅

### F.2 Bug 2 — sibling tz_convert 第二處漏修（Pattern 14 教訓）

`grep` 後沒有殘留同型 sibling pattern。✅ 但 Pattern 14 cross-interference 紀律本 repo 沒寫進 SOP（Options 在 Pattern 14 sub-rule (c) 已升級）。

### F.3 Bug 3 — Pre-filter universe degradation（已修 0debbf0）

**Sibling 搜獵**：找「沒 sanity bound assertion 就 silent fallback」的同型

✅ universe.py 線 184-189 已有 `coverage < min_coverage → raise`
✅ universe.py 線 207-211 已有 `if not _twse_turnover → raise`
✅ engine.py 線 438-445 已有 `analyze_success / n_analyzed < min_eligible_ratio → raise`
✅ universe.py 線 282-306 size-proxy 三層 guard

→ 對 Pattern 6 + Pattern 17（hollow PASS）的後續防線**結構性已補齊**。✅

### F.4 但 1 個 sibling pattern 未補

A.1 `_metric_ranks` 補 0.5 median rank 屬於同型「silent fallback 假通過」**未被 2026-04-15 sweep 抓到**——當時 sweep 集中在 universe 層，沒下到 ranking 層。**這是本 audit 抓到的 1 個 silent bug 候選**（不是新生 bug，是同型 sibling 8 個月來都沒被掃）。

---

## Section G — GO / NO-GO Decision

### G.1 對 D.1 立即必做（cache refresh / 422 tests / Smart Beta）— ✅ GO

D.1 是 maintenance work，不依賴架構 audit 結論。**直接跑沒問題**。

### G.2 對 D.2 中期方向（long/short / multi-factor / quality+lowvol）— 🟡 GO-WITH-CAVEATS

**先補 3 件再開跑 D.2**（按 ROI 排序）：

| # | 動作 | 工程量 | 為什麼必補 |
|---|---|---|---|
| 1 | 遷移 Options 4 件（audit_doc_drift.py / self-audit / forensic-sweep / multi-perspective）| ~30 min | A.6 — D.2 工程量大，需要更強 audit gate；不補等於用 Phase A0 紀律打 Phase B 戰 |
| 2 | 修 A.1 `_metric_ranks` 0.5 imputation | ~2 hr | F.4 — 唯一 audit 抓到的 silent bug 候選；D.2 任何方向都會踩 |
| 3 | 修 A.2 `fetch_institutional` / `fetch_financial_quality` PIT cover + test | ~3 hr | A.2 — 若 D.2 選 multi-factor / quality+lowvol，會直接重啟這兩個 fetch path |

**不必補（建議 D.2 拍板後再做）**：
- A.3 tw_stock.py refactor（D.2 改 selection 邏輯時順手做）
- A.4 real-data fixtures（過程中發現需要再補）
- A.5 lookback assertion（latent，現實風險低）

### G.3 對 D.3 不要做（已驗證 ROI 為負）— ✅ 維持

A1-A3 結論不重做；本 audit 不挑戰「產業集中是 factor 特性」的 root cause。

### G.4 整體 Verdict

**🟡 GO-WITH-CAVEATS**：

1. **底層 PIT discipline 跟 silent-bug 防線 = 已達 Pro Retail 標準**（修前那 3 個 bug 之後沒倒退；F.3 sibling sweep clean）
2. **工程紀律 = 落後 Options ≈ 9 個月**（19-pattern / forensic-sweep / audit_doc_drift 全缺）
3. **抓到 1 個 silent bug 候選（A.1）+ 1 個 architectural risk（A.2）**——都是 Phase A3.1 sector_neutral fail 的「次因」候選，但不挑戰 Plan 假設錯的根因
4. **D.1 立即必做可直接跑；D.2 開跑前必補 3 件（30 min + 2 hr + 3 hr ≈ 6 hr）**

---

## Section H — 誠實補充

**Time estimate**：本 audit ≈ 2 hr 完成（含 Options repo 對照 + grep + 核心檔 deep read）；D.2 開跑前的補強工作 ≈ 6 hr ± 30%（最大卡點：A.2 PIT test 寫法）

**Failure modes**：
- 本 audit 沒**真跑** Mutation test / pytest，純 read-only grep + 邏輯推論。A.1 silent imputation 是「結構性風險」，**還沒實證它對 Phase A3.1 fail 有量化貢獻**（要跑 mutation 補 None vs 0.5 比較 OOS Sharpe 差距才知道）
- 「sibling sweep clean」基於 grep 結果，**未跑 external audit 對抗**——獨立 audit 過去常從 SOP 沒涵蓋的角度抓
- 對 Options_Trading 19-pattern 細節評估僅讀 SKILL.md 沒實際跑過——對 Pattern 0/13/17/18 sub-rule 的權衡可能略，建議遷移後 1 週內驗 1 次完整 chain

**Assumption boundary**：
- 假設 user 接受「Phase A1-A3 結論不重做」+「Plan 假設錯（產業集中是 factor 特性）」是 root cause
- 假設 Options 工程資產可乾淨遷移（path constant 改 + import 對齊即可，沒漏接 Quant repo 沒有的依賴）
- 假設 D.2 中期方向會在 1-2 週內拍板；audit 結論的「補強 ROI」估算依此前提
- 不假設「補完這 6 hr 就能贏 0050」——這 6 hr 只是把 Phase A3 結束時的 Pro 紀律標準補齊

---

**End of Audit. Awaiting user 拍板：(1) GO with 6 hr 補強  (2) GO straight to D.2 不補強  (3) 先聊 D.2 方向選擇**
