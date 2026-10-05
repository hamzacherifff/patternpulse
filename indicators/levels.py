"""Auto-detected support/resistance zones and Fibonacci retracement levels -- derived
purely from price history, used to annotate the Live Charts page."""

import numpy as np
import pandas as pd

from patterns.chart_patterns import find_swings

FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def find_support_resistance(df: pd.DataFrame, tolerance: float = 0.008, min_touches: int = 3, swing_order: int = 8) -> list[dict]:
    """Clusters swing highs/lows into horizontal zones touched at least `min_touches` times.
    Returns zones sorted by strength (touch count) descending, each with a price, touch
    count, the bar index of its most recent touch, and whether price is currently above
    (support) or below (resistance) it."""
    peaks, troughs = find_swings(df, order=swing_order)
    points = [(idx, df["high"].iloc[idx]) for idx in peaks] + [(idx, df["low"].iloc[idx]) for idx in troughs]
    if not points:
        return []
    points.sort(key=lambda p: p[1])

    # Compare each point to the cluster's running average (not just its last member) so a
    # long run of gradually-drifting swings doesn't chain into one meaningless mega-cluster.
    clusters = []
    current = [points[0]]
    for point in points[1:]:
        cluster_avg = np.mean([p[1] for p in current])
        if abs(point[1] - cluster_avg) / cluster_avg <= tolerance:
            current.append(point)
        else:
            clusters.append(current)
            current = [point]
    clusters.append(current)

    last_close = df["close"].iloc[-1]
    zones = []
    for cluster in clusters:
        if len(cluster) < min_touches:
            continue
        avg_price = float(np.mean([p[1] for p in cluster]))
        zones.append({
            "price": avg_price,
            "touches": len(cluster),
            "last_touch_index": max(p[0] for p in cluster),
            "kind": "support" if avg_price < last_close else "resistance",
        })
    zones.sort(key=lambda z: z["touches"], reverse=True)
    return zones


def find_fibonacci_levels(df: pd.DataFrame, lookback: int = 150) -> dict | None:
    """Finds the largest recent swing (by |high-low|) within the last `lookback` bars and
    returns standard retracement levels across it. Direction is inferred from which end of
    the swing happened more recently (so an uptrend swing retraces downward and vice versa)."""
    window = df.iloc[-lookback:] if len(df) > lookback else df
    if len(window) < 10:
        return None

    high_idx = window["high"].idxmax()
    low_idx = window["low"].idxmin()
    high_price = float(window["high"].loc[high_idx])
    low_price = float(window["low"].loc[low_idx])
    if high_price <= low_price:
        return None

    uptrend = high_idx > low_idx  # the high came after the low -> an upswing to retrace down from
    levels = []
    for ratio in FIB_RATIOS:
        price = high_price - (high_price - low_price) * ratio if uptrend else low_price + (high_price - low_price) * ratio
        levels.append({"ratio": ratio, "price": price})

    return {
        "direction": "up" if uptrend else "down",
        "start_index": int(low_idx if uptrend else high_idx),
        "end_index": int(high_idx if uptrend else low_idx),
        "levels": levels,
    }
