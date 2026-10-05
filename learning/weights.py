"""Adaptive win-rate weighting, with hierarchical shrinkage across (pattern, timeframe,
symbol) -> (pattern, timeframe) -> (pattern) -> a flat neutral prior.

Read side is used by signals/engine.py to seed confidence scores. Write side is used by
jobs/outcome_updater.py whenever a signal gets resolved (learning/tracker.py). The write side
and the underlying pattern_stats table are unchanged from before -- stats are still recorded
at the finest (pattern, timeframe, symbol) grain; the hierarchy is applied at READ time by
aggregating those same rows across the coarser levels, so no new tables or write-path changes
were needed for this.

Why the hierarchy: with ~20 chart patterns x 6 timeframes x a dozen-plus watchlist symbols,
there are 1,000+ distinct (pattern, timeframe, symbol) combinations, and MIN_SAMPLES_FOR_TRUST
samples is a real bar to clear at that granularity -- the large majority of combinations will
realistically never individually accumulate enough history to escape the neutral 50/50 prior,
even after months of live scanning. Borrowing statistical strength from the broader (pattern,
timeframe) and (pattern)-only aggregates -- classic hierarchical/empirical-Bayes shrinkage --
means a thin symbol-specific slice still gets an estimate informed by how that pattern
performs generally, instead of regressing straight to a naive coin-flip the moment its own
history is sparse, while a slice with genuinely deep history still gets to speak mostly for
itself (trust -> 1 as its own sample count grows).
"""

from datetime import datetime, timezone

from core.db import get_db

DEFAULT_PRIOR_WIN_RATE = 0.5
MIN_SAMPLES_FOR_TRUST = 10  # applied at every level of the hierarchy, not just the finest one

_STATS_COLS = "wins, losses, total, avg_return"


def _aggregate(rows) -> dict:
    """Sums wins/total/return across a set of pattern_stats rows (e.g. every symbol sharing
    a pattern+timeframe), weighting avg_return by each row's own sample count."""
    total = sum(r["total"] for r in rows)
    wins = sum(r["wins"] for r in rows)
    avg_return = (sum(r["avg_return"] * r["total"] for r in rows) / total) if total else 0.0
    return {"wins": wins, "total": total, "avg_return": avg_return}


def _shrink(win_rate: float, total: int, fallback: float) -> float:
    """Blends `win_rate` toward `fallback` in proportion to how thin `total` is relative to
    MIN_SAMPLES_FOR_TRUST -- the same trust-weighted blend as before, just parameterized so it
    can be chained across hierarchy levels instead of only ever blending toward a flat 0.5."""
    if total == 0:
        return fallback
    trust = min(total / MIN_SAMPLES_FOR_TRUST, 1.0)
    return trust * win_rate + (1 - trust) * fallback


def get_pattern_weight(pattern_type: str, timeframe: str, symbol: str) -> dict:
    """Returns {win_rate, avg_return, total}. `total` is always the exact (pattern, timeframe,
    symbol) sample count (used elsewhere for "n=X" display, so it must stay literal); `win_rate`
    is shrunk through the hierarchy described in this module's docstring."""
    conn = get_db()

    level1_row = conn.execute(
        f"SELECT {_STATS_COLS} FROM pattern_stats WHERE pattern_type = ? AND timeframe = ? AND symbol = ?",
        (pattern_type, timeframe, symbol),
    ).fetchone()
    level2_rows = conn.execute(
        f"SELECT {_STATS_COLS} FROM pattern_stats WHERE pattern_type = ? AND timeframe = ?",
        (pattern_type, timeframe),
    ).fetchall()
    level3_rows = conn.execute(
        f"SELECT {_STATS_COLS} FROM pattern_stats WHERE pattern_type = ?",
        (pattern_type,),
    ).fetchall()

    level3 = _aggregate(level3_rows)
    win_rate3 = _shrink(
        level3["wins"] / level3["total"] if level3["total"] else 0.0, level3["total"], DEFAULT_PRIOR_WIN_RATE
    )

    level2 = _aggregate(level2_rows)
    win_rate2 = _shrink(
        level2["wins"] / level2["total"] if level2["total"] else 0.0, level2["total"], win_rate3
    )

    level1_total = level1_row["total"] if level1_row else 0
    level1_wins = level1_row["wins"] if level1_row else 0
    win_rate1 = _shrink(level1_wins / level1_total if level1_total else 0.0, level1_total, win_rate2)

    if level1_row and level1_row["total"]:
        avg_return = level1_row["avg_return"]
    elif level2["total"]:
        avg_return = level2["avg_return"]
    else:
        avg_return = level3["avg_return"]

    return {"win_rate": win_rate1, "avg_return": avg_return, "total": level1_total}


def get_pattern_type_performance(pattern_type: str) -> dict:
    """Aggregate performance for a pattern type across every timeframe and symbol it's ever
    fired on -- the broadest, fastest-to-mature read on "does this strategy actually work",
    used by signals/strategy_selector.py to decide whether the scanner should keep generating
    signals for it at all. Unlike get_pattern_weight(), this is a plain aggregate (no
    shrinkage toward a fallback) since it IS the fallback for finer grains -- shrinking it
    toward itself would be a no-op."""
    conn = get_db()
    rows = conn.execute(f"SELECT {_STATS_COLS} FROM pattern_stats WHERE pattern_type = ?", (pattern_type,)).fetchall()
    agg = _aggregate(rows)
    win_rate = agg["wins"] / agg["total"] if agg["total"] else DEFAULT_PRIOR_WIN_RATE
    return {"win_rate": win_rate, "avg_return": agg["avg_return"], "total": agg["total"]}


def get_pattern_symbol_performance(pattern_type: str, symbol: str) -> dict:
    """Aggregate performance for one specific (pattern_type, symbol) pair across every
    timeframe -- this is the actual "combo" signals/strategy_selector.py needs to judge.

    An earlier version of this module had a get_symbol_performance() that aggregated a
    symbol across EVERY pattern type. That was a real bug caught against live production
    data: double_bottom is a genuinely strong pattern (+0.278% avg_return over 209 real
    samples, positive on nearly every symbol individually), but it was getting blocked on
    every single symbol anyway, because the symbol-wide aggregate was dragged negative by
    OTHER pattern types sharing that symbol -- a good pattern was vetoed for a bad pattern's
    behavior. Judging the actual pair instead of pattern-OR-symbol independently fixes that:
    a good pattern stays tradeable on a symbol even if that symbol also hosts bad patterns,
    and vice versa."""
    conn = get_db()
    rows = conn.execute(
        f"SELECT {_STATS_COLS} FROM pattern_stats WHERE pattern_type = ? AND symbol = ?",
        (pattern_type, symbol),
    ).fetchall()
    agg = _aggregate(rows)
    win_rate = agg["wins"] / agg["total"] if agg["total"] else DEFAULT_PRIOR_WIN_RATE
    return {"win_rate": win_rate, "avg_return": agg["avg_return"], "total": agg["total"]}


def update_pattern_stats(pattern_type: str, timeframe: str, symbol: str, won: bool, return_pct: float) -> None:
    conn = get_db()
    row = conn.execute(
        "SELECT wins, losses, total, avg_return FROM pattern_stats "
        "WHERE pattern_type = ? AND timeframe = ? AND symbol = ?",
        (pattern_type, timeframe, symbol),
    ).fetchone()

    if row is None:
        wins, losses, total, avg_return = 0, 0, 0, 0.0
    else:
        wins, losses, total, avg_return = row["wins"], row["losses"], row["total"], row["avg_return"]

    new_total = total + 1
    new_wins = wins + (1 if won else 0)
    new_losses = losses + (0 if won else 1)
    new_avg_return = (avg_return * total + return_pct) / new_total
    new_win_rate = new_wins / new_total
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO pattern_stats (pattern_type, timeframe, symbol, wins, losses, total, win_rate, avg_return, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pattern_type, timeframe, symbol) DO UPDATE SET
            wins=excluded.wins, losses=excluded.losses, total=excluded.total,
            win_rate=excluded.win_rate, avg_return=excluded.avg_return, updated_at=excluded.updated_at
        """,
        (pattern_type, timeframe, symbol, new_wins, new_losses, new_total, new_win_rate, new_avg_return, now),
    )
    conn.commit()
