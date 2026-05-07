#!/bin/bash
# daily_update.sh — 每日收盤後 cache 更新一鍵執行
#
# 建議執行時間：台股收盤後 14:30 以後
# 預計時間：~3 分鐘（TWSE+TPEX via --daily 2 min + validate 1 min）
# 2026-04-16 起 TPEX OpenAPI 提供 OHL，--daily 單一指令即涵蓋 TWSE + TPEX
# 若 --daily 發現 TPEX 更新 < 500 檔，自動 fallback 到 --daily-tpex（FinMind，~7 min）
#
# Usage:
#   bash scripts/daily_update.sh
#
# 執行前提：Docker Desktop 開啟

set -e
cd "$(dirname "$0")/.."

LOG_DATE=$(date +%m%d)
START_TIME=$(date +%H:%M:%S)

echo "=========================================="
echo "  Daily Cache Update - $(date)"
echo "=========================================="

# --- Step 0: 檢查環境 ---
if ! docker info >/dev/null 2>&1; then
    echo "❌ Docker 未開啟，請先啟動 Docker Desktop"
    exit 1
fi

# 停掉可能自動拉起的 live 容器
if docker ps -a --format '{{.Names}}' | grep -q '^tw-portfolio-bot$'; then
    echo "⚠️ 發現 tw-portfolio-bot 容器，先停掉避免干擾"
    docker stop tw-portfolio-bot 2>/dev/null || true
    docker rm tw-portfolio-bot 2>/dev/null || true
fi

# --- Step 1: Daily OHLCV update (TWSE + TPEX 一次搞定, STOCK_DAY_ALL + TPEX OpenAPI) ---
echo ""
echo "--- [1/2] Daily OHLCV update (TWSE + TPEX) ---"
STEP1_START=$(date +%s)
MSYS_NO_PATHCONV=1 docker compose run --rm \
    -e DATA_CACHE_DIR=/app/data/cache \
    --entrypoint python portfolio-bot \
    scripts/cache_fill.py --daily \
    > logs/daily_twse_$LOG_DATE.log 2>&1
STEP1_DUR=$(($(date +%s) - STEP1_START))
echo "✅ Daily OHLCV 完成 ($STEP1_DUR 秒) → logs/daily_twse_$LOG_DATE.log"
tail -5 logs/daily_twse_$LOG_DATE.log

# --- Step 1.5: 檢查 TPEX 更新狀況；若不足則 fallback 到 FinMind ---
TPEX_UPDATED=$(grep -oE "TPEX daily_all: [0-9]+" logs/daily_twse_$LOG_DATE.log | grep -oE "[0-9]+" | head -1 || echo "0")
echo ""
echo "  → TPEX stocks from OpenAPI: $TPEX_UPDATED"

STEP2_DUR=0
if [ "${TPEX_UPDATED:-0}" -lt 500 ]; then
    echo "⚠️ TPEX OpenAPI 只取得 $TPEX_UPDATED < 500 檔 → fallback 到 FinMind (--daily-tpex)"
    echo ""
    echo "--- [Fallback] TPEX daily via FinMind ---"
    STEP2_START=$(date +%s)
    MSYS_NO_PATHCONV=1 docker compose run --rm \
        -e DATA_CACHE_DIR=/app/data/cache \
        --entrypoint python portfolio-bot \
        scripts/cache_fill.py --daily-tpex \
        > logs/daily_tpex_$LOG_DATE.log 2>&1
    STEP2_DUR=$(($(date +%s) - STEP2_START))
    echo "✅ TPEX fallback 完成 ($STEP2_DUR 秒) → logs/daily_tpex_$LOG_DATE.log"
    tail -3 logs/daily_tpex_$LOG_DATE.log
else
    echo "✅ TPEX OpenAPI 充分（$TPEX_UPDATED 檔），略過 FinMind fallback"
fi

# --- Step 2: Margin + Institutional (TWSE/TPEX 公開匿名端點, 0 FinMind token) ---
# Phase A1 R11 新增。每日 4 calls (TWSE margin + TPEX margin + TWSE T86 + TPEX insti)
# insert-if-missing 到 margin_short/ + institutional_v2/ pickle。
# FinMind 既有 row 永不覆蓋。
echo ""
echo "--- [2/3] Margin + Institutional (TWSE/TPEX) ---"
STEP2_5_START=$(date +%s)
TODAY_DATE=$(date +%Y-%m-%d)
MSYS_NO_PATHCONV=1 docker compose run --rm \
    -e DATA_CACHE_DIR=/app/data/cache \
    --entrypoint python portfolio-bot \
    scripts/backfill_tw_factors.py --dataset both --date "$TODAY_DATE" \
    > logs/daily_margin_insti_$LOG_DATE.log 2>&1
STEP2_5_DUR=$(($(date +%s) - STEP2_5_START))
echo "✅ Margin + Insti 完成 ($STEP2_5_DUR 秒) → logs/daily_margin_insti_$LOG_DATE.log"
tail -5 logs/daily_margin_insti_$LOG_DATE.log

# --- Step 3: validate ---
echo ""
echo "--- [3/3] Validate cache ---"
STEP3_START=$(date +%s)
MSYS_NO_PATHCONV=1 docker compose run --rm \
    -e DATA_CACHE_DIR=/app/data/cache \
    --entrypoint python portfolio-bot \
    scripts/validate_cache.py \
    > logs/validate_$LOG_DATE.log 2>&1
STEP3_DUR=$(($(date +%s) - STEP3_START))
echo "✅ Validate 完成 ($STEP3_DUR 秒) → logs/validate_$LOG_DATE.log"
tail -5 logs/validate_$LOG_DATE.log

# --- Step 4: 確認關鍵股更新到今日 ---
echo ""
echo "--- Final check: 關鍵股最新日期 ---"
MSYS_NO_PATHCONV=1 docker compose run --rm \
    -e DATA_CACHE_DIR=/app/data/cache \
    --entrypoint python portfolio-bot -c "
import pandas as pd
import os
from datetime import datetime
today = datetime.now().strftime('%Y-%m-%d')
keys = ['0050', '0056', '2330', '2454', '2408']
print(f'今日 = {today}')
for s in keys:
    p = f'/app/data/cache/ohlcv/{s}.pkl'
    if os.path.exists(p):
        df = pd.read_pickle(p)
        d = str(df.index.max())[:10]
        status = '✅' if d == today else '⚠️'
        print(f'  {status} {s}: {d}  close={df[\"close\"].iloc[-1]:.2f}')
" 2>&1 | tail -10

# --- Summary ---
END_TIME=$(date +%H:%M:%S)
TOTAL_DUR=$((STEP1_DUR + STEP2_DUR + STEP2_5_DUR + STEP3_DUR))
echo ""
echo "=========================================="
echo "  Daily Update 完成"
echo "  開始: $START_TIME / 結束: $END_TIME"
echo "  總耗時: ${TOTAL_DUR} 秒 (~$((TOTAL_DUR / 60)) 分鐘)"
echo "=========================================="
