"""Signal persistence and outcome resolution -- the core of the learning loop.

record_signal() is called both by the dashboard (Signal Feed page) and by
jobs/live_scanner.py, with a dedupe guard so re-scanning doesn't spam duplicate rows.
resolve_outcome() is a pure function shared by jobs/outcome_updater.py (live resolution)
and patterns/symbol_history.py (historical backtest), so "what counts as a win" is
defined in exactly one place.
"""

from datetime import datetime, timezone

import pandas as pd

from core.db import get_db
from signals.engine import Signal
from signals.strategy_selector import has_room_for_new_signal

_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


def lookforward_candles_for(timeframe: str, base_candles: int) -> int:
    """core.config.learning.outcome_lookforward_candles (default 30) is a candle COUNT, which
    is a wildly different amount of real time depending on timeframe -- 30 candles is 2.5 hours
    on 5m but 30 hours on 1h. This showed up as a real, measured problem in production: 1h
    signals resolved decisively (hit stop or target) 100% of the time within their 30-candle
    window, while 5m/15m signals timed out inconclusively (`window_expired`, graded on
    whatever price happened to be doing when the clock ran out) roughly 45% of the time --
    they were realistically never given enough real-world time to actually prove themselves.

    This scales the lookforward window so every timeframe gets a comparable amount of *real
    time* to resolve, calibrated directly off `base_candles` measured at 1h (the one timeframe
    with empirical proof that duration is sufficient) rather than a separately-invented
    constant, and floors at `base_candles` so timeframes that already clear the bar (1h and
    slower) are completely unaffected. Capped so a fast timeframe's fetch/scan window never
    grows unreasonably large."""
    target_minutes = _TIMEFRAME_MINUTES.get("1h", 60) * base_candles
    tf_minutes = _TIMEFRAME_MINUTES.get(timeframe, 60)
    scaled = -(-target_minutes // tf_minutes)  # ceil division, no float rounding surprises
    return max(base_candles, min(scaled, 300))


def record_signal(signal: Signal) -> int | None:
    """Persists `signal` unless an open signal already exists for the same
    (symbol, timeframe, pattern_type, direction), or the concurrent-open-signal cap is already
    full (see signals/strategy_selector.py's has_room_for_new_signal -- checked here, the single
    choke point every signal actually gets saved through, whether it came from the background
    scanner or a manual "Scan now" click). Returns the new row id, or None if skipped."""
    conn = get_db()
    existing = conn.execute(
        """
        SELECT id FROM signals
        WHERE symbol = ? AND timeframe = ? AND pattern_type = ? AND direction = ? AND status = 'open'
        """,
        (signal.symbol, signal.timeframe, signal.pattern_type, signal.direction),
    ).fetchone()
    if existing:
        return None
    if not has_room_for_new_signal():
        return None

    row = signal.to_row()
    row["created_at"] = datetime.now(timezone.utc).isoformat()
    row["status"] = "open"
    cur = conn.execute(
        """
        INSERT INTO signals (
            symbol, timeframe, pattern_type, direction, confidence, entry_price, stop_price,
            target_price, risk_pct, position_size_suggested, portfolio_size_at_signal,
            indicators_json, status, created_at
        ) VALUES (
            :symbol, :timeframe, :pattern_type, :direction, :confidence, :entry_price, :stop_price,
            :target_price, :risk_pct, :position_size_suggested, :portfolio_size_at_signal,
            :indicators_json, :status, :created_at
        )
        """,
        row,
    )
    conn.commit()
    return cur.lastrowid


def resolve_outcome(future_df: pd.DataFrame, direction: str, entry_price: float, stop_price: float, target_price: float):
    """Walks `future_df` (candles strictly after the signal) bar by bar; whichever of
    stop/target is touched first wins. No touch within the window -> 'timeout' against
    the last close. Returns (outcome, return_pct, exit_price, exit_reason)."""
    for _, row in future_df.iterrows():
        if direction == "long":
            hit_stop = row["low"] <= stop_price
            hit_target = row["high"] >= target_price
        else:
            hit_stop = row["high"] >= stop_price
            hit_target = row["low"] <= target_price

        if hit_stop and hit_target:
            # Both touched in the same candle -- assume the worse outcome (stop) since we
            # can't know intra-candle order from OHLC alone.
            exit_price = stop_price
            outcome = "loss"
            reason = "stop_and_target_same_candle"
        elif hit_stop:
            exit_price, outcome, reason = stop_price, "loss", "stop_hit"
        elif hit_target:
            exit_price, outcome, reason = target_price, "win", "target_hit"
        else:
            continue

        return_pct = (exit_price - entry_price) / entry_price * (1 if direction == "long" else -1)
        return outcome, return_pct * 100, exit_price, reason

    if future_df.empty:
        return None

    exit_price = float(future_df["close"].iloc[-1])
    return_pct = (exit_price - entry_price) / entry_price * (1 if direction == "long" else -1) * 100
    outcome = "win" if return_pct > 0 else "timeout"
    return outcome, return_pct, exit_price, "window_expired"
