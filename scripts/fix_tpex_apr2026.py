"""Fix TPEX 2026-04 only (4/13 missing data). One FinMind call per stock."""
import os, sys, json, pathlib, time as _time, logging
import pandas as pd
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Load .env
env_path = pathlib.Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Import from validate_cache
from scripts.validate_cache import FinMindRotator, _finmind_raw_to_df

OHLCV_DIR = pathlib.Path("data/cache_new/ohlcv")
FIX_LIST  = pathlib.Path("data/validate_fix_list.json")

def main():
    with open(FIX_LIST) as f:
        raw_data = json.load(f)
    fix_entries = raw_data["fix_list"] if isinstance(raw_data, dict) else raw_data

    # Filter: TPEX stocks with 2026-04
    tpex_apr = [e for e in fix_entries
                if e["source"] == "tpex" and e["year"] == 2026 and e["month"] == 4]
    stocks = sorted({e["stock_id"] for e in tpex_apr})
    logger.info("TPEX 2026-04 fix: %d stocks", len(stocks))

    rotator = FinMindRotator()
    start_str = "2026-04-01"
    end_str   = datetime.now().strftime("%Y-%m-%d")

    updated = skipped = failed = 0
    for sym in stocks:
        pkl_path = OHLCV_DIR / f"{sym}.pkl"
        if not pkl_path.exists():
            logger.warning("  %s: pkl not found, skip", sym)
            skipped += 1
            continue
        raw = rotator.fetch(sym, start_str, end_str)
        ndf = _finmind_raw_to_df(raw)
        if ndf is None:
            logger.warning("  %s: FinMind returned no data", sym)
            failed += 1
            continue
        df = pd.read_pickle(pkl_path)
        df = pd.concat([df, ndf])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df[df["close"] > 0]
        tmp = pkl_path.with_suffix(".tmp"); df.to_pickle(tmp); tmp.replace(pkl_path)
        last = df.index[-1].date()
        logger.info("  %s: last=%s, rows=%d", sym, last, len(df))
        updated += 1

    logger.info("Done: %d updated, %d skipped, %d failed", updated, skipped, failed)

if __name__ == "__main__":
    main()
