#!/bin/bash
# make_sync_info.sh — 產生 .sync_info 檔記錄 cache snapshot
#
# 用途：壓縮專案前跑一次，記錄「這份 cache 的身分證」
# 解決：跨電腦同步時無法追溯「metrics 基於哪份 cache」的問題
#
# Usage:
#   bash scripts/make_sync_info.sh
#   # 然後才壓縮：
#   7z a ../QuantTrading-$(date +%Y%m%d).7z .
#
# 跨平台：Git Bash (Windows) / Linux / macOS
# 依賴：docker compose（讀 pkl 檔內容；若無 Docker 則降級為 filesystem info only）

set -e
cd "$(dirname "$0")/.."  # 切到專案根目錄

OUTPUT=".sync_info"
HOSTNAME=$(hostname 2>/dev/null || echo "unknown")
SYNC_TIME=$(date -Iseconds 2>/dev/null || date)
CACHE_DIR="data/cache"

echo "=== 產生 $OUTPUT ==="

# 檢查 cache 是否存在
if [ ! -d "$CACHE_DIR" ]; then
    echo "❌ Cache directory not found: $CACHE_DIR"
    echo "請在專案根目錄執行此腳本"
    exit 1
fi

# --- Filesystem info（不需 Docker）---
OHLCV_COUNT=$(ls "$CACHE_DIR/ohlcv/"*.pkl 2>/dev/null | wc -l)
REVENUE_COUNT=$(ls "$CACHE_DIR/revenue/"*.pkl 2>/dev/null | wc -l)

# 抓關鍵檔案的 mtime
get_mtime() {
    if [ -f "$1" ]; then
        stat -c "%y" "$1" 2>/dev/null || stat -f "%Sm" "$1" 2>/dev/null || echo "unknown"
    else
        echo "FILE_NOT_FOUND"
    fi
}

OHLCV_0050_MTIME=$(get_mtime "$CACHE_DIR/ohlcv/0050.pkl")
STOCK_INFO_MTIME=$(get_mtime "$CACHE_DIR/stock_info/_global.pkl")
DIVIDENDS_MTIME=$(get_mtime "$CACHE_DIR/dividends/_global.pkl")

# Git 資訊（如果在 git repo 中）
GIT_BRANCH=""
GIT_COMMIT=""
if [ -d ".git" ]; then
    GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "detached")
    GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
fi

# --- PKL 資料日期（需 Docker）---
PKL_INFO=""
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "讀取 pkl 資料日期（透過 Docker）..."
    PKL_INFO=$(MSYS_NO_PATHCONV=1 docker compose run --rm -e DATA_CACHE_DIR=/app/data/cache \
        --entrypoint python portfolio-bot -c "
import pandas as pd, os
samples = ['0050', '0056', '2330', '2454', '2408']
print()
for sym in samples:
    path = f'/app/data/cache/ohlcv/{sym}.pkl'
    if os.path.exists(path):
        df = pd.read_pickle(path)
        if len(df) > 0:
            max_date = str(df.index.max())[:10]
            close = df['close'].iloc[-1]
            print(f'  {sym}_latest_date: {max_date}')
            print(f'  {sym}_latest_close: {close:.2f}')

# 統計 cache 整體最新日期分布
from collections import Counter
import glob
files = glob.glob('/app/data/cache/ohlcv/*.pkl')
dates = []
for f in files:
    try:
        df = pd.read_pickle(f)
        if len(df) > 0:
            dates.append(str(df.index.max())[:10])
    except Exception:
        pass

if dates:
    c = Counter(dates)
    top = sorted(c.items(), reverse=True)[:3]
    print(f'  cache_latest_date_top: {top}')
    print(f'  cache_most_common_date: {c.most_common(1)[0][0]}')
" 2>/dev/null | tail -20)
else
    echo "⚠️ Docker 未可用，跳過 pkl 資料日期讀取"
    PKL_INFO="  (Docker unavailable - pkl data dates skipped)"
fi

# --- 組合 .sync_info ---
cat > "$OUTPUT" <<EOF
# Cache Snapshot Identifier (壓縮前自動產生)
# 解壓後讀此檔可確認：這份 cache 是何時、從哪台電腦打包的

sync_source: $HOSTNAME
sync_time: $SYNC_TIME
sync_cwd: $(pwd)

# Git 狀態（如適用）
git_branch: ${GIT_BRANCH:-not_a_git_repo}
git_commit: ${GIT_COMMIT:-N/A}

# Cache Filesystem Info
cache_dir: $CACHE_DIR
ohlcv_files: $OHLCV_COUNT
revenue_files: $REVENUE_COUNT
ohlcv_0050_mtime: $OHLCV_0050_MTIME
stock_info_mtime: $STOCK_INFO_MTIME
dividends_mtime: $DIVIDENDS_MTIME

# Cache Data Dates (from Docker pkl read)
$PKL_INFO

# Notes
# - 解壓後在公司電腦 cat .sync_info 驗證版本
# - 若 metrics 數字異常，比對這裡的 date 看是否 cache 不同步
EOF

echo ""
echo "✅ $OUTPUT 已產生："
echo "=================================="
cat "$OUTPUT"
echo "=================================="
echo ""
echo "下一步："
echo "  1. 確認內容無誤"
echo "  2. 壓縮專案：7z a ../QuantTrading-\$(date +%Y%m%d).7z ."
echo "  3. 上傳 Google Drive"
