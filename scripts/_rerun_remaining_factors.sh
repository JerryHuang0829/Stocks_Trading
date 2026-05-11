#!/usr/bin/env bash
# Sequentially rerun 4 remaining Phase A1 factors after foreign_investor_v2 fresh rerun.
# Plan: codex-pro-codex-precious-reef.md Phase 3b-e (user 拍板 B 全 5 因子重跑)
#
# Run from any cwd; uses absolute paths.
# Output goes to reports/factor_ic/<factor>_ic.json (overwrites archived pre-rerun versions).

set -e  # exit on error

REPO="e:/Data/chongweihuang/Desktop/project/Stock-Trading"
PY="C:/Users/chongweihuang/AppData/Local/miniconda3/envs/quant/python.exe"
RUN_SCRIPT="$REPO/scripts/run_factor_ic.py"
OUT_DIR="$REPO/reports/factor_ic"
CFG="$REPO/config/settings.yaml"

# Set PYTHONPATH so `from src.X import Y` resolves
export PYTHONPATH="$REPO"

START="2020-01-01"
END="2025-12-31"

LOG_DIR="$REPO/reports/factor_ic/_audit"
mkdir -p "$LOG_DIR"

run_factor() {
    local factor=$1
    local log="$LOG_DIR/fresh_rerun_${factor}_2026-05-10.log"
    echo "============================================================"
    echo "[$(date)] Fresh rerun: $factor"
    echo "  Log: $log"
    echo "============================================================"
    "$PY" -u "$RUN_SCRIPT" \
        --factor "$factor" \
        --start "$START" \
        --end "$END" \
        --output-dir "$OUT_DIR" \
        --config "$CFG" \
        2>&1 | tee "$log"
    echo "[$(date)] Done: $factor"
    echo ""
}

# Run in order (margin_short first since it's the only other PIT-affected one)
run_factor "margin_short_ratio"
run_factor "high_proximity"
run_factor "revenue_momentum_v2"
run_factor "pead_eps"

echo "============================================================"
echo "[$(date)] All 4 remaining factors done."
echo "Per-factor logs: $LOG_DIR/fresh_rerun_<factor>_2026-05-10.log"
echo "============================================================"
