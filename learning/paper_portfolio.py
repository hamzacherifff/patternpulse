"""A fully automatic paper-trading replay: every signal the tool has ever generated, taken
exactly as recommended, starting from a persisted fictional balance -- no manual logging
required, unlike core/positions.py's Positions page (the user's own real or by-hand trades).
This answers "how much would I have if I'd started with X and followed every signal" as a
live, always-current number, not a one-off calculation.

Position sizing compounds: `position_size_suggested / portfolio_size_at_signal` is the exact
fraction of portfolio each signal was risk-sized to, independent of whatever portfolio size was
actually configured when it fired (the ratio cancels portfolio size out algebraically, since
position_size = portfolio * risk_pct/100 / stop_distance). Multiplying that fraction by a
simulated running balance reproduces the same risk-scaled compounding the live system would
have used against a different starting number.

On top of that base risk-fraction, the paper portfolio additionally scales size by conviction
-- but conviction is measured by CALIBRATED confidence (what this confidence level has
actually delivered historically), not the raw reported score. This was a real bug, not a
theoretical concern: production data showed the 0.9-1.0 confidence bucket claimed ~95% wins
but actually won only 75% of the time, and leverage was scaling directly off the raw (inflated)
number -- putting the biggest bets on exactly the least-reliable tier. Combined with a base
risk_fraction that can itself already be near 100% of the account on a tight-stop signal (it
has its own cap inside signals/risk.py, but that cap didn't account for THIS module's leverage
stacking on top of it), real production trades were sized at up to 243% of the account in a
single position. Both are fixed here: MAX_POSITION_FRACTION caps the combined (base x leverage)
fraction, and leverage is computed from calibrated_confidence() instead of the raw score.
"""

import pandas as pd

from core.db import get_db
from core.settings import get_float_setting, set_setting

DEFAULT_STARTING_CAPITAL = 1000.0
_SETTING_KEY = "paper_starting_capital"

# CONFIDENCE_FLOOR matches signals/engine.py's CONFIDENCE_THRESHOLD -- the minimum confidence
# a signal must clear to fire at all, so it's the natural "least conviction possible"
# reference point, not an arbitrary number. CONFIDENCE_CAP matches the engine's own cap
# (confidence is always clamped to <= 0.99). A signal at the floor takes the full normal
# risk-sized position (MIN_LEVERAGE=1.0); a signal at the cap gets MAX_LEVERAGE x that;
# everything between is a straight linear ramp -- applied to CALIBRATED confidence, see
# calibrated_confidence() below, not the raw score.
CONFIDENCE_FLOOR = 0.55
CONFIDENCE_CAP = 0.99
MIN_LEVERAGE = 1.0
MAX_LEVERAGE = 5.0

# The base risk_fraction is already capped at 1.0 inside signals/risk.py's own sizing formula,
# but that cap doesn't account for THIS module's leverage multiplier stacking on top of it --
# see the module docstring for the real production example (0.487 base x 4.99x leverage =
# 2.43x the account in one trade). This caps the COMBINED fraction at a modest share of the
# current balance PER POSITION. It's deliberately much lower than "100% of the account" (the
# first version of this fix): the scanner routinely has a dozen-plus signals open at once
# across different symbols/timeframes, and get_open_paper_positions() sizes each one against
# the SAME account balance -- with no per-position cap tight enough to leave room for others,
# production had 12 of 26 concurrently open positions each independently sized at 100% of the
# account (well over 1000% of total notional exposure live at once). See
# get_open_paper_positions() for the other half of this fix: positions are now also allocated
# capital sequentially against what's actually still available, not the full balance every time.
MAX_POSITION_FRACTION = 0.20

# Confidence buckets for calibration -- must match pages/3_Performance.py's calibration chart
# bins so "what leverage uses" and "what the chart shows you" are always the same thing.
_CALIBRATION_BINS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
CALIBRATION_MIN_SAMPLES = 15  # below this, a bucket's empirical rate is too noisy to trust over the raw score


def get_starting_capital() -> float:
    return get_float_setting(_SETTING_KEY, DEFAULT_STARTING_CAPITAL)


def set_starting_capital(value: float) -> None:
    set_setting(_SETTING_KEY, value)


def load_calibration_table() -> pd.DataFrame:
    """One query + groupby, computed once per compute_paper_history()/get_open_paper_positions()
    call (not per row) and threaded through -- indexed by confidence-bucket position, columns
    actual_win_rate/n. Empty DataFrame if nothing has resolved yet."""
    conn = get_db()
    rows = conn.execute(
        "SELECT s.confidence, o.outcome FROM outcomes o JOIN signals s ON s.id = o.signal_id"
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["bucket_idx"] = pd.cut(df["confidence"], bins=_CALIBRATION_BINS, include_lowest=True, labels=False)
    return df.groupby("bucket_idx").agg(actual_win_rate=("outcome", lambda s: (s == "win").mean()), n=("outcome", "size"))


def calibrated_confidence(confidence: float, table: pd.DataFrame) -> float:
    """Looks up the ACTUAL historical win rate for signals at this confidence level and uses
    that instead of the raw score, when there's enough history in that bucket to trust it --
    this is what makes leverage self-correct against overconfidence instead of amplifying it.
    Falls back to the raw confidence when the table is empty or that specific bucket is too
    thin (nothing to correct against yet)."""
    if table is None or table.empty:
        return confidence
    idx = pd.cut([confidence], bins=_CALIBRATION_BINS, include_lowest=True, labels=False)[0]
    if idx not in table.index:
        return confidence
    row = table.loc[idx]
    if row["n"] < CALIBRATION_MIN_SAMPLES:
        return confidence
    return float(row["actual_win_rate"])


def confidence_leverage(confidence: float, calibration_table: pd.DataFrame = None) -> float:
    """Linear ramp from MIN_LEVERAGE at CONFIDENCE_FLOOR to MAX_LEVERAGE at CONFIDENCE_CAP,
    applied to calibrated_confidence() (falls back to the raw score if no calibration table is
    given or there isn't enough history yet), clamped at both ends."""
    calibrated = calibrated_confidence(confidence, calibration_table)
    span = CONFIDENCE_CAP - CONFIDENCE_FLOOR
    t = (calibrated - CONFIDENCE_FLOOR) / span if span else 0.0
    t = min(max(t, 0.0), 1.0)
    return MIN_LEVERAGE + t * (MAX_LEVERAGE - MIN_LEVERAGE)


def compute_paper_history(starting_capital: float) -> pd.DataFrame:
    """Every resolved signal, in the order it resolved, with a running `simulated_balance`
    column. Empty DataFrame if nothing has resolved yet."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT s.id, s.symbol, s.timeframe, s.pattern_type, s.direction, s.entry_price,
               s.confidence, s.position_size_suggested, s.portfolio_size_at_signal,
               o.outcome, o.return_pct, o.resolved_at
        FROM outcomes o JOIN signals s ON s.id = o.signal_id
        ORDER BY o.resolved_at ASC
        """
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df

    calibration_table = load_calibration_table()
    risk_fraction = (df["position_size_suggested"] / df["portfolio_size_at_signal"]).replace(
        [float("inf"), -float("inf")], 0
    ).fillna(0.0)
    leverage = df["confidence"].apply(lambda c: confidence_leverage(c, calibration_table))
    effective_fraction = (risk_fraction * leverage).clip(upper=MAX_POSITION_FRACTION)

    balance = starting_capital
    balances, sizes = [], []
    for frac, return_pct in zip(effective_fraction, df["return_pct"]):
        size = balance * frac
        balance += size * (return_pct / 100)
        balances.append(balance)
        sizes.append(size)
    df["risk_fraction"] = risk_fraction
    df["confidence_leverage"] = leverage
    df["simulated_position_size"] = sizes
    df["simulated_realized_pnl"] = df["simulated_position_size"] * (df["return_pct"] / 100)
    df["simulated_balance"] = balances
    return df


def current_paper_balance(starting_capital: float) -> float:
    history = compute_paper_history(starting_capital)
    return float(history["simulated_balance"].iloc[-1]) if not history.empty else starting_capital


def get_open_paper_positions(current_balance: float) -> list[dict]:
    """Every currently-open signal, auto-sized against the paper account's AVAILABLE (not
    total) balance -- positions are allocated capital sequentially in the order they were
    actually opened, and each one can only use what's left after earlier still-open positions
    have already claimed their share, exactly like a real account with one shared, finite pool
    of capital. Sizing every position independently against the FULL balance (the original
    version of this function) let every one of N concurrently open positions claim up to
    MAX_POSITION_FRACTION of the account *each*, all at once -- confirmed in production with
    12 of 26 simultaneously open positions each sized at 100% of the account, well over 1000%
    of total notional exposure live at the same time. That's what was actually driving large
    unrealized swings, not any single trade."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, symbol, timeframe, pattern_type, direction, entry_price, stop_price,
               target_price, confidence, position_size_suggested, portfolio_size_at_signal, created_at
        FROM signals WHERE status = 'open' ORDER BY created_at ASC
        """
    ).fetchall()
    if not rows:
        return []

    calibration_table = load_calibration_table()
    available = current_balance
    result = []
    for r in rows:
        d = dict(r)
        base_frac = (d["position_size_suggested"] / d["portfolio_size_at_signal"]) if d["portfolio_size_at_signal"] else 0.0
        leverage = confidence_leverage(d["confidence"], calibration_table)
        wanted = current_balance * min(base_frac * leverage, MAX_POSITION_FRACTION)
        size = max(min(wanted, available), 0.0)
        d["confidence_leverage"] = leverage
        d["simulated_position_size"] = size
        available -= size
        result.append(d)

    result.reverse()  # allocated oldest-first, but displayed newest-first like before
    return result
