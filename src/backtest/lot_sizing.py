"""Whole-lot position sizing — single source of truth (v8.1 realism fix, 2026-05-22).

Problem
-------
The backtest path uses continuous fractional weights, but a NT$1,000,000 retail
account buying Taiwan stocks trades in **whole lots** (1 lot = 1,000 shares).
At top_n=16 each slice is ~NT$62,500; a single lot of a high-priced stock costs
hundreds of thousands — so a large share of the top-80 universe simply cannot be
held. v7's continuous-weight backtest therefore carried an un-modelled optimistic
bias. This module models the whole-lot constraint so the impact can be measured.

Policy (v8.1 design lock — see plan stock-swirling-elephant.md)
--------------------------------------------------------------
- Fixed equal slice per name: ``slice_i = nav / top_n`` (caller supplies the
  target weights; v8.1 uses 1/top_n each).
- Within a slice, buy ``floor(slice / (price * LOT_SIZE))`` whole lots.
- **No substitution**: an unaffordable name is simply not held; its slice
  residual becomes cash earning 0%.
- This is a pure function: same input -> same output. NAV compounding lives in
  the backtest loop, not here.

Whole-lot only — intraday odd-lot trading (available in TW from 2020-10-26) is
deliberately excluded so the model is valid across the entire 2019+ sample and
keeps v8.1 to a single variable. Odd-lot is a separate v8.1b sensitivity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Taiwan stock board lot = 1,000 shares. Single definition point.
LOT_SIZE = 1000


@dataclass(frozen=True)
class LotPosition:
    """Whole-lot sizing outcome for a single symbol."""

    symbol: str
    target_weight: float        # ideal weight the caller asked for (e.g. 1/top_n)
    price: float                # PIT close price per share (NT$); NaN/<=0 if bad
    slice_capital: float        # nav * target_weight — the budget for this name
    lots: int                   # floor(slice_capital / (price * LOT_SIZE))
    invested_capital: float     # lots * price * LOT_SIZE
    residual_cash: float        # slice_capital - invested_capital (-> cash, 0%)
    actual_weight: float        # invested_capital / nav
    feasible: bool              # lots >= 1 (could afford at least one lot)


@dataclass(frozen=True)
class LotSizingResult:
    """Aggregate whole-lot sizing outcome for one rebalance."""

    nav: float
    positions: list[LotPosition]
    total_invested: float
    total_residual_cash: float
    invested_ratio: float           # total_invested / nav  (1 - cash drag)
    n_feasible: int                 # names with >= 1 lot
    n_target: int                   # names the caller asked to size
    actual_weights: dict[str, float]  # symbol -> actual_weight (0.0 if infeasible)
    weight_deviation_l1: float      # sum_i |actual_weight_i - target_weight_i|


def _is_valid_price(price: float | None) -> bool:
    """A price is usable only if it is a finite positive number."""
    return price is not None and math.isfinite(price) and price > 0


def size_whole_lots(
    target_weights: dict[str, float],
    prices: dict[str, float],
    nav: float,
    *,
    lot_size: int = LOT_SIZE,
) -> LotSizingResult:
    """Convert ideal target weights into achievable whole-lot positions.

    Parameters
    ----------
    target_weights : {symbol: weight}
        Ideal portfolio weights (v8.1: 1/top_n for each selected name).
    prices : {symbol: price}
        PIT close price per share (NT$). Must have exactly the same keys as
        ``target_weights``.
    nav : float
        Current portfolio net asset value (NT$). Must be > 0.
    lot_size : int
        Shares per lot (default 1,000; set to 1 in tests to recover the
        continuous-weight limit).

    Returns
    -------
    LotSizingResult

    Raises
    ------
    ValueError
        If ``nav <= 0`` or ``target_weights`` / ``prices`` have mismatched keys
        (fail-fast — that indicates a caller bug).
    """
    if not (nav > 0):
        raise ValueError(f"nav must be > 0, got {nav!r}")
    if set(target_weights) != set(prices):
        raise ValueError(
            "target_weights and prices must have identical keys; "
            f"target-only={set(target_weights) - set(prices)}, "
            f"price-only={set(prices) - set(target_weights)}"
        )

    positions: list[LotPosition] = []
    actual_weights: dict[str, float] = {}
    total_invested = 0.0
    total_residual_cash = 0.0
    n_feasible = 0
    weight_deviation_l1 = 0.0

    for symbol, target_weight in target_weights.items():
        price = prices[symbol]
        slice_capital = nav * target_weight

        if _is_valid_price(price):
            lots = math.floor(slice_capital / (price * lot_size))
        else:
            lots = 0
        if lots < 0:
            lots = 0  # defensive: a negative slice (weight<0) buys nothing

        invested_capital = lots * price * lot_size if _is_valid_price(price) else 0.0
        residual_cash = slice_capital - invested_capital
        actual_weight = invested_capital / nav
        feasible = lots >= 1

        positions.append(LotPosition(
            symbol=symbol,
            target_weight=target_weight,
            price=price,
            slice_capital=slice_capital,
            lots=lots,
            invested_capital=invested_capital,
            residual_cash=residual_cash,
            actual_weight=actual_weight,
            feasible=feasible,
        ))
        actual_weights[symbol] = actual_weight
        total_invested += invested_capital
        total_residual_cash += residual_cash
        n_feasible += int(feasible)
        weight_deviation_l1 += abs(actual_weight - target_weight)

    return LotSizingResult(
        nav=nav,
        positions=positions,
        total_invested=total_invested,
        total_residual_cash=total_residual_cash,
        invested_ratio=total_invested / nav,
        n_feasible=n_feasible,
        n_target=len(target_weights),
        actual_weights=actual_weights,
        weight_deviation_l1=weight_deviation_l1,
    )


def compute_gross_return(
    actual_weights: dict[str, float],
    stock_returns: dict[str, float],
) -> float:
    """Portfolio gross return from whole-lot actual weights.

    ``gross = sum_i actual_weight_i * r_i``. The actual weights sum to <= 1; the
    uninvested remainder is implicitly cash earning 0% (no top-up). A symbol with
    no return available (e.g. suspended over the whole period) contributes 0.

    This is the whole-lot replacement for v7's ``np.mean(rets)`` equal-weight
    aggregation in d_cell_sweep_v7_real.py.
    """
    return sum(
        weight * stock_returns.get(symbol, 0.0)
        for symbol, weight in actual_weights.items()
    )
