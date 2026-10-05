"""Per-symbol historical backtest: replays the same pattern detectors used live over
cached candle history and feeds every detected pattern's actual outcome into
learning/weights.py's pattern_stats table. This is what lets a fresh symbol start with a
meaningful prior instead of the flat 0.5 default, and is how the tool 'learns the
patterns specific to that chart's own history', as requested.
"""

import logging

from core.config import learning as learning_config
from data.cache import refresh_candles
from indicators.technical import compute_indicators
from learning.tracker import lookforward_candles_for, resolve_outcome
from learning.weights import update_pattern_stats
from patterns.chart_patterns import detect_all as detect_chart_patterns
from patterns.chart_patterns import find_swings
from signals.risk import compute_risk_plan

logger = logging.getLogger(__name__)
SWING_ORDER = 5


def backtest_symbol_timeframe(symbol: str, timeframe: str, limit: int = 500, min_window: int = 60) -> int:
    """Walks history bar by bar, detects patterns as of each bar, resolves their outcome
    against the following candles, and records it. Returns how many samples were recorded."""
    df = refresh_candles(symbol, timeframe, limit=limit)
    lookforward = lookforward_candles_for(timeframe, learning_config.outcome_lookforward_candles)
    if len(df) < min_window + lookforward + 1:
        return 0

    df_ind = compute_indicators(df)
    # Computed once on the full series instead of once per bar -- see detect_all()'s
    # docstring for why this is equivalent, not just faster.
    all_peaks, all_troughs = find_swings(df_ind, order=SWING_ORDER)
    count = 0
    for i in range(min_window, len(df_ind) - lookforward):
        window = df_ind.iloc[: i + 1]
        # Only confirmed breakouts get backtested into pattern_stats -- a forming pattern at
        # bar i has no resolved outcome to grade.
        matches = [
            m for m in detect_chart_patterns(window, swing_order=SWING_ORDER, peaks=all_peaks, troughs=all_troughs)
            if m.get("status") == "confirmed"
        ]
        if not matches:
            continue

        entry_price = float(window["close"].iloc[-1])
        atr = float(window["atr"].iloc[-1]) or entry_price * 0.01
        future = df_ind.iloc[i + 1 : i + 1 + lookforward]

        for match in matches:
            plan = compute_risk_plan(
                direction=match["direction"],
                entry_price=entry_price,
                atr=atr,
                invalidation_price=match["invalidation_price"],
                target_price=match["target_price"],
            )
            result = resolve_outcome(future, match["direction"], entry_price, plan.stop_price, plan.target_price)
            if result is None:
                continue
            outcome, return_pct, _exit_price, _reason = result
            update_pattern_stats(match["pattern"], timeframe, symbol, outcome == "win", return_pct)
            count += 1
    return count


def seed_symbol_history(symbols: list[str], timeframes: list[str], limit: int = 500) -> int:
    total = 0
    for symbol in symbols:
        for timeframe in timeframes:
            try:
                total += backtest_symbol_timeframe(symbol, timeframe, limit=limit)
            except Exception:
                logger.exception("Backtest failed for %s %s", symbol, timeframe)
    return total
