"""Chart (multi-bar) pattern detection: swing-point extraction + geometric rules for
head & shoulders, double/triple top & bottom, triangles, wedges, flags, pennants,
rectangles, channels, broadening formations, rounding formations, cup & handle, and
bull/bear traps -- plus RSI/MACD divergence.

Every detector returns matches only when the pattern's breakout/trigger condition falls
on the *last* bar of the given DataFrame -- callers pass in `df.iloc[:i+1]` to check
whether a pattern triggers exactly at bar `i` (used both for live scanning and for
`patterns/history.py`'s historical scan of the same detectors).

Each match carries a `status`: "confirmed" once price has actually broken out, or
"forming" when the geometry is present but price is still inside the pattern's
boundaries -- in which case `progress` (0..1, how far price has travelled from the
invalidation edge toward the breakout edge) and `missing` (a human-readable description
of what's still needed) are also set. A forming match is only returned while the pattern
hasn't already been invalidated by price closing the wrong way. Matches also carry
`key_points`/`trend_lines` -- the actual peaks/troughs/necklines/trendlines that justify
the pattern -- so the dashboard can draw the pattern's real shape on the chart.
"""

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema


def _dedupe(idxs: np.ndarray, min_gap: int) -> np.ndarray:
    if len(idxs) == 0:
        return idxs
    out = [idxs[0]]
    for x in idxs[1:]:
        if x - out[-1] >= min_gap:
            out.append(x)
    return np.array(out)


def find_swings(df: pd.DataFrame, order: int = 5):
    highs, lows = df["high"].values, df["low"].values
    peak_idx = _dedupe(argrelextrema(highs, np.greater_equal, order=order)[0], order)
    trough_idx = _dedupe(argrelextrema(lows, np.less_equal, order=order)[0], order)
    return peak_idx, trough_idx


def _fresh_cross_below(df: pd.DataFrame, level: float) -> bool:
    if len(df) < 2:
        return False
    return df["close"].iloc[-2] >= level and df["close"].iloc[-1] < level


def _fresh_cross_above(df: pd.DataFrame, level: float) -> bool:
    if len(df) < 2:
        return False
    return df["close"].iloc[-2] <= level and df["close"].iloc[-1] > level


def _pt(idx, price, label: str = "") -> dict:
    return {"index": int(idx), "price": float(price), "label": label}


def _line(points, label: str = "") -> dict:
    return {"points": [(int(i), float(p)) for i, p in points], "label": label}


def _not_invalidated(direction: str, last_close: float, invalidation_price: float) -> bool:
    return last_close < invalidation_price if direction == "short" else last_close > invalidation_price


def _progress(last_close: float, breakout_level: float, invalidation_price: float) -> float:
    total = abs(breakout_level - invalidation_price)
    if total <= 0:
        return 0.5
    traveled = abs(last_close - invalidation_price)
    return max(0.0, min(1.0, traveled / total))


def _finalize(pattern, direction, invalidation_price, target_price, strength, key_points, trend_lines,
              df, breakout_level):
    """Shared confirmed/forming/invalidated state machine for single-direction patterns."""
    last_close = float(df["close"].iloc[-1])
    if direction == "long":
        confirmed = _fresh_cross_above(df, breakout_level)
        already_broken = last_close > breakout_level
    else:
        confirmed = _fresh_cross_below(df, breakout_level)
        already_broken = last_close < breakout_level

    base = {
        "pattern": pattern, "direction": direction, "invalidation_price": invalidation_price,
        "target_price": target_price, "strength": strength, "key_points": key_points, "trend_lines": trend_lines,
    }
    if confirmed:
        base["status"] = "confirmed"
        return base
    if already_broken:
        # Broke out on some earlier bar, not this one -- stale by the time we're looking at it.
        return None
    if not _not_invalidated(direction, last_close, invalidation_price):
        return None
    base["status"] = "forming"
    base["progress"] = _progress(last_close, breakout_level, invalidation_price)
    base["breakout_level"] = breakout_level
    verb = "above" if direction == "long" else "below"
    base["missing"] = f"Needs a close {verb} {breakout_level:,.4f} to confirm (now {last_close:,.4f})"
    return base


def _finalize_bilateral(pattern_up, pattern_down, resistance, support, height, strength, key_points, trend_lines, df):
    """Shared state machine for patterns that can break either way (triangles, rectangles,
    channels, broadening formations) -- while forming, leans toward whichever boundary
    price currently sits closer to."""
    last_close = float(df["close"].iloc[-1])
    if _fresh_cross_above(df, resistance):
        return {"pattern": pattern_up, "direction": "long", "invalidation_price": support * 0.99,
                "target_price": resistance + height, "strength": strength, "key_points": key_points,
                "trend_lines": trend_lines, "status": "confirmed"}
    if _fresh_cross_below(df, support):
        return {"pattern": pattern_down, "direction": "short", "invalidation_price": resistance * 1.01,
                "target_price": support - height, "strength": strength, "key_points": key_points,
                "trend_lines": trend_lines, "status": "confirmed"}

    mid = (resistance + support) / 2
    leaning_long = last_close >= mid
    if leaning_long:
        pattern, direction = pattern_up, "long"
        invalidation_price, target_price, breakout_level = support * 0.99, resistance + height, resistance
    else:
        pattern, direction = pattern_down, "short"
        invalidation_price, target_price, breakout_level = resistance * 1.01, support - height, support
    already_broken = last_close > resistance if leaning_long else last_close < support
    if already_broken or not _not_invalidated(direction, last_close, invalidation_price):
        return None
    verb = "above" if direction == "long" else "below"
    return {
        "pattern": pattern, "direction": direction, "invalidation_price": invalidation_price,
        "target_price": target_price, "strength": strength, "key_points": key_points, "trend_lines": trend_lines,
        "status": "forming", "progress": _progress(last_close, breakout_level, invalidation_price),
        "breakout_level": breakout_level,
        "missing": f"Needs a close {verb} {breakout_level:,.4f} to confirm (now {last_close:,.4f}, leaning {'long' if leaning_long else 'short'})",
    }


# ---------------------------------------------------------------------------
# Double / triple top & bottom
# ---------------------------------------------------------------------------

def detect_double_top(df: pd.DataFrame, peaks, troughs, tolerance: float = 0.025):
    if len(peaks) < 2 or len(troughs) < 1:
        return None
    p1, p2 = peaks[-2], peaks[-1]
    mid_troughs = troughs[(troughs > p1) & (troughs < p2)]
    if len(mid_troughs) == 0:
        return None
    trough = mid_troughs[-1]
    h1, h2 = df["high"].iloc[p1], df["high"].iloc[p2]
    if abs(h1 - h2) / max(h1, h2) > tolerance:
        return None
    neckline = df["low"].iloc[trough]
    peak_avg = (h1 + h2) / 2
    last_idx = len(df) - 1
    return _finalize(
        "double_top", "short", peak_avg * 1.01, neckline - (peak_avg - neckline),
        max(0.3, 1 - abs(h1 - h2) / max(h1, h2) / tolerance),
        [_pt(p1, h1, "Peak 1"), _pt(trough, neckline, "Trough"), _pt(p2, h2, "Peak 2")],
        [_line([(p1, h1), (p2, h2)], "Resistance"), _line([(trough, neckline), (last_idx, neckline)], "Neckline")],
        df, neckline,
    )


def detect_double_bottom(df: pd.DataFrame, peaks, troughs, tolerance: float = 0.025):
    if len(troughs) < 2 or len(peaks) < 1:
        return None
    t1, t2 = troughs[-2], troughs[-1]
    mid_peaks = peaks[(peaks > t1) & (peaks < t2)]
    if len(mid_peaks) == 0:
        return None
    peak = mid_peaks[-1]
    l1, l2 = df["low"].iloc[t1], df["low"].iloc[t2]
    if abs(l1 - l2) / max(l1, l2) > tolerance:
        return None
    neckline = df["high"].iloc[peak]
    trough_avg = (l1 + l2) / 2
    last_idx = len(df) - 1
    return _finalize(
        "double_bottom", "long", trough_avg * 0.99, neckline + (neckline - trough_avg),
        max(0.3, 1 - abs(l1 - l2) / max(l1, l2) / tolerance),
        [_pt(t1, l1, "Trough 1"), _pt(peak, neckline, "Peak"), _pt(t2, l2, "Trough 2")],
        [_line([(t1, l1), (t2, l2)], "Support"), _line([(peak, neckline), (last_idx, neckline)], "Neckline")],
        df, neckline,
    )


def detect_triple_top(df: pd.DataFrame, peaks, troughs, tolerance: float = 0.025):
    if len(peaks) < 3 or len(troughs) < 2:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    t1c = troughs[(troughs > p1) & (troughs < p2)]
    t2c = troughs[(troughs > p2) & (troughs < p3)]
    if len(t1c) == 0 or len(t2c) == 0:
        return None
    t1, t2 = t1c[-1], t2c[-1]
    heights = [df["high"].iloc[p1], df["high"].iloc[p2], df["high"].iloc[p3]]
    if (max(heights) - min(heights)) / max(heights) > tolerance:
        return None
    neckline = min(df["low"].iloc[t1], df["low"].iloc[t2])
    peak_avg = sum(heights) / 3
    last_idx = len(df) - 1
    return _finalize(
        "triple_top", "short", peak_avg * 1.01, neckline - (peak_avg - neckline),
        max(0.3, 1 - (max(heights) - min(heights)) / max(heights) / tolerance),
        [_pt(p1, heights[0], "Peak 1"), _pt(t1, df["low"].iloc[t1], "Trough 1"), _pt(p2, heights[1], "Peak 2"),
         _pt(t2, df["low"].iloc[t2], "Trough 2"), _pt(p3, heights[2], "Peak 3")],
        [_line([(p1, heights[0]), (p2, heights[1]), (p3, heights[2])], "Resistance"),
         _line([(t1, df["low"].iloc[t1]), (t2, df["low"].iloc[t2]), (last_idx, neckline)], "Neckline")],
        df, neckline,
    )


def detect_triple_bottom(df: pd.DataFrame, peaks, troughs, tolerance: float = 0.025):
    if len(troughs) < 3 or len(peaks) < 2:
        return None
    t1, t2, t3 = troughs[-3], troughs[-2], troughs[-1]
    p1c = peaks[(peaks > t1) & (peaks < t2)]
    p2c = peaks[(peaks > t2) & (peaks < t3)]
    if len(p1c) == 0 or len(p2c) == 0:
        return None
    p1, p2 = p1c[-1], p2c[-1]
    lows = [df["low"].iloc[t1], df["low"].iloc[t2], df["low"].iloc[t3]]
    if (max(lows) - min(lows)) / max(lows) > tolerance:
        return None
    neckline = max(df["high"].iloc[p1], df["high"].iloc[p2])
    trough_avg = sum(lows) / 3
    last_idx = len(df) - 1
    return _finalize(
        "triple_bottom", "long", trough_avg * 0.99, neckline + (neckline - trough_avg),
        max(0.3, 1 - (max(lows) - min(lows)) / max(lows) / tolerance),
        [_pt(t1, lows[0], "Trough 1"), _pt(p1, df["high"].iloc[p1], "Peak 1"), _pt(t2, lows[1], "Trough 2"),
         _pt(p2, df["high"].iloc[p2], "Peak 2"), _pt(t3, lows[2], "Trough 3")],
        [_line([(t1, lows[0]), (t2, lows[1]), (t3, lows[2])], "Support"),
         _line([(p1, df["high"].iloc[p1]), (p2, df["high"].iloc[p2]), (last_idx, neckline)], "Neckline")],
        df, neckline,
    )


# ---------------------------------------------------------------------------
# Head & shoulders
# ---------------------------------------------------------------------------

def detect_head_and_shoulders(df: pd.DataFrame, peaks, troughs, tolerance: float = 0.05):
    if len(peaks) < 3 or len(troughs) < 2:
        return None
    ls, head, rs = peaks[-3], peaks[-2], peaks[-1]
    h_ls, h_head, h_rs = df["high"].iloc[ls], df["high"].iloc[head], df["high"].iloc[rs]
    if not (h_head > h_ls and h_head > h_rs):
        return None
    if abs(h_ls - h_rs) / max(h_ls, h_rs) > tolerance:
        return None
    t1 = troughs[(troughs > ls) & (troughs < head)]
    t2 = troughs[(troughs > head) & (troughs < rs)]
    if len(t1) == 0 or len(t2) == 0:
        return None
    t1_idx, t2_idx = t1[-1], t2[-1]
    low_t1, low_t2 = df["low"].iloc[t1_idx], df["low"].iloc[t2_idx]
    neckline = (low_t1 + low_t2) / 2
    last_idx = len(df) - 1
    return _finalize(
        "head_and_shoulders", "short", h_head * 1.01, neckline - (h_head - neckline),
        max(0.3, 1 - abs(h_ls - h_rs) / max(h_ls, h_rs) / tolerance),
        [_pt(ls, h_ls, "Left shoulder"), _pt(t1_idx, low_t1, "Trough 1"), _pt(head, h_head, "Head"),
         _pt(t2_idx, low_t2, "Trough 2"), _pt(rs, h_rs, "Right shoulder")],
        [_line([(ls, h_ls), (head, h_head), (rs, h_rs)], "Shoulders/Head"),
         _line([(t1_idx, low_t1), (t2_idx, low_t2), (last_idx, neckline)], "Neckline")],
        df, neckline,
    )


def detect_inverse_head_and_shoulders(df: pd.DataFrame, peaks, troughs, tolerance: float = 0.05):
    if len(troughs) < 3 or len(peaks) < 2:
        return None
    ls, head, rs = troughs[-3], troughs[-2], troughs[-1]
    l_ls, l_head, l_rs = df["low"].iloc[ls], df["low"].iloc[head], df["low"].iloc[rs]
    if not (l_head < l_ls and l_head < l_rs):
        return None
    if abs(l_ls - l_rs) / max(l_ls, l_rs) > tolerance:
        return None
    p1 = peaks[(peaks > ls) & (peaks < head)]
    p2 = peaks[(peaks > head) & (peaks < rs)]
    if len(p1) == 0 or len(p2) == 0:
        return None
    p1_idx, p2_idx = p1[-1], p2[-1]
    high_p1, high_p2 = df["high"].iloc[p1_idx], df["high"].iloc[p2_idx]
    neckline = (high_p1 + high_p2) / 2
    last_idx = len(df) - 1
    return _finalize(
        "inverse_head_and_shoulders", "long", l_head * 0.99, neckline + (neckline - l_head),
        max(0.3, 1 - abs(l_ls - l_rs) / max(l_ls, l_rs) / tolerance),
        [_pt(ls, l_ls, "Left shoulder"), _pt(p1_idx, high_p1, "Peak 1"), _pt(head, l_head, "Head"),
         _pt(p2_idx, high_p2, "Peak 2"), _pt(rs, l_rs, "Right shoulder")],
        [_line([(ls, l_ls), (head, l_head), (rs, l_rs)], "Shoulders/Head"),
         _line([(p1_idx, high_p1), (p2_idx, high_p2), (last_idx, neckline)], "Neckline")],
        df, neckline,
    )


# ---------------------------------------------------------------------------
# Triangles, wedges, rectangles, channels, broadening formations
# ---------------------------------------------------------------------------

def _slope(idxs, values) -> float:
    if len(idxs) < 2:
        return 0.0
    return float(np.polyfit(idxs, values, 1)[0])


def _bilateral_lines(df, p, t, res_slope, sup_slope, resistance_now, support_now, last_idx):
    res_start = resistance_now + res_slope * (p[0] - last_idx)
    sup_start = support_now + sup_slope * (t[0] - last_idx)
    key_points = [_pt(idx, df["high"].iloc[idx], "Peak") for idx in p] + [_pt(idx, df["low"].iloc[idx], "Trough") for idx in t]
    trend_lines = [
        _line([(p[0], res_start), (last_idx, resistance_now)], "Resistance"),
        _line([(t[0], sup_start), (last_idx, support_now)], "Support"),
    ]
    return key_points, trend_lines


def detect_triangle(df: pd.DataFrame, peaks, troughs, lookback: int = 40, min_points: int = 2):
    start = max(0, len(df) - lookback)
    p = peaks[peaks >= start][-4:]
    t = troughs[troughs >= start][-4:]
    if len(p) < min_points or len(t) < min_points:
        return None

    last_idx = len(df) - 1
    res_slope = _slope(p, df["high"].iloc[p].values)
    sup_slope = _slope(t, df["low"].iloc[t].values)
    resistance_now = df["high"].iloc[p[-1]] + res_slope * (last_idx - p[-1])
    support_now = df["low"].iloc[t[-1]] + sup_slope * (last_idx - t[-1])
    height = df["high"].iloc[p].max() - df["low"].iloc[t].min()
    flat_thresh = 0.15 * height / max(lookback, 1)

    flat_res = abs(res_slope) < flat_thresh
    flat_sup = abs(sup_slope) < flat_thresh
    rising_sup = sup_slope > flat_thresh
    falling_res = res_slope < -flat_thresh

    if flat_res and rising_sup:
        up_name, down_name = "ascending_triangle", "ascending_triangle"
    elif flat_sup and falling_res:
        up_name, down_name = "descending_triangle", "descending_triangle"
    elif rising_sup and falling_res:
        up_name, down_name = "symmetrical_triangle", "symmetrical_triangle"
    else:
        return None

    key_points, trend_lines = _bilateral_lines(df, p, t, res_slope, sup_slope, resistance_now, support_now, last_idx)
    return _finalize_bilateral(up_name, down_name, resistance_now, support_now, height, 0.5, key_points, trend_lines, df)


def detect_wedge(df: pd.DataFrame, peaks, troughs, lookback: int = 40, min_points: int = 2):
    start = max(0, len(df) - lookback)
    p = peaks[peaks >= start][-4:]
    t = troughs[troughs >= start][-4:]
    if len(p) < min_points or len(t) < min_points:
        return None

    last_idx = len(df) - 1
    res_slope = _slope(p, df["high"].iloc[p].values)
    sup_slope = _slope(t, df["low"].iloc[t].values)
    resistance_now = df["high"].iloc[p[-1]] + res_slope * (last_idx - p[-1])
    support_now = df["low"].iloc[t[-1]] + sup_slope * (last_idx - t[-1])
    height = df["high"].iloc[p].max() - df["low"].iloc[t].min()

    key_points, trend_lines = _bilateral_lines(df, p, t, res_slope, sup_slope, resistance_now, support_now, last_idx)

    if res_slope > 0 and sup_slope > 0 and sup_slope > res_slope:
        # Rising wedge: converging upward -- textbook expectation is a bearish break of support.
        return _finalize("rising_wedge", "short", resistance_now * 1.01, support_now - height, 0.5,
                          key_points, trend_lines, df, support_now)
    if res_slope < 0 and sup_slope < 0 and res_slope < sup_slope:
        # Falling wedge: converging downward -- textbook expectation is a bullish break of resistance.
        return _finalize("falling_wedge", "long", support_now * 0.99, resistance_now + height, 0.5,
                          key_points, trend_lines, df, resistance_now)
    return None


def detect_rectangle(df: pd.DataFrame, peaks, troughs, lookback: int = 50, min_points: int = 2, tolerance: float = 0.02):
    start = max(0, len(df) - lookback)
    p = peaks[peaks >= start][-4:]
    t = troughs[troughs >= start][-4:]
    if len(p) < min_points or len(t) < min_points:
        return None

    p_highs, t_lows = df["high"].iloc[p], df["low"].iloc[t]
    resistance, support = float(p_highs.mean()), float(t_lows.mean())
    if support <= 0 or resistance <= support:
        return None
    if p_highs.std() / resistance > tolerance or t_lows.std() / support > tolerance:
        return None

    height = resistance - support
    last_idx = len(df) - 1
    span_start = min(p[0], t[0])
    key_points = [_pt(idx, df["high"].iloc[idx], "Resistance touch") for idx in p] + \
        [_pt(idx, df["low"].iloc[idx], "Support touch") for idx in t]
    trend_lines = [
        _line([(span_start, resistance), (last_idx, resistance)], "Resistance"),
        _line([(span_start, support), (last_idx, support)], "Support"),
    ]
    return _finalize_bilateral("rectangle", "rectangle", resistance, support, height, 0.5, key_points, trend_lines, df)


def detect_channel(df: pd.DataFrame, peaks, troughs, lookback: int = 60, min_points: int = 2, tolerance: float = 0.3):
    start = max(0, len(df) - lookback)
    p = peaks[peaks >= start][-4:]
    t = troughs[troughs >= start][-4:]
    if len(p) < min_points or len(t) < min_points:
        return None

    last_idx = len(df) - 1
    res_slope = _slope(p, df["high"].iloc[p].values)
    sup_slope = _slope(t, df["low"].iloc[t].values)
    if res_slope == 0 or sup_slope == 0 or res_slope * sup_slope <= 0:
        return None
    if abs(res_slope - sup_slope) / max(abs(res_slope), abs(sup_slope)) > tolerance:
        return None

    resistance_now = df["high"].iloc[p[-1]] + res_slope * (last_idx - p[-1])
    support_now = df["low"].iloc[t[-1]] + sup_slope * (last_idx - t[-1])
    height = resistance_now - support_now
    if height <= 0:
        return None

    name = "ascending_channel" if res_slope > 0 else "descending_channel"
    key_points, trend_lines = _bilateral_lines(df, p, t, res_slope, sup_slope, resistance_now, support_now, last_idx)
    return _finalize_bilateral(name, name, resistance_now, support_now, height, 0.45, key_points, trend_lines, df)


def detect_broadening_formation(df: pd.DataFrame, peaks, troughs, lookback: int = 50, min_points: int = 2):
    start = max(0, len(df) - lookback)
    p = peaks[peaks >= start][-4:]
    t = troughs[troughs >= start][-4:]
    if len(p) < min_points or len(t) < min_points:
        return None

    last_idx = len(df) - 1
    res_slope = _slope(p, df["high"].iloc[p].values)
    sup_slope = _slope(t, df["low"].iloc[t].values)
    if not (res_slope > 0 and sup_slope < 0):
        return None

    resistance_now = df["high"].iloc[p[-1]] + res_slope * (last_idx - p[-1])
    support_now = df["low"].iloc[t[-1]] + sup_slope * (last_idx - t[-1])
    height = resistance_now - support_now
    if height <= 0:
        return None

    key_points, trend_lines = _bilateral_lines(df, p, t, res_slope, sup_slope, resistance_now, support_now, last_idx)
    return _finalize_bilateral("broadening_formation", "broadening_formation", resistance_now, support_now, height, 0.4, key_points, trend_lines, df)


# ---------------------------------------------------------------------------
# Flags, pennants, cup & handle
# ---------------------------------------------------------------------------

def detect_flag(df: pd.DataFrame, pole_window: int = 15, flag_window: int = 10, min_pole_pct: float = 0.05):
    if len(df) < pole_window + flag_window + 1:
        return None
    pole = df.iloc[-(pole_window + flag_window):-flag_window]
    flag = df.iloc[-flag_window:]
    last_idx = len(df) - 1
    pole_start_idx = last_idx - pole_window - flag_window + 1
    flag_start_idx = last_idx - flag_window + 1

    pole_return = (pole["close"].iloc[-1] - pole["close"].iloc[0]) / pole["close"].iloc[0]
    flag_slope = _slope(np.arange(len(flag)), flag["close"].values)
    flag_range = flag["high"].max() - flag["low"].min()
    pole_height = abs(pole["close"].iloc[-1] - pole["close"].iloc[0])
    tight = flag_range < 0.6 * pole_height
    if not tight:
        return None

    pole_line = _line([(pole_start_idx, pole["close"].iloc[0]), (flag_start_idx, pole["close"].iloc[-1])], "Flagpole")
    pole_points = [_pt(pole_start_idx, pole["close"].iloc[0], "Pole start"), _pt(flag_start_idx, pole["close"].iloc[-1], "Pole end")]

    if pole_return > min_pole_pct and flag_slope <= 0:
        breakout_level = flag["high"].max()
        flag_low = flag["low"].min()
        trend_lines = [pole_line, _line([(flag_start_idx, breakout_level), (last_idx, breakout_level)], "Flag top"),
                       _line([(flag_start_idx, flag_low), (last_idx, flag_low)], "Flag bottom")]
        return _finalize("bull_flag", "long", flag_low * 0.99, breakout_level + pole_height, 0.5,
                          pole_points, trend_lines, df, breakout_level)
    if pole_return < -min_pole_pct and flag_slope >= 0:
        breakout_level = flag["low"].min()
        flag_high = flag["high"].max()
        trend_lines = [pole_line, _line([(flag_start_idx, flag_high), (last_idx, flag_high)], "Flag top"),
                       _line([(flag_start_idx, breakout_level), (last_idx, breakout_level)], "Flag bottom")]
        return _finalize("bear_flag", "short", flag_high * 1.01, breakout_level - pole_height, 0.5,
                          pole_points, trend_lines, df, breakout_level)
    return None


def detect_pennant(df: pd.DataFrame, pole_window: int = 15, flag_window: int = 10, min_pole_pct: float = 0.05):
    if len(df) < pole_window + flag_window + 1:
        return None
    pole = df.iloc[-(pole_window + flag_window):-flag_window]
    flag = df.iloc[-flag_window:]
    last_idx = len(df) - 1
    pole_start_idx = last_idx - pole_window - flag_window + 1
    flag_start_idx = last_idx - flag_window + 1

    pole_return = (pole["close"].iloc[-1] - pole["close"].iloc[0]) / pole["close"].iloc[0]
    pole_height = abs(pole["close"].iloc[-1] - pole["close"].iloc[0])
    idxs = np.arange(len(flag))
    high_slope = _slope(idxs, flag["high"].values)
    low_slope = _slope(idxs, flag["low"].values)
    if not (high_slope < 0 and low_slope > 0):
        return None

    apex_high, apex_low = flag["high"].iloc[-1], flag["low"].iloc[-1]
    pole_line = _line([(pole_start_idx, pole["close"].iloc[0]), (flag_start_idx, pole["close"].iloc[-1])], "Flagpole")
    pole_points = [_pt(pole_start_idx, pole["close"].iloc[0], "Pole start"), _pt(flag_start_idx, pole["close"].iloc[-1], "Pole end")]
    trend_lines = [pole_line, _line([(flag_start_idx, flag["high"].iloc[0]), (last_idx, apex_high)], "Upper"),
                   _line([(flag_start_idx, flag["low"].iloc[0]), (last_idx, apex_low)], "Lower")]

    if pole_return > min_pole_pct:
        return _finalize("pennant", "long", apex_low * 0.99, apex_high + pole_height, 0.5, pole_points, trend_lines, df, apex_high)
    if pole_return < -min_pole_pct:
        return _finalize("pennant", "short", apex_high * 1.01, apex_low - pole_height, 0.5, pole_points, trend_lines, df, apex_low)
    return None


def detect_cup_and_handle(df: pd.DataFrame, troughs, lookback: int = 120, handle_window: int = 10, tolerance: float = 0.04):
    start = max(0, len(df) - lookback)
    last_idx = len(df) - 1
    handle_start = last_idx - handle_window + 1
    candidates = troughs[(troughs >= start) & (troughs < handle_start)]
    if len(candidates) == 0:
        return None

    cup_bottom_idx = int(candidates[np.argmin(df["low"].iloc[candidates].values)])
    left = df.iloc[start:cup_bottom_idx + 1]
    right = df.iloc[cup_bottom_idx:handle_start]
    if len(left) < 5 or len(right) < 5:
        return None

    left_rim_idx = int(left["high"].idxmax())
    right_rim_idx = int(right["high"].idxmax())
    if right_rim_idx <= cup_bottom_idx:
        return None
    left_rim, right_rim = df["high"].iloc[left_rim_idx], df["high"].iloc[right_rim_idx]
    if abs(left_rim - right_rim) / max(left_rim, right_rim) > tolerance:
        return None

    cup_depth = right_rim - df["low"].iloc[cup_bottom_idx]
    if cup_depth <= 0:
        return None

    handle = df.iloc[handle_start:]
    handle_low = handle["low"].min()
    if (right_rim - handle_low) > 0.5 * cup_depth:
        return None

    breakout_level = max(left_rim, right_rim)
    key_points = [
        _pt(left_rim_idx, left_rim, "Left rim"), _pt(cup_bottom_idx, df["low"].iloc[cup_bottom_idx], "Cup bottom"),
        _pt(right_rim_idx, right_rim, "Right rim"), _pt(handle_start + int(handle["low"].values.argmin()), handle_low, "Handle low"),
    ]
    trend_lines = [
        _line([(left_rim_idx, left_rim), (cup_bottom_idx, df["low"].iloc[cup_bottom_idx]), (right_rim_idx, right_rim)], "Cup"),
        _line([(right_rim_idx, right_rim), (last_idx, breakout_level)], "Rim"),
    ]
    return _finalize("cup_and_handle", "long", handle_low * 0.99, breakout_level + cup_depth, 0.5,
                      key_points, trend_lines, df, breakout_level)


# ---------------------------------------------------------------------------
# Rounding top / bottom
# ---------------------------------------------------------------------------

def _rounding(df: pd.DataFrame, lookback: int, bullish: bool):
    if len(df) < lookback:
        return None
    start = len(df) - lookback
    window = df.iloc[start:]
    mid = len(window) // 2
    first_half, second_half = window.iloc[:mid], window.iloc[mid:]
    slope1 = _slope(np.arange(len(first_half)), first_half["close"].values)
    slope2 = _slope(np.arange(len(second_half)), second_half["close"].values)
    last_idx = len(df) - 1

    if bullish:
        if not (slope1 < 0 and slope2 > 0):
            return None
        extreme_local = int(window["low"].values.argmin())
        extreme_idx = start + extreme_local
        extreme_price = float(df["low"].iloc[extreme_idx])
        breakout_level = max(window["close"].iloc[0], window["high"].iloc[:5].max())
        depth = breakout_level - extreme_price
        if depth <= 0:
            return None
        pattern, direction, invalid, target = "rounding_bottom", "long", extreme_price * 0.99, breakout_level + depth
    else:
        if not (slope1 > 0 and slope2 < 0):
            return None
        extreme_local = int(window["high"].values.argmax())
        extreme_idx = start + extreme_local
        extreme_price = float(df["high"].iloc[extreme_idx])
        breakout_level = min(window["close"].iloc[0], window["low"].iloc[:5].min())
        depth = extreme_price - breakout_level
        if depth <= 0:
            return None
        pattern, direction, invalid, target = "rounding_top", "short", extreme_price * 1.01, breakout_level - depth

    key_points = [_pt(start, window["close"].iloc[0], "Start"), _pt(extreme_idx, extreme_price, "Extreme"),
                  _pt(last_idx, window["close"].iloc[-1], "Now")]
    trend_lines = [_line([(start, window["close"].iloc[0]), (extreme_idx, extreme_price), (last_idx, breakout_level)], "Curve")]
    return _finalize(pattern, direction, invalid, target, 0.4, key_points, trend_lines, df, breakout_level)


def detect_rounding_bottom(df: pd.DataFrame, lookback: int = 80):
    return _rounding(df, lookback, bullish=True)


def detect_rounding_top(df: pd.DataFrame, lookback: int = 80):
    return _rounding(df, lookback, bullish=False)


# ---------------------------------------------------------------------------
# Bull / bear traps (failed breakouts)
# ---------------------------------------------------------------------------

def detect_bull_trap(df: pd.DataFrame, peaks, lookback: int = 40, confirm_window: int = 6):
    start = max(0, len(df) - lookback)
    last_idx = len(df) - 1
    candidates = peaks[(peaks >= start) & (peaks < last_idx - 1)]
    if len(candidates) == 0:
        return None
    resistance_idx = int(candidates[-1])
    resistance = float(df["high"].iloc[resistance_idx])
    after = df.iloc[resistance_idx + 1:last_idx]
    if after.empty or not (after["high"] > resistance).any():
        return None
    breakout_positions = after.index[after["high"] > resistance]
    most_recent_breakout = int(breakout_positions.max())
    if last_idx - most_recent_breakout > confirm_window:
        return None
    if df["close"].iloc[last_idx] >= resistance:
        return None
    high_after = float(after["high"].max())
    entry_now = float(df["close"].iloc[last_idx])
    overshoot = high_after - resistance
    # Target anchored to the actual entry price, not to the stale resistance level -- by the
    # time this pattern confirms, price has already reverted partway back through resistance,
    # so a target measured FROM resistance can end up barely past (or even behind) entry,
    # which produced a catastrophic ~0.08:1 reward:risk in production (see
    # core/config.py's min_reward_risk_ratio docstring). Measuring the same overshoot distance
    # from entry instead keeps the target a real, tradeable distance away regardless of how
    # far price already reverted before the signal fired.
    return {
        "pattern": "bull_trap", "direction": "short", "status": "confirmed",
        "invalidation_price": high_after * 1.01, "target_price": entry_now - overshoot,
        "strength": 0.55,
        "key_points": [_pt(resistance_idx, resistance, "Resistance"), _pt(most_recent_breakout, high_after, "False breakout")],
        "trend_lines": [_line([(resistance_idx, resistance), (last_idx, resistance)], "Resistance")],
    }


def detect_bear_trap(df: pd.DataFrame, troughs, lookback: int = 40, confirm_window: int = 6):
    start = max(0, len(df) - lookback)
    last_idx = len(df) - 1
    candidates = troughs[(troughs >= start) & (troughs < last_idx - 1)]
    if len(candidates) == 0:
        return None
    support_idx = int(candidates[-1])
    support = float(df["low"].iloc[support_idx])
    after = df.iloc[support_idx + 1:last_idx]
    if after.empty or not (after["low"] < support).any():
        return None
    breakdown_positions = after.index[after["low"] < support]
    most_recent_breakdown = int(breakdown_positions.max())
    if last_idx - most_recent_breakdown > confirm_window:
        return None
    if df["close"].iloc[last_idx] <= support:
        return None
    low_after = float(after["low"].min())
    entry_now = float(df["close"].iloc[last_idx])
    undershoot = support - low_after
    # Same entry-anchored fix as detect_bull_trap -- see that function's comment.
    return {
        "pattern": "bear_trap", "direction": "long", "status": "confirmed",
        "invalidation_price": low_after * 0.99, "target_price": entry_now + undershoot,
        "strength": 0.55,
        "key_points": [_pt(support_idx, support, "Support"), _pt(most_recent_breakdown, low_after, "False breakdown")],
        "trend_lines": [_line([(support_idx, support), (last_idx, support)], "Support")],
    }


# ---------------------------------------------------------------------------
# RSI / MACD divergence
# ---------------------------------------------------------------------------

def _detect_divergence(df: pd.DataFrame, peaks, troughs, indicator_col: str, indicator_label: str,
                        lookback: int = 100, recency: int = 10):
    """Bullish: price makes a lower low while `indicator_col` makes a higher low (momentum
    fading on the way down). Bearish: price makes a higher high while the indicator makes a
    lower high. Requires a same-bar reversal confirmation to actually trigger."""
    last_idx = len(df) - 1
    start = max(0, len(df) - lookback)
    t = troughs[troughs >= start]
    p = peaks[peaks >= start]
    pattern_prefix = indicator_col

    if len(t) >= 2:
        t1, t2 = t[-2], t[-1]
        if last_idx - t2 <= recency and df["low"].iloc[t2] < df["low"].iloc[t1] and df[indicator_col].iloc[t2] > df[indicator_col].iloc[t1]:
            confirmed = df["close"].iloc[last_idx] > df["high"].iloc[t2] and df["close"].iloc[last_idx] > df["close"].iloc[last_idx - 1]
            if confirmed:
                low2 = df["low"].iloc[t2]
                move = df["close"].iloc[last_idx] - low2
                return {
                    "pattern": f"bullish_{pattern_prefix}_divergence", "direction": "long", "status": "confirmed",
                    "invalidation_price": low2 * 0.99, "target_price": df["close"].iloc[last_idx] + move,
                    "strength": 0.6,
                    "key_points": [_pt(t1, df["low"].iloc[t1], "Price low"), _pt(t2, low2, "Price lower low")],
                    "trend_lines": [_line([(t1, df["low"].iloc[t1]), (t2, low2)], f"{indicator_label} divergence")],
                    "indicator_line": {"row": indicator_col,
                                        "points": [(int(t1), float(df[indicator_col].iloc[t1])), (int(t2), float(df[indicator_col].iloc[t2]))]},
                }
    if len(p) >= 2:
        p1, p2 = p[-2], p[-1]
        if last_idx - p2 <= recency and df["high"].iloc[p2] > df["high"].iloc[p1] and df[indicator_col].iloc[p2] < df[indicator_col].iloc[p1]:
            confirmed = df["close"].iloc[last_idx] < df["low"].iloc[p2] and df["close"].iloc[last_idx] < df["close"].iloc[last_idx - 1]
            if confirmed:
                high2 = df["high"].iloc[p2]
                move = high2 - df["close"].iloc[last_idx]
                return {
                    "pattern": f"bearish_{pattern_prefix}_divergence", "direction": "short", "status": "confirmed",
                    "invalidation_price": high2 * 1.01, "target_price": df["close"].iloc[last_idx] - move,
                    "strength": 0.6,
                    "key_points": [_pt(p1, df["high"].iloc[p1], "Price high"), _pt(p2, high2, "Price higher high")],
                    "trend_lines": [_line([(p1, df["high"].iloc[p1]), (p2, high2)], f"{indicator_label} divergence")],
                    "indicator_line": {"row": indicator_col,
                                        "points": [(int(p1), float(df[indicator_col].iloc[p1])), (int(p2), float(df[indicator_col].iloc[p2]))]},
                }
    return None


def detect_rsi_divergence(df: pd.DataFrame, peaks, troughs):
    return _detect_divergence(df, peaks, troughs, "rsi", "RSI")


def detect_macd_divergence(df: pd.DataFrame, peaks, troughs):
    return _detect_divergence(df, peaks, troughs, "macd", "MACD")


# ---------------------------------------------------------------------------

def detect_all(df: pd.DataFrame, swing_order: int = 5, peaks: np.ndarray = None, troughs: np.ndarray = None) -> list[dict]:
    """Runs every chart-pattern detector against `df`, returning matches -- confirmed
    (breakout already happened) and forming (geometry present, no breakout yet) -- that
    are current as of its last bar.

    `peaks`/`troughs` can be precomputed swing points (see `find_swings`) sliced from a
    *longer* series than `df` -- callers doing a historical walk-forward scan can compute
    swings once on the full history instead of paying for scipy's extrema search again at
    every single bar, which is what made that scan slow. A peak/trough at index `k` is a
    legitimate match for a window ending at `len(df) - 1` as long as `k + swing_order <=
    len(df) - 1`, since that's exactly the neighborhood argrelextrema needed to confirm it
    -- so a full-history computation and a truncated one agree on every such point."""
    if len(df) < 30:
        return []
    if peaks is None or troughs is None:
        peaks, troughs = find_swings(df, order=swing_order)
    else:
        last_idx = len(df) - 1
        peaks = peaks[peaks + swing_order <= last_idx]
        troughs = troughs[troughs + swing_order <= last_idx]
    detectors = [
        lambda: detect_double_top(df, peaks, troughs),
        lambda: detect_double_bottom(df, peaks, troughs),
        lambda: detect_triple_top(df, peaks, troughs),
        lambda: detect_triple_bottom(df, peaks, troughs),
        lambda: detect_head_and_shoulders(df, peaks, troughs),
        lambda: detect_inverse_head_and_shoulders(df, peaks, troughs),
        lambda: detect_triangle(df, peaks, troughs),
        lambda: detect_wedge(df, peaks, troughs),
        lambda: detect_flag(df),
        lambda: detect_pennant(df),
        lambda: detect_rectangle(df, peaks, troughs),
        lambda: detect_channel(df, peaks, troughs),
        lambda: detect_broadening_formation(df, peaks, troughs),
        lambda: detect_cup_and_handle(df, troughs),
        lambda: detect_rounding_bottom(df),
        lambda: detect_rounding_top(df),
        lambda: detect_bull_trap(df, peaks),
        lambda: detect_bear_trap(df, troughs),
        lambda: detect_rsi_divergence(df, peaks, troughs),
        lambda: detect_macd_divergence(df, peaks, troughs),
    ]
    matches = []
    for detector in detectors:
        result = detector()
        if result:
            matches.append(result)
    return matches
