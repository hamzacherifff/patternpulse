"""SignalEngine: chart pattern + candlestick confirmation + indicator confirmation +
multi-timeframe confluence -> a scored, risk-sized Signal. Detection only -- persistence
lives in learning/tracker.py, execution never happens anywhere in this codebase.
"""

import json
from dataclasses import asdict, dataclass
from typing import Optional

from core.config import risk as risk_config
from data.cache import refresh_candles
from indicators.technical import compute_indicators, macd_bearish_cross, macd_bullish_cross, trend_direction
from learning.ml_model import predict_win_probability
from learning.weights import get_pattern_weight
from patterns.candlestick import detect_candlestick_patterns
from patterns.chart_patterns import detect_all as detect_chart_patterns
from signals.risk import compute_risk_plan, compute_stop_price
from signals.strategy_selector import is_combo_allowed

CONFIDENCE_THRESHOLD = 0.55
# A pattern can have a >50% win rate and still be a bad bet if its average win is small and its
# average loss is large (exactly what production data showed: 57.1% win rate, but avg return
# per trade was ~0.002% -- essentially flat -- because avg win (+0.96%) was smaller than avg
# loss (-2.65%)). win_rate alone never sees that asymmetry; pattern_stats.avg_return already
# captures it (it's a real per-trade average, wins and losses combined), it just wasn't being
# used anywhere in confidence scoring before. This turns it into a small, capped adjustment in
# the same style as the existing confirm/candle/trend bonuses, rather than letting a single
# noisy average dominate the score.
EXPECTANCY_SCALE = 3.0  # avg_return (in %) that earns the full adjustment
EXPECTANCY_ADJUSTMENT_CAP = 0.10

_HIGHER_TIMEFRAME = {
    "1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h",
    "1h": "4h", "4h": "1d", "1d": "1w", "1w": "1w",
}


@dataclass
class Signal:
    symbol: str
    timeframe: str
    pattern_type: str
    direction: str
    confidence: float
    entry_price: float
    stop_price: float
    target_price: float
    risk_pct: float
    position_size_suggested: float
    portfolio_size_at_signal: float
    indicators_snapshot: dict

    def to_row(self) -> dict:
        d = asdict(self)
        d["indicators_json"] = json.dumps(d.pop("indicators_snapshot"))
        return d


def _confirmation_score(df_ind, i: int, direction: str) -> tuple[float, dict]:
    checks = {}
    if direction == "long":
        checks["macd"] = bool(macd_bullish_cross(df_ind, i) or df_ind["macd_diff"].iloc[i] > 0)
        checks["rsi"] = bool(df_ind["rsi"].iloc[i] < 75)
    else:
        checks["macd"] = bool(macd_bearish_cross(df_ind, i) or df_ind["macd_diff"].iloc[i] < 0)
        checks["rsi"] = bool(df_ind["rsi"].iloc[i] > 25)
    checks["volume"] = bool(df_ind["volume_spike"].iloc[i])

    passed = sum(1 for v in checks.values() if v)
    snapshot = {
        "macd": float(df_ind["macd"].iloc[i]),
        "macd_signal": float(df_ind["macd_signal"].iloc[i]),
        "rsi": float(df_ind["rsi"].iloc[i]),
        "atr": float(df_ind["atr"].iloc[i]),
        "volume_spike": checks["volume"],
        "checks_passed": passed,
        "checks_total": len(checks),
    }
    return passed / len(checks), snapshot


class SignalEngine:
    def __init__(self, portfolio_size: float = None, max_risk_pct: float = None):
        self.portfolio_size = portfolio_size if portfolio_size is not None else risk_config.default_portfolio_size
        self.max_risk_pct = max_risk_pct if max_risk_pct is not None else risk_config.default_max_risk_pct

    def _higher_timeframe_trend(self, symbol: str, timeframe: str) -> str:
        higher_tf = _HIGHER_TIMEFRAME.get(timeframe, timeframe)
        if higher_tf == timeframe:
            return "flat"
        try:
            df_higher = compute_indicators(refresh_candles(symbol, higher_tf, limit=200))
            if df_higher.empty:
                return "flat"
            return trend_direction(df_higher, len(df_higher) - 1)
        except Exception:
            return "flat"

    def scan_symbol_timeframe(self, symbol: str, timeframe: str, limit: int = 300) -> Optional[Signal]:
        df = refresh_candles(symbol, timeframe, limit=limit)
        if len(df) < 60:
            return None
        df_ind = compute_indicators(df)
        i = len(df_ind) - 1

        # Only confirmed breakouts become tradeable signals -- forming/potential patterns are
        # a separate, lighter-weight concept (see patterns/history.py) that never enters the
        # learning loop, since they haven't produced an outcome to grade yet.
        chart_matches = [m for m in detect_chart_patterns(df_ind) if m.get("status") == "confirmed"]
        if not chart_matches:
            return None

        # Skip pattern types or symbols with a PROVEN bad track record (see
        # signals/strategy_selector.py) -- still-unproven ones pass through untouched, this
        # only benches things that have already demonstrated negative expectancy.
        chart_matches = [m for m in chart_matches if is_combo_allowed(m["pattern"], symbol, timeframe)]
        if not chart_matches:
            return None

        candle_matches = detect_candlestick_patterns(df_ind, i)
        candle_directions = {m["direction"] for m in candle_matches}
        higher_trend = self._higher_timeframe_trend(symbol, timeframe)
        entry_price = float(df_ind["close"].iloc[i])
        atr = float(df_ind["atr"].iloc[i]) or entry_price * 0.01

        best = None
        for match in chart_matches:
            direction = match["direction"]

            # Reward:risk discipline, checked before this candidate is even scored -- a
            # professional trader skips a setup whose own geometry doesn't justify the risk,
            # no matter how "confident" the pattern otherwise looks. Real production data
            # (1293 resolved signals) showed 95% of signals had a target closer than the
            # stop (median 0.37:1), which is why a 68% win rate still produced a negative
            # average return: the stop is deliberately set wide (never inside the pattern's
            # own invalidation level) but nothing ever checked the target against that width.
            candidate_stop = compute_stop_price(direction, entry_price, atr, match["invalidation_price"])
            stop_distance = abs(entry_price - candidate_stop)
            target_distance = abs(match["target_price"] - entry_price)
            reward_risk_ratio = (target_distance / stop_distance) if stop_distance > 0 else 0.0
            if reward_risk_ratio < risk_config.min_reward_risk_ratio:
                continue

            confirm_score, snapshot = _confirmation_score(df_ind, i, direction)
            candle_confirms = direction in candle_directions
            trend_agrees = higher_trend == "flat" or \
                (higher_trend == "up" and direction == "long") or \
                (higher_trend == "down" and direction == "short")

            weight = get_pattern_weight(match["pattern"], timeframe, symbol)
            base_score = weight["win_rate"] * match["strength"]
            expectancy_adjustment = max(min(weight["avg_return"] / EXPECTANCY_SCALE, 1.0), -1.0) * EXPECTANCY_ADJUSTMENT_CAP
            bonus = 0.10 * confirm_score + (0.05 if candle_confirms else 0) + (0.05 if trend_agrees else 0) + expectancy_adjustment
            stats_confidence = min(max(base_score + bonus, 0.0), 0.99)

            snapshot.update(
                candlestick_confirmation=candle_confirms,
                higher_timeframe_trend=higher_trend,
                pattern_strength=match["strength"],
                base_win_rate=weight["win_rate"],
                sample_size=weight["total"],
                pattern_avg_return=weight["avg_return"],
                expectancy_adjustment=round(expectancy_adjustment, 4),
                reward_risk_ratio=round(reward_risk_ratio, 3),
            )

            # Once a trained model exists (see learning/ml_model.py), blend its win-probability
            # in -- weighted more heavily than the stats prior, since it has seen more signals
            # than any single pattern/timeframe/symbol slice -- but keep both numbers visible in
            # the snapshot rather than letting the ML score silently override the stats one.
            ml_prob = predict_win_probability(
                {"pattern_type": match["pattern"], "direction": direction, "confidence": stats_confidence,
                 "entry_price": entry_price},
                snapshot,
            )
            snapshot["stats_confidence"] = round(stats_confidence, 4)
            snapshot["ml_win_probability"] = round(ml_prob, 4) if ml_prob is not None else None
            confidence = min(0.4 * stats_confidence + 0.6 * ml_prob, 0.99) if ml_prob is not None else stats_confidence

            if confidence < CONFIDENCE_THRESHOLD:
                continue
            if best is None or confidence > best[0]:
                best = (confidence, match, snapshot)

        if best is None:
            return None

        confidence, match, snapshot = best
        plan = compute_risk_plan(
            direction=match["direction"],
            entry_price=entry_price,
            atr=atr,
            invalidation_price=match["invalidation_price"],
            target_price=match["target_price"],
            portfolio_size=self.portfolio_size,
            max_risk_pct=self.max_risk_pct,
        )

        return Signal(
            symbol=symbol,
            timeframe=timeframe,
            pattern_type=match["pattern"],
            direction=match["direction"],
            confidence=round(confidence, 4),
            entry_price=entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            risk_pct=plan.risk_pct,
            position_size_suggested=plan.position_size,
            portfolio_size_at_signal=self.portfolio_size,
            indicators_snapshot=snapshot,
        )

    def scan_watchlist(self, symbols: list[str], timeframes: list[str]) -> list[Signal]:
        signals = []
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    sig = self.scan_symbol_timeframe(symbol, timeframe)
                except Exception:
                    sig = None
                if sig:
                    signals.append(sig)
        return signals
