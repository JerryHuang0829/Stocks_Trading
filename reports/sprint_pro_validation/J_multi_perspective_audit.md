# Multi-Perspective + Codex Pre-Audit — Sprint Phase J

**Target**: `CANONICAL_MANIFEST_2026-05-04.md` 的 Pro 級可信度
**Method**: 7 quant personas + Codex adversary，每 persona 提 ≥3 攻擊問題並自答（給 file:line 證據或誠實 acknowledge gap）

---

## P1 (量化主管) — 投組 risk + governance

**Q1.1**: 跑了 5 IC + 1 backtest config (D1_v2)，**v5 主結論「D1_v2 OOS IR 0.0058 觸發 D6 disqualify」是 cherry-picked 嗎？**v5 還有 D-A 至 D-G 7 個 candidate factor sets 可選，這 sprint 只 reproduce 1 個。
- **Answer**: ⚠️ **acknowledged gap**。Sprint 限定 reproduce 既有 D1_v2 IS+OOS（plan 明寫「不重新設計 / 不掃 candidates」）。其他 6 個 candidate sets 是 Plan v5 Session 2-4 的工作。**Manifest 不宣告 D1_v2 disqualifier 適用其他 candidate**——只宣告 v5 D6 規則的數值門檻可被 IR 0.0058 觸發。

**Q1.2**: 460 ~ 462 個 test 變動 +3 沒查根因——這 3 個新 collected test 會不會其實是 covering parameter regression?
- **Answer**: ⚠️ partial。Manifest §2 說「pytest collection variance」但沒實際 diff test names。建議 Sprint J+1 補做 `pytest --collect-only` 對比兩次 commit。本次未做。

**Q1.3**: 寫死「monthly only」是 v5 intentional，但 Plan v5 為何不直接做 weekly sensitivity？這是 governance 怠惰嗎？
- **Answer**: Codex-Prompt.md:185 v5 pre-commit rule #6「1 frequency monthly 不可改 (v5 縮版)」明寫策略選擇。v6 預留 (Codex-Prompt.md:122 B3)。governance 上是縮版 trade-off，非怠惰，**Manifest 已 cite v5 spec L185**。

---

## P2 (策略研究員) — FDR / DSR / Bootstrap / PIT / Permutation

**Q2.1**: DSR 全 5 因子 = 0.0 — Pro paper (López de Prado 2014) 說 DSR < 0.95 不算 significant，但 manifest 沒挑戰「factor 全 fail DSR」這個事實對 v5 multi-factor composite 的意涵。
- **Answer**: ⚠️ **acknowledged gap**。DSR=0 是 well-known phase A1 finding (CLAUDE.md:469 record)，n_trials=5 conservative haircut。Manifest 是 reproducer 文件，不是 strategy decision doc。**v5 Plan 自己已承認 DSR 全 fail 是 single-factor signal weakness，靠 multi-factor composite + bootstrap CI on monthly active returns (L6) 來達 significance**。Manifest 不重複 critique 這已知約束。

**Q2.2**: Bootstrap CI block_len=3, n_iter=10000, seed=42 fixed — 沒做 seed sensitivity (e.g. seed ∈ {42, 123, 7777} 跑 3 次比 CI overlap)。
- **Answer**: ⚠️ **未做**，acknowledged in §10。Sprint 規模限制（plan 內已標 "no seed sweep"）。降級為 P2 follow-up。

**Q2.3**: FDR adjusted p 沒 reproduce check — 5 IC JSON 只 verify mean_ic / bootstrap CI，FDR adjustment 跨多因子的 BH (Benjamini-Hochberg) 是否還對齊原值?
- **Answer**: ⚠️ **gap**。Manifest §3 表格沒列 FDR p。Quick Python check 可加。本次未做。

**Q2.4 (Codex 借)**: 你說 effective_n drift +3-4 是「industry label cache 增量更新」，但**沒實際 grep cache file mtime / row count diff** 證明。
- **Answer**: ⚠️ **未實證**。當下推論基於 log line `Loaded 3074 industry labels for effective_n clustering`（高機率正確），但沒 diff 舊版 cache 的 industry label count。若 cache 沒變但 effective_n 動，根因錯。**降級為 P2，建議跑 cache mtime 對比**。

---

## P3 (Vol Trader) — N/A 此 sprint，Q-T 不涉 options

不適用，pass。

---

## P4 (CRO) — Risk gate, MDD, tail event

**Q4.1**: D1_v2 max_drawdown -0.20 → -0.20，沒做 2008 / 2020 / 2022 tail event stress test。
- **Answer**: 範圍外。Sprint 是 reproducer，非 stress test 設計。Plan v5 L4 規則（max DD diff vs 0050 ≤ +0.05）提供 gate。

**Q4.2**: cost rate 從 57 bps 修對到 67 bps，但**沒 stress test slippage_bps 在 retail tail event 時可能 50-100 bps（thin liquidity）**。
- **Answer**: ⚠️ valid critique，但**範圍外**。multi-perspective skill SKILL.md:24 早 flag「turnover_cost / slippage 從未 sweep sensitivity」是已知 P3 gap。**Sprint 收尾後可加 slippage_bps ∈ {10, 30, 50, 100} sensitivity sweep（v5 Session 2 候選）**。

**Q4.3**: walk_forward implicit slippage 改變對歷史 reports/walk_forward_*/ 的解讀——CRO 風控視角這要不要追溯所有歷史報告？
- **Answer**: 是。Manifest §10 「Codex 上輪 audit 我的修正」段已 flag「過往 walk_forward 報告應視為 5bps assumption」。**未來引用任何 walk_forward 報告須加 cost-rate caveat**。降級為文件規範，非數值錯。

---

## P5 (Buy-side 面試官) — Manifest 經得起外審？

**Q5.1**: 你給我 462 passed，但**沒給 fail rate over time**——是 first run pass 還是 retry 才 pass？
- **Answer**: First run。Manifest §2 三次 pytest run 全 first-time pass（SOP Step 6 全綠，無 retry）。

**Q5.2**: 5 因子 IC reproducer 都 PASS，但**為何不 reproduce 5 因子的 phase_a1_summary aggregate report 也對齊**？aggregate 才是 user 真正使用的決策報告。
- **Answer**: ⚠️ **acknowledged gap**。Sprint 範圍限「個別 IC JSON 對齊」，未跑 `/ic-aggregate` 重產 summary report。aggregate 內含 FDR adjusted p (用 m=5 5 因子 BH adjustment) — 若上游 5 個 IC 全對齊，aggregate 應 deterministic 對齊（除非有別 inputs）。建議降級為 P2 follow-up。

**Q5.3**: 「test count 459→462 是 collection variance」這個解釋外審不會買單——你**為何不 diff `--collect-only`** 給確證？
- **Answer**: 你對。Manifest §2 自己標 "non-fail signal" 是 honest 但不 rigorous。**Sprint J+1 補 diff** 是必要的。降級為 P1 follow-up before publishing manifest。

---

## P6 (資料工程師) — PIT discipline + cache integrity

**Q6.1**: PIT mutation 4 tests 只覆蓋 OHLCV / institutional / month_revenue 3 panel，**沒覆蓋 quarterly_eps / margin_short / market_value 3 panel**——用「共用 _truncate_by_date_col」推論不夠，因為 fetch_ohlcv 走 separate path（engine.py:191 直接 `df[df.index <= self._as_of]` 不經過 _truncate）。
- **Answer**: 對，**架構 asymmetric**：fetch_ohlcv 用 index 比較，其他 panel 用 _truncate_by_date_col 比較。Manifest §8 已標「未測 quarterly_eps / margin_short / market_value，可推論安全」。實際 cover 所有 6 panel 是 follow-up（每 panel 加 1 test）。

**Q6.2**: PIT mutation 你只測「forward-leak rejection」，**沒測「lag day enforcement」**——例如 revenue 應 lag 45 days (REVENUE_LAG_DAYS)，你測的是 as_of cutoff 不是 lag。
- **Answer**: 對。`_DataSlicer` 只 enforce as_of cutoff，不 enforce lag——lag 是 feature 計算層 (src/features/*) 的責任。Sprint 範圍是 _DataSlicer，**lag 測試屬另一層 audit**。Manifest §8 沒 claim 測 lag，誠實表述。

**Q6.3**: industry label cache 「自然增量」的說法**沒給 mtime 證據**。如果 cache 被 manual touch / corruption，effective_n 會變但你誤判為自然。
- **Answer**: 對 (P2.4 同). Quick `ls -la data/cache/industry/` 對比 modification time 即可驗。本次 sprint 未做。

---

## P7 (Retail trader) — NT$1M scale realistic?

**Q7.1**: 67 bps cost (0.47% 手續費 + 0.2% slippage) 在 NT$1M scale **真實嗎**？台股 retail 手續費分券商，永豐 0.6 折可達 7.5 bps × 2 = 15 bps + 證交稅 30 bps = 45 bps；slippage 中型股 10-15 bps × 2 = 20-30 bps；total 65-75 bps roughly OK。
- **Answer**: ✅ 對齊。conftest.py 註記 R19 audit 已調 10 bps slippage 對「中型股實際 10-15 bps」。Pro tier 假設 retail 永豐折扣 OK，cost 模型大致合理。

**Q7.2**: D1_v2 OOS 12 個 rebalance 換手 4.67 → 一年要動 4.67 × position（top_n=8）= 37 trade。NT$1M scale per trade ≈ NT$125k，不是 thin order book。retail 摩擦 OK。
- **Answer**: ✅ pass。

---

## P8 (Codex adversary) — 3 hardest attack questions

**Q8.1 (核心)**: 你的 Phase B reproducer **用同一 cache @ 2026-04-21**，新舊跑用的是同一 input data。**這不是 reproducibility verification，是「重跑驗算」**。真正 reproducibility 要 cross-machine / cross-cache regenerate；如果 cache 本身是上一輪 IC 的污染源（hidden state），sprint 抓不到。
- **Answer**: ⚠️ **valid，最強攻擊**。Manifest §10 信任邊界第 1 條已誠實標「FinMind cache @ 2026-04-21 不變」是假設。**Sprint 證的是「現在 code + 同 cache 重跑得同數值」，不是「現在 code 在 fresh cache 上重跑得同數值」**。後者要 wipe cache + 重抓 FinMind，1-2 hr 額外工程，超出 sprint 範圍。**降為 P1 follow-up，列入 Sprint v2 必跑項**。

**Q8.2**: 你 commit 的 slippage default fix **改變了 walk_forward.py 的歷史 backtest 數值含義** — 過往所有 reports/walk_forward_*/ 須 retroactively annotated。但 Sprint commit message 沒寫 "BREAKING CHANGE"。
- **Answer**: ⚠️ **acknowledged gap**。Commit message §0d31572 寫「walk_forward.py:177 implicit caller 也順便對齊（過往 walk_forward 報告應視為 5bps assumption）」但未 tag BREAKING。應在 git note 或 follow-up commit 加 tag。降級 P2 housekeeping。

**Q8.3**: 459→462 test count 你解釋 "collection variance" 但 **3 次 pytest run 用的是不同 environment state**（first 是純 baseline、second 是 get_threshold fix 後、third 是 slippage fix 後）。**slippage default change 可能 disable 一個 conditional skip + enable 一些 parametrize variants**。
- **Answer**: ⚠️ **valid challenge**。Manifest §2 honest 但解釋 hand-wavy。**Sprint J+1 必須 diff 3 次 collect-only output 找出 +3 test 來源**。Fail to do = manifest 公信力扣分。降級 **P1 must-fix before final manifest publish**。

---

## Consolidated Patch List

### P1 (Must-fix before publish)
1. **diff `pytest --collect-only` output** between 459 / 462 三次 run，找 +3 test source（Codex Q8.3 + 面試官 Q5.3）
2. **document cache reproducibility caveat** more loudly in §10——Codex Q8.1 attack 必答

### P2 (Should-do follow-up)
3. **PIT mutation tests** 補 quarterly_eps / margin_short / market_value 3 panel（資料工程師 Q6.1）
4. **effective_n drift root-cause grep** — diff industry label cache mtime + row count（資料工程師 Q6.3 + 策略研究員 Q2.4）
5. **diff FDR adjusted p** for 5 IC reproducer（策略研究員 Q2.3）
6. **walk_forward report retroactive tag** with cost-rate caveat（CRO Q4.3 + Codex Q8.2）
7. **rerun /ic-aggregate** to verify 5-factor aggregate report 對齊（面試官 Q5.2）

### P3 (Nice-to-have)
8. Bootstrap CI seed sensitivity sweep（策略研究員 Q2.2）
9. Slippage_bps ∈ {10, 30, 50, 100} sensitivity sweep（CRO Q4.2）
10. quarterly_eps 60d lag enforcement test（資料工程師 Q6.2）

---

## Sprint J Verdict

**21 attack questions answered**（7 personas × 3 + Codex × 3 = 24，Vol Trader N/A 扣 3 = 21）

**Honest summary**:
- 0 P0 unaddressed
- 2 P1 必處理 before publishing manifest as "Pro signoff"
- 5 P2 follow-up
- 3 P3 nice-to-have

**Conclusion**: Manifest 在「reproducer 對齊既有 cache」層 valid，但**「Pro 級可獨立 reproduce」尚差 1-2 hr fresh-cache rerun 才完整**。建議先解 2 P1 後再對外宣告 Pro signoff。
