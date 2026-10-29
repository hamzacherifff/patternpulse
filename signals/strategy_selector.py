"""Decides which (pattern, symbol) combinations the scanner is currently allowed to generate
signals for, and how many signals can be open at once -- instead of scanning every symbol x
timeframe x pattern combination uniformly forever and letting the open-signal count grow
without bound (production reached 180+ concurrently open signals before this existed, most of
which the paper portfolio couldn't even fund -- see learning/paper_portfolio.py).

Explore/exploit, not a permanent blacklist: a pattern type or symbol with too little history
is still allowed through (there's no way to know if something works without trying it enough
times), but once either has accumulated enough resolved samples to judge, it only stays active
if its own aggregate expectancy (avg_return) is non-negative -- a pattern or symbol that's
demonstrably losing money gets benched, freeing up scanner attention and open-signal slots for
demonstrated winners, using the exact real finding from production: pattern type alone ranged
from a 68% win rate (head_and_shoulders) down to 14% (triple_bottom) with enough samples on
both to actually judge them, a much stronger and faster-to-mature signal than any single
(pattern, timeframe, symbol) slice.

This only gates NEW signal generation -- already-open signals are left alone and resolve
normally through the existing outcome_updater pipeline, since force-closing them early isn't
something the resolution methodology supports and would need separate, deliberate design.
"""

from core.db import get_db
from learning.weights import (
    get_pattern_symbol_performance,
    get_pattern_timeframe_performance,
    get_pattern_type_performance,
)

MIN_SAMPLES_TO_JUDGE = 15  # matches learning/paper_portfolio.py's CALIBRATION_MIN_SAMPLES
MIN_EXPECTANCY_TO_KEEP = 0.0  # avg_return must clear this once there's enough history to judge
MAX_CONCURRENT_OPEN_SIGNALS = 20  # a bounded number of live ideas, not every qualifying signal at once


def is_combo_allowed(pattern_type: str, symbol: str, timeframe: str) -> bool:
    """False once any of three independent, non-contaminating axes has enough resolved
    history to be judged (>= MIN_SAMPLES_TO_JUDGE) AND shows a negative aggregate average
    return: the pattern type overall, this (pattern_type, timeframe) pair, or this
    (pattern_type, symbol) pair. True in every other case, including "not enough data yet"
    -- that's still exploration, not a green light being withheld.

    Each axis is checked at a grain that can't contaminate an unrelated good combo: pattern
    type alone, pattern+timeframe, pattern+symbol. Deliberately does NOT check symbol alone
    across every pattern type -- an earlier version did, and real production data showed it
    blocked a genuinely good pattern (double_bottom, +0.278% avg_return) on every symbol,
    because each symbol also hosted unrelated bad-performing patterns that dragged its
    blended average negative. Symbol-level signal is instead applied as a continuous sizing
    dial, not a veto -- see learning/weights.py:get_symbol_sizing_multiplier.

    The (pattern_type, timeframe) axis was added after production data showed the same
    pattern type can have wildly different reward:risk by timeframe (5m median 0.31 vs 4h
    median 0.85) -- a pattern that's fine on slow timeframes can still be a structurally bad
    bet on fast ones."""
    pattern_perf = get_pattern_type_performance(pattern_type)
    if pattern_perf["total"] >= MIN_SAMPLES_TO_JUDGE and pattern_perf["avg_return"] < MIN_EXPECTANCY_TO_KEEP:
        return False
    tf_perf = get_pattern_timeframe_performance(pattern_type, timeframe)
    if tf_perf["total"] >= MIN_SAMPLES_TO_JUDGE and tf_perf["avg_return"] < MIN_EXPECTANCY_TO_KEEP:
        return False
    pair_perf = get_pattern_symbol_performance(pattern_type, symbol)
    if pair_perf["total"] >= MIN_SAMPLES_TO_JUDGE and pair_perf["avg_return"] < MIN_EXPECTANCY_TO_KEEP:
        return False
    return True


def has_room_for_new_signal() -> bool:
    """Hard concurrency cap, checked at the point a signal would actually be persisted
    (learning/tracker.py's record_signal) so it applies uniformly whether the signal came from
    the background scanner or a manual "Scan now" click."""
    conn = get_db()
    open_count = conn.execute("SELECT COUNT(*) AS c FROM signals WHERE status = 'open'").fetchone()["c"]
    return open_count < MAX_CONCURRENT_OPEN_SIGNALS
