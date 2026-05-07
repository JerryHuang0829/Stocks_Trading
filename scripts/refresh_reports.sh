#!/bin/bash
# 一鍵重跑 Walk-Forward summary 和 Dashboard 6M 回測資料
# 需在專案根目錄執行，且 Docker Compose 環境可用
#
# Usage:
#   chmod +x scripts/refresh_reports.sh
#   ./scripts/refresh_reports.sh
#
# 或在 Docker 內部直接執行對應指令。

set -e

echo "=============================================="
echo "  Refresh Reports — Walk-Forward + Dashboard"
echo "=============================================="

# --- E2: Walk-Forward summary 重跑 ---
echo ""
echo "--- [E2] Re-running Walk-Forward validation ---"
docker compose run --rm --entrypoint python portfolio-bot \
    scripts/walk_forward.py \
    --train-months 18 --test-months 6 \
    --start 2019-01-01 --end 2025-12-31 \
    --output-dir reports/walk_forward

echo ""
echo "Walk-Forward summary updated → reports/walk_forward/summary.json"

# --- E5: Dashboard 6M 重跑 ---
echo ""
echo "--- [E5] Re-running Dashboard 6M backtest ---"
docker compose run --rm --entrypoint python portfolio-bot \
    scripts/run_backtest.py \
    --start 2024-06-01 --end 2024-12-31 \
    --output-dir reports/backtests/dashboard_6m

echo ""
echo "Dashboard 6M updated → reports/backtests/dashboard_6m/"

echo ""
echo "=============================================="
echo "  All reports refreshed successfully."
echo "=============================================="
