"""Hand-coded single/multi-candle pattern rules (no TA-Lib dependency).

Each detector inspects the candle(s) ending at index `i` of `df` and returns a
(pattern_name, direction) tuple if it matches, else None. `direction` is 'long' or 'short'.
"""

import pandas as pd


def _body(row) -> float:
    return abs(row["close"] - row["open"])


def _range(row) -> float:
    return max(row["high"] - row["low"], 1e-12)


def _upper_shadow(row) -> float:
    return row["high"] - max(row["open"], row["close"])


def _lower_shadow(row) -> float:
    return min(row["open"], row["close"]) - row["low"]


def _is_bullish(row) -> bool:
    return row["close"] > row["open"]


def _local_trend(df: pd.DataFrame, i: int, lookback: int = 5) -> str:
    start = max(0, i - lookback)
    if start >= i:
        return "flat"
    prior_avg = df["close"].iloc[start:i].mean()
    return "up" if df["close"].iloc[i] > prior_avg else "down"


def detect_doji(df: pd.DataFrame, i: int):
    row = df.iloc[i]
    if _body(row) <= 0.1 * _range(row):
        return ("doji", "long" if _local_trend(df, i) == "down" else "short")
    return None


def detect_hammer_family(df: pd.DataFrame, i: int):
    row = df.iloc[i]
    body, rng = _body(row), _range(row)
    lower, upper = _lower_shadow(row), _upper_shadow(row)
    if body > 0.35 * rng or lower < 2 * body or upper > 0.3 * body + 1e-9:
        return None
    trend = _local_trend(df, i)
    if trend == "down":
        return ("hammer", "long")
    return ("hanging_man", "short")


def detect_inverted_hammer_family(df: pd.DataFrame, i: int):
    row = df.iloc[i]
    body, rng = _body(row), _range(row)
    lower, upper = _lower_shadow(row), _upper_shadow(row)
    if body > 0.35 * rng or upper < 2 * body or lower > 0.3 * body + 1e-9:
        return None
    trend = _local_trend(df, i)
    if trend == "down":
        return ("inverted_hammer", "long")
    return ("shooting_star", "short")


def detect_engulfing(df: pd.DataFrame, i: int):
    if i < 1:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    prev_bull, cur_bull = _is_bullish(prev), _is_bullish(cur)
    if prev_bull == cur_bull:
        return None
    engulfs = cur["close"] > prev["open"] and cur["open"] < prev["close"] if cur_bull else \
        cur["close"] < prev["open"] and cur["open"] > prev["close"]
    if not engulfs:
        return None
    return ("bullish_engulfing", "long") if cur_bull else ("bearish_engulfing", "short")


def detect_piercing_dark_cloud(df: pd.DataFrame, i: int):
    if i < 1:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    prev_mid = (prev["open"] + prev["close"]) / 2
    if not _is_bullish(prev) and _is_bullish(cur):
        if cur["open"] < prev["close"] and prev_mid < cur["close"] < prev["open"]:
            return ("piercing_line", "long")
    if _is_bullish(prev) and not _is_bullish(cur):
        if cur["open"] > prev["close"] and prev["open"] < cur["close"] < prev_mid:
            return ("dark_cloud_cover", "short")
    return None


def detect_star(df: pd.DataFrame, i: int):
    if i < 2:
        return None
    first, mid, third = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    mid_small = _body(mid) < 0.4 * _body(first)
    if not _is_bullish(first) and mid_small and _is_bullish(third):
        if mid["high"] < first["close"] and third["close"] > (first["open"] + first["close"]) / 2:
            return ("morning_star", "long")
    if _is_bullish(first) and mid_small and not _is_bullish(third):
        if mid["low"] > first["close"] and third["close"] < (first["open"] + first["close"]) / 2:
            return ("evening_star", "short")
    return None


def detect_three_soldiers_crows(df: pd.DataFrame, i: int):
    if i < 2:
        return None
    a, b, c = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    if _is_bullish(a) and _is_bullish(b) and _is_bullish(c):
        if a["close"] < b["close"] < c["close"] and a["open"] < b["open"] < c["open"]:
            if b["open"] > a["open"] and c["open"] > b["open"]:
                return ("three_white_soldiers", "long")
    if not _is_bullish(a) and not _is_bullish(b) and not _is_bullish(c):
        if a["close"] > b["close"] > c["close"] and a["open"] > b["open"] > c["open"]:
            if b["open"] < a["open"] and c["open"] < b["open"]:
                return ("three_black_crows", "short")
    return None


def detect_harami(df: pd.DataFrame, i: int):
    if i < 1:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    prev_bull, cur_bull = _is_bullish(prev), _is_bullish(cur)
    if prev_bull == cur_bull or _body(prev) < 2 * _body(cur):
        return None
    contained = max(cur["open"], cur["close"]) < max(prev["open"], prev["close"]) and \
        min(cur["open"], cur["close"]) > min(prev["open"], prev["close"])
    if not contained:
        return None
    return ("bearish_harami", "short") if prev_bull else ("bullish_harami", "long")


def detect_marubozu(df: pd.DataFrame, i: int):
    row = df.iloc[i]
    body, rng = _body(row), _range(row)
    if body < 0.9 * rng:
        return None
    return ("bullish_marubozu", "long") if _is_bullish(row) else ("bearish_marubozu", "short")


def detect_spinning_top(df: pd.DataFrame, i: int):
    row = df.iloc[i]
    body, rng = _body(row), _range(row)
    upper, lower = _upper_shadow(row), _lower_shadow(row)
    if body > 0.3 * rng or upper < body or lower < body:
        return None
    return ("spinning_top", "long" if _local_trend(df, i) == "down" else "short")


def detect_tweezer(df: pd.DataFrame, i: int):
    if i < 1:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    high_match = abs(prev["high"] - cur["high"]) <= 0.15 * _range(cur)
    low_match = abs(prev["low"] - cur["low"]) <= 0.15 * _range(cur)
    trend = _local_trend(df, i)
    if high_match and trend == "up":
        return ("tweezer_top", "short")
    if low_match and trend == "down":
        return ("tweezer_bottom", "long")
    return None


def detect_dragonfly_gravestone(df: pd.DataFrame, i: int):
    row = df.iloc[i]
    body, rng = _body(row), _range(row)
    upper, lower = _upper_shadow(row), _lower_shadow(row)
    if body > 0.1 * rng:
        return None
    if lower > 0.6 * rng and upper < 0.15 * rng:
        return ("dragonfly_doji", "long")
    if upper > 0.6 * rng and lower < 0.15 * rng:
        return ("gravestone_doji", "short")
    return None


def detect_three_inside(df: pd.DataFrame, i: int):
    if i < 2:
        return None
    a, b, c = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    a_bull, b_bull = _is_bullish(a), _is_bullish(b)
    if a_bull == b_bull or _body(a) < 2 * _body(b):
        return None
    contained = max(b["open"], b["close"]) < max(a["open"], a["close"]) and \
        min(b["open"], b["close"]) > min(a["open"], a["close"])
    if not contained:
        return None
    if not a_bull and _is_bullish(c) and c["close"] > a["open"]:
        return ("three_inside_up", "long")
    if a_bull and not _is_bullish(c) and c["close"] < a["open"]:
        return ("three_inside_down", "short")
    return None


def detect_three_outside(df: pd.DataFrame, i: int):
    if i < 2:
        return None
    a, b, c = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    a_bull, b_bull = _is_bullish(a), _is_bullish(b)
    if a_bull == b_bull:
        return None
    engulfs = (b["close"] > a["open"] and b["open"] < a["close"]) if b_bull else \
        (b["close"] < a["open"] and b["open"] > a["close"])
    if not engulfs:
        return None
    if b_bull and _is_bullish(c) and c["close"] > b["close"]:
        return ("three_outside_up", "long")
    if not b_bull and not _is_bullish(c) and c["close"] < b["close"]:
        return ("three_outside_down", "short")
    return None


def detect_three_methods(df: pd.DataFrame, i: int):
    if i < 4:
        return None
    c0, c1, c2, c3, c4 = (df.iloc[i - k] for k in (4, 3, 2, 1, 0))
    small_bodies = max(_body(c1), _body(c2), _body(c3))
    big_bull = _is_bullish(c0) and _body(c0) > 1.5 * small_bodies
    big_bear = (not _is_bullish(c0)) and _body(c0) > 1.5 * small_bodies
    contained = all(
        max(c["open"], c["close"]) <= c0["high"] and min(c["open"], c["close"]) >= c0["low"]
        for c in (c1, c2, c3)
    )
    if not contained:
        return None
    if big_bull and all(not _is_bullish(c) for c in (c1, c2, c3)) and _is_bullish(c4) and c4["close"] > c0["close"]:
        return ("rising_three_methods", "long")
    if big_bear and all(_is_bullish(c) for c in (c1, c2, c3)) and not _is_bullish(c4) and c4["close"] < c0["close"]:
        return ("falling_three_methods", "short")
    return None


_DETECTORS = [
    detect_three_methods,
    detect_three_outside,
    detect_three_inside,
    detect_star,
    detect_three_soldiers_crows,
    detect_engulfing,
    detect_harami,
    detect_piercing_dark_cloud,
    detect_tweezer,
    detect_dragonfly_gravestone,
    detect_marubozu,
    detect_hammer_family,
    detect_inverted_hammer_family,
    detect_spinning_top,
    detect_doji,
]


def detect_candlestick_patterns(df: pd.DataFrame, i: int = None) -> list[dict]:
    """Returns all candlestick patterns matching at index `i` (defaults to the last row)."""
    if df.empty:
        return []
    i = len(df) - 1 if i is None else i
    matches = []
    for detector in _DETECTORS:
        result = detector(df, i)
        if result:
            name, direction = result
            matches.append({"pattern": name, "direction": direction})
    return matches
