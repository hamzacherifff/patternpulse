"""Historical pattern scan: walks a symbol's cached candle history and returns every
*confirmed* pattern occurrence that would have cleared the live engine's confidence bar
-- including ones from before this tool existed. This is what lets the Live Charts page
show 'past signals' on the chart, each labeled with the specific pattern that justifies
it. A separate function, `scan_forming_patterns`, answers a different question: what's
actively forming *right now* (not yet broken out, not yet invalidated) -- these aren't
scanned across history since a near-miss from days ago isn't actionable today.

Deliberately simpler than signals/engine.py's live scoring: it uses the same-timeframe
trend (price vs SMA50) as a trend-confluence proxy instead of fetching a separate higher
timeframe for every historical bar, and it scores against the pattern_stats table as it
exists *now* rather than as it existed at that point in time. Both are fine trade-offs for
a chart-annotation feature -- neither this module nor its output feeds the learning loop.

The `_df` variants take an already-fetched, already-indicator-computed DataFrame so a
caller that needs both this module's output *and* the raw candles for something else
(e.g. the Live Charts page, which draws them) only pays for one Binance round-trip
instead of two.
"""

import pandas as pd

from data.cache import refresh_candles
from indicators.technical import compute_indicators, macd_bearish_cross, macd_bullish_cross, trend_direction
from learning.weights import get_pattern_weight
from patterns.candlestick import detect_candlestick_patterns
from patterns.chart_patterns import detect_all as detect_chart_patterns
from patterns.chart_patterns import find_swings

CONFIDENCE_THRESHOLD = 0.55
MIN_WINDOW = 60
SWING_ORDER = 5
# The historical pattern scan is O(bars scanned) -- each bar re-runs every detector, and
# with ~20 detectors that adds up fast on pandas indexing overhead. Recent pattern history
# matters far more than what happened 900 bars back, so cap how far the scan walks
# regardless of how many candles the chart itself is showing.
MAX_SCAN_BARS = 150


def _confirmation_score(df_ind, i: int, direction: str) -> float:
    if direction == "long":
        macd_ok = bool(macd_bullish_cross(df_ind, i) or df_ind["macd_diff"].iloc[i] > 0)
        rsi_ok = bool(df_ind["rsi"].iloc[i] < 75)
    else:
        macd_ok = bool(macd_bearish_cross(df_ind, i) or df_ind["macd_diff"].iloc[i] < 0)
        rsi_ok = bool(df_ind["rsi"].iloc[i] > 25)
    volume_ok = bool(df_ind["volume_spike"].iloc[i])
    return sum([macd_ok, rsi_ok, volume_ok]) / 3


def scan_symbol_history_df(df_ind: pd.DataFrame, symbol: str, timeframe: str, min_window: int = MIN_WINDOW) -> list[dict]:
    """Same as `scan_symbol_history` but takes an already-fetched, indicator-computed frame."""
    if len(df_ind) < min_window + 1:
        return []
    all_peaks, all_troughs = find_swings(df_ind, order=SWING_ORDER)

    scan_start = max(min_window, len(df_ind) - MAX_SCAN_BARS)
    results = []
    for i in range(scan_start, len(df_ind)):
        window = df_ind.iloc[: i + 1]
        matches = [
            m for m in detect_chart_patterns(window, swing_order=SWING_ORDER, peaks=all_peaks, troughs=all_troughs)
            if m.get("status") == "confirmed"
        ]
        if not matches:
            continue

        candle_matches = detect_candlestick_patterns(df_ind, i)
        candle_directions = {m["direction"] for m in candle_matches}
        trend = trend_direction(df_ind, i)

        for match in matches:
            direction = match["direction"]
            confirm_score = _confirmation_score(df_ind, i, direction)
            candle_confirms = direction in candle_directions
            trend_agrees = trend == "flat" or (trend == "up" and direction == "long") or (trend == "down" and direction == "short")

            weight = get_pattern_weight(match["pattern"], timeframe, symbol)
            base_score = weight["win_rate"] * match["strength"]
            bonus = 0.10 * confirm_score + (0.05 if candle_confirms else 0) + (0.05 if trend_agrees else 0)
            confidence = min(base_score + bonus, 0.99)
            if confidence < CONFIDENCE_THRESHOLD:
                continue

            results.append({
                **match,
                "trigger_index": i,
                "confidence": round(confidence, 4),
                "entry_price": float(df_ind["close"].iloc[i]),
            })
    return results


def scan_symbol_history(symbol: str, timeframe: str, limit: int = 500, min_window: int = MIN_WINDOW) -> list[dict]:
    """Returns every historical *confirmed* pattern occurrence for (symbol, timeframe) that
    would have met the live engine's confidence bar, each with full geometry for chart
    drawing and a `trigger_index` locating it in the candle DataFrame the caller fetched
    separately."""
    df_ind = compute_indicators(refresh_candles(symbol, timeframe, limit=limit))
    return scan_symbol_history_df(df_ind, symbol, timeframe, min_window)


def scan_forming_patterns_df(df_ind: pd.DataFrame, symbol: str, timeframe: str, min_window: int = MIN_WINDOW) -> list[dict]:
    """Same as `scan_forming_patterns` but takes an already-fetched, indicator-computed frame."""
    if len(df_ind) < min_window + 1:
        return []
    i = len(df_ind) - 1

    matches = [m for m in detect_chart_patterns(df_ind) if m.get("status") == "forming"]
    if not matches:
        return []

    candle_matches = detect_candlestick_patterns(df_ind, i)
    candle_directions = {m["direction"] for m in candle_matches}
    trend = trend_direction(df_ind, i)

    results = []
    for match in matches:
        direction = match["direction"]
        weight = get_pattern_weight(match["pattern"], timeframe, symbol)
        trend_agrees = trend == "flat" or (trend == "up" and direction == "long") or (trend == "down" and direction == "short")
        results.append({
            **match,
            "trigger_index": i,
            "entry_price": float(df_ind["close"].iloc[i]),
            "historical_win_rate": weight["win_rate"],
            "sample_size": weight["total"],
            "candle_confirms": direction in candle_directions,
            "trend_agrees": trend_agrees,
        })
    results.sort(key=lambda m: m["progress"], reverse=True)
    return results


def scan_forming_patterns(symbol: str, timeframe: str, limit: int = 500, min_window: int = MIN_WINDOW) -> list[dict]:
    """Returns whatever chart patterns are actively forming right now (the latest bar) for
    (symbol, timeframe) -- geometry present, no breakout yet, not already invalidated. Each
    result carries `progress` (0..1 toward breakout), `missing` (what's still needed), and
    the pattern's historical win-rate for context."""
    df_ind = compute_indicators(refresh_candles(symbol, timeframe, limit=limit))
    return scan_forming_patterns_df(df_ind, symbol, timeframe, min_window)
