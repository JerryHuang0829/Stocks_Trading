"""小額實盤追蹤記錄器。

記錄實際買賣、計算報酬、與 paper trading 比較。

Usage:
    # 記錄買入
    python scripts/real_trade.py buy 2360 12 158.5
    python scripts/real_trade.py buy 2337 25 201.0

    # 記錄賣出
    python scripts/real_trade.py sell 2360 12 165.0

    # 月結（輸入當前持股市值，計算月報酬）
    python scripts/real_trade.py close --date 2026-04

    # 查看目前持股
    python scripts/real_trade.py status

    # 查看績效報告（vs paper trading）
    python scripts/real_trade.py report
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

# Windows 終端 UTF-8 支援
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_TRADING_DIR = PROJECT_ROOT / "reports" / "real_trading"
PAPER_TRADING_DIR = PROJECT_ROOT / "reports" / "paper_trading"
TRADES_FILE = REAL_TRADING_DIR / "trades.json"
PORTFOLIO_FILE = REAL_TRADING_DIR / "portfolio.json"
PERFORMANCE_FILE = REAL_TRADING_DIR / "performance.json"


def _load_json(path: Path) -> list | dict:
    if not path.exists():
        return [] if path.name in ("trades.json", "performance.json") else {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_portfolio() -> dict:
    """載入目前持股。格式：{symbol: {shares, avg_cost, total_cost}}"""
    data = _load_json(PORTFOLIO_FILE)
    return data if isinstance(data, dict) else {}


def cmd_buy(symbol: str, shares: int, price: float):
    """記錄買入。"""
    trades = _load_json(TRADES_FILE)
    portfolio = _load_portfolio()

    cost = shares * price
    fee = max(20, round(cost * 0.001425))  # 手續費最低 20 元
    total_cost = cost + fee

    trade = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "action": "BUY",
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "cost": cost,
        "fee": fee,
        "total": total_cost,
    }
    trades.append(trade)
    _save_json(TRADES_FILE, trades)

    # 更新持股
    if symbol in portfolio:
        old = portfolio[symbol]
        new_shares = old["shares"] + shares
        new_total_cost = old["total_cost"] + total_cost
        portfolio[symbol] = {
            "shares": new_shares,
            "avg_cost": round(new_total_cost / new_shares, 2),
            "total_cost": round(new_total_cost, 2),
        }
    else:
        portfolio[symbol] = {
            "shares": shares,
            "avg_cost": round(total_cost / shares, 2),
            "total_cost": round(total_cost, 2),
        }
    _save_json(PORTFOLIO_FILE, portfolio)

    print(f"✅ 買入 {symbol} × {shares} 股 @ {price} 元")
    print(f"   成本 {cost:,.0f} + 手續費 {fee} = 合計 {total_cost:,.0f} 元")
    print(f"   目前持有 {portfolio[symbol]['shares']} 股，均價 {portfolio[symbol]['avg_cost']} 元")


def cmd_sell(symbol: str, shares: int, price: float):
    """記錄賣出。"""
    trades = _load_json(TRADES_FILE)
    portfolio = _load_portfolio()

    if symbol not in portfolio or portfolio[symbol]["shares"] < shares:
        print(f"❌ 持股不足：{symbol} 目前持有 {portfolio.get(symbol, {}).get('shares', 0)} 股")
        return

    revenue = shares * price
    fee = max(20, round(revenue * 0.001425))  # 手續費
    tax = round(revenue * 0.003)  # 證交稅 0.3%
    net = revenue - fee - tax

    avg_cost = portfolio[symbol]["avg_cost"]
    cost_basis = shares * avg_cost
    profit = net - cost_basis
    profit_pct = profit / cost_basis if cost_basis > 0 else 0

    trade = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "action": "SELL",
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "revenue": revenue,
        "fee": fee,
        "tax": tax,
        "net": round(net, 2),
        "profit": round(profit, 2),
        "profit_pct": round(profit_pct, 4),
    }
    trades.append(trade)
    _save_json(TRADES_FILE, trades)

    # 更新持股
    remaining = portfolio[symbol]["shares"] - shares
    if remaining <= 0:
        del portfolio[symbol]
    else:
        portfolio[symbol]["shares"] = remaining
        portfolio[symbol]["total_cost"] = round(remaining * avg_cost, 2)
    _save_json(PORTFOLIO_FILE, portfolio)

    emoji = "📈" if profit >= 0 else "📉"
    print(f"✅ 賣出 {symbol} × {shares} 股 @ {price} 元")
    print(f"   收入 {revenue:,.0f} - 手續費 {fee} - 證交稅 {tax} = 淨收 {net:,.0f} 元")
    print(f"   {emoji} 損益 {profit:+,.0f} 元（{profit_pct:+.1%}）")


def cmd_status():
    """顯示目前持股。"""
    portfolio = _load_portfolio()
    if not portfolio:
        print("目前無持股。")
        return

    print("\n" + "=" * 50)
    print("  目前持股")
    print("=" * 50)

    total_cost = 0
    for symbol, info in sorted(portfolio.items()):
        cost = info["total_cost"]
        total_cost += cost
        print(f"  {symbol}  {info['shares']:>5} 股  均價 {info['avg_cost']:>8.2f}  成本 {cost:>10,.0f}")

    print(f"\n  總投入成本：{total_cost:,.0f} 元")
    print("=" * 50)
    print("\n  提示：用 'python scripts/real_trade.py close --date 2026-04' 進行月結")


def cmd_close(month_key: str):
    """月結：記錄當月績效。需手動輸入各持股的當前市價。"""
    portfolio = _load_portfolio()
    if not portfolio:
        print("目前無持股，無法月結。")
        return

    print(f"\n月結 {month_key}：請輸入各持股的當前收盤價")
    print("-" * 40)

    total_cost = 0
    total_market_value = 0
    positions = []

    for symbol, info in sorted(portfolio.items()):
        while True:
            try:
                current_price = float(input(f"  {symbol}（{info['shares']} 股）當前價格："))
                break
            except ValueError:
                print("  請輸入數字。")

        cost = info["total_cost"]
        market_value = info["shares"] * current_price
        profit = market_value - cost
        profit_pct = profit / cost if cost > 0 else 0

        total_cost += cost
        total_market_value += market_value
        positions.append({
            "symbol": symbol,
            "shares": info["shares"],
            "avg_cost": info["avg_cost"],
            "current_price": current_price,
            "market_value": round(market_value, 2),
            "profit": round(profit, 2),
            "profit_pct": round(profit_pct, 4),
        })

    total_profit = total_market_value - total_cost
    total_return = total_profit / total_cost if total_cost > 0 else 0

    # 載入 paper trading 同期數據比較（比對持股重疊度）
    paper_positions = []
    for p in sorted(PAPER_TRADING_DIR.glob(f"{month_key}_*.json")):
        try:
            data = _load_json(p)
            if data.get("positions"):
                paper_positions = data["positions"]
                break  # 使用該月第一筆正式紀錄
        except Exception:
            pass
    # Fallback：嘗試 history.json
    if not paper_positions:
        history_path = PAPER_TRADING_DIR / "history.json"
        if history_path.exists():
            history = _load_json(history_path)
            for h in history:
                if h.get("month_key") == month_key and not h.get("is_rerun") and h.get("positions"):
                    paper_positions = h["positions"]
                    break
    paper_symbols = {p["symbol"] for p in paper_positions}
    real_symbols = set(portfolio.keys())

    # 計算持股重疊度
    overlap = real_symbols & paper_symbols
    overlap_ratio = len(overlap) / len(paper_symbols) if paper_symbols else None

    record = {
        "month_key": month_key,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_cost": round(total_cost, 2),
        "total_market_value": round(total_market_value, 2),
        "total_profit": round(total_profit, 2),
        "total_return": round(total_return, 4),
        "paper_overlap_ratio": overlap_ratio,
        "paper_overlap_symbols": sorted(overlap) if overlap else [],
        "paper_only_symbols": sorted(paper_symbols - real_symbols) if paper_symbols else [],
        "real_only_symbols": sorted(real_symbols - paper_symbols) if paper_symbols else [],
        "positions": positions,
    }

    performance = _load_json(PERFORMANCE_FILE)
    # 覆蓋同月（重新月結）
    performance = [p for p in performance if p.get("month_key") != month_key]
    performance.append(record)
    performance.sort(key=lambda x: x["month_key"])
    _save_json(PERFORMANCE_FILE, performance)

    print("\n" + "=" * 50)
    print(f"  月結 {month_key}")
    print("=" * 50)
    for p in positions:
        emoji = "📈" if p["profit"] >= 0 else "📉"
        print(f"  {p['symbol']}  {p['shares']:>4} 股  成本 {p['avg_cost']:>7.1f}  現價 {p['current_price']:>7.1f}  {emoji} {p['profit']:+,.0f}（{p['profit_pct']:+.1%}）")

    print(f"\n  總成本：{total_cost:>10,.0f} 元")
    print(f"  總市值：{total_market_value:>10,.0f} 元")
    emoji = "📈" if total_profit >= 0 else "📉"
    print(f"  {emoji} 損益：{total_profit:+,.0f} 元（{total_return:+.1%}）")

    if paper_symbols:
        print(f"\n  --- Paper Trading 比對 ---")
        print(f"  Paper 建議持股：{', '.join(sorted(paper_symbols))}")
        print(f"  實盤持股：{', '.join(sorted(real_symbols))}")
        print(f"  重疊率：{overlap_ratio:.0%}（{len(overlap)}/{len(paper_symbols)}）")
        if overlap:
            print(f"  重疊：{', '.join(sorted(overlap))}")
        if paper_symbols - real_symbols:
            print(f"  Paper 有/實盤無：{', '.join(sorted(paper_symbols - real_symbols))}")
        if real_symbols - paper_symbols:
            print(f"  實盤有/Paper 無：{', '.join(sorted(real_symbols - paper_symbols))}")
    print("=" * 50)


def cmd_report():
    """績效報告。"""
    performance = _load_json(PERFORMANCE_FILE)
    if not performance:
        print("尚無月結紀錄。請先執行 'close' 命令。")
        return

    trades = _load_json(TRADES_FILE)
    total_fees = sum(t.get("fee", 0) for t in trades)
    total_tax = sum(t.get("tax", 0) for t in trades)

    print("\n" + "=" * 50)
    print("  實盤績效報告")
    print("=" * 50)

    print(f"\n  累積 {len(performance)} 期")
    print(f"  交易手續費合計：{total_fees:,.0f} 元")
    print(f"  證交稅合計：{total_tax:,.0f} 元")

    print(f"\n  {'月份':>8}  {'實盤':>8}  {'重疊率':>8}")
    print("  " + "-" * 32)

    cum_real = 1.0
    for p in performance:
        real_ret = p.get("total_return", 0)
        cum_real *= (1 + real_ret)

        real_str = f"{real_ret:+.1%}"
        overlap = p.get("paper_overlap_ratio")
        overlap_str = f"{overlap:.0%}" if overlap is not None else "—"

        print(f"  {p['month_key']:>8}  {real_str:>8}  {overlap_str:>8}")

    print(f"\n  累積實盤報酬：{cum_real - 1:+.1%}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="小額實盤追蹤")
    sub = parser.add_subparsers(dest="command")

    buy_parser = sub.add_parser("buy", help="記錄買入")
    buy_parser.add_argument("symbol", help="股票代碼")
    buy_parser.add_argument("shares", type=int, help="股數")
    buy_parser.add_argument("price", type=float, help="成交價")

    sell_parser = sub.add_parser("sell", help="記錄賣出")
    sell_parser.add_argument("symbol", help="股票代碼")
    sell_parser.add_argument("shares", type=int, help="股數")
    sell_parser.add_argument("price", type=float, help="成交價")

    sub.add_parser("status", help="查看目前持股")

    close_parser = sub.add_parser("close", help="月結")
    close_parser.add_argument("--date", required=True, help="月份 (YYYY-MM)")

    sub.add_parser("report", help="績效報告")

    args = parser.parse_args()

    if args.command == "buy":
        cmd_buy(args.symbol, args.shares, args.price)
    elif args.command == "sell":
        cmd_sell(args.symbol, args.shares, args.price)
    elif args.command == "status":
        cmd_status()
    elif args.command == "close":
        cmd_close(args.date)
    elif args.command == "report":
        cmd_report()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
