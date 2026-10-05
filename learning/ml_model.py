"""LightGBM classifier trained on logged signal outcomes once enough labeled data exists.

Blends into (never silently replaces) the stats-based confidence from learning/weights.py --
see signals/engine.py's integration, which keeps the raw ML probability visible in the
signal's indicator snapshot. Below core.config.learning.ml_training_threshold this module
simply has no model on disk yet and predict_win_probability() returns None, so the engine
falls back to the stats-only score.

Training holds out the most recent slice of history (chronological, not a random shuffle --
this model's actual job is predicting signals it hasn't seen yet, so the honest test of
whether it generalizes is validating on the newest data, not data adjacent in time to what it
trained on) and persists the resulting out-of-sample AUC via core.settings. Without this, a
"model trained" indicator says nothing about whether the model is any good -- it could be
fitting noise in its own training set and there would be no way to tell from a "trained: yes"
flag alone. get_validation_metrics() is what pages/3_Performance.py reads to show real
evidence instead of a boolean.
"""

import json
import logging
import os
from datetime import datetime, timezone

import lightgbm as lgb
import pandas as pd

from core.config import app as app_config
from core.config import learning as learning_config
from core.db import get_db
from core.settings import get_setting, set_setting

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(app_config.db_path) or ".", "ml_model.txt")
VALIDATION_HOLDOUT_FRACTION = 0.2
EARLY_STOPPING_ROUNDS = 20
MAX_BOOST_ROUNDS = 300

# Fixed vocabulary so train-time and serve-time feature columns always line up, regardless
# of which pattern types happen to be present in any given training slice.
KNOWN_PATTERNS = [
    "double_top", "double_bottom", "head_and_shoulders", "inverse_head_and_shoulders",
    "ascending_triangle", "descending_triangle", "symmetrical_triangle",
    "rising_wedge", "falling_wedge", "bull_flag", "bear_flag",
]


def _row_to_features(signal_row: dict, indicators: dict) -> dict:
    entry_price = signal_row.get("entry_price") or 1.0
    direction = signal_row.get("direction")
    higher_trend = indicators.get("higher_timeframe_trend", "flat")
    trend_agrees = 1 if (
        higher_trend == "flat"
        or (higher_trend == "up" and direction == "long")
        or (higher_trend == "down" and direction == "short")
    ) else 0

    feats = {
        "confidence": signal_row.get("confidence", 0.5),
        "base_win_rate": indicators.get("base_win_rate", 0.5),
        "pattern_avg_return": indicators.get("pattern_avg_return", 0.0),
        "sample_size": indicators.get("sample_size", 0),
        "pattern_strength": indicators.get("pattern_strength", 0.5),
        "checks_passed": indicators.get("checks_passed", 0),
        "checks_total": indicators.get("checks_total", 3),
        "rsi": indicators.get("rsi", 50.0),
        "macd": indicators.get("macd", 0.0),
        "macd_signal": indicators.get("macd_signal", 0.0),
        "atr_pct": (indicators.get("atr", 0.0) / entry_price) if entry_price else 0.0,
        "volume_spike": int(bool(indicators.get("volume_spike", False))),
        "candlestick_confirmation": int(bool(indicators.get("candlestick_confirmation", False))),
        "trend_agrees": trend_agrees,
        "is_long": 1 if direction == "long" else 0,
    }
    for pattern in KNOWN_PATTERNS:
        feats[f"pattern_{pattern}"] = 1 if signal_row.get("pattern_type") == pattern else 0
    return feats


def _load_training_data() -> pd.DataFrame:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT s.pattern_type, s.direction, s.confidence, s.entry_price, s.indicators_json,
               o.outcome, o.resolved_at
        FROM signals s JOIN outcomes o ON o.signal_id = s.id
        """
    ).fetchall()

    records, labels, resolved_ats = [], [], []
    for row in rows:
        d = dict(row)
        try:
            indicators = json.loads(d["indicators_json"])
        except (TypeError, ValueError):
            indicators = {}
        records.append(_row_to_features(d, indicators))
        labels.append(1 if d["outcome"] == "win" else 0)
        resolved_ats.append(d["resolved_at"])

    df = pd.DataFrame(records)
    df["label"] = labels
    df["resolved_at"] = resolved_ats
    return df


def training_sample_count() -> int:
    conn = get_db()
    return conn.execute("SELECT COUNT(*) AS c FROM outcomes").fetchone()["c"]


def get_validation_metrics() -> dict:
    """The out-of-sample evidence for whether the currently-deployed model is actually
    predictive -- persisted by train_model() at training time, since it's not recoverable
    after the fact (the holdout was folded back into the final refit)."""
    auc = get_setting("ml_model_valid_auc")
    samples = get_setting("ml_model_valid_samples")
    trained_at = get_setting("ml_model_trained_at")
    return {
        "valid_auc": float(auc) if auc is not None else None,
        "valid_samples": int(samples) if samples is not None else None,
        "trained_at": trained_at,
    }


def train_model():
    """Retrains on every resolved signal so far. Returns the Booster, or None if there
    still aren't enough labeled samples (see core.config.learning.ml_training_threshold).

    Splits chronologically: trains on the older ~80%, validates (with early stopping) on the
    newest ~20%, records that holdout AUC as the honest measure of predictiveness, then
    refits on the full dataset (at the iteration count early stopping settled on) so the
    deployed model isn't permanently blind to its most recent ~20% of history once the
    validation read has already been taken."""
    df = _load_training_data()
    if len(df) < learning_config.ml_training_threshold:
        logger.info(
            "Not enough labeled samples yet (%d/%d) -- skipping ML training",
            len(df), learning_config.ml_training_threshold,
        )
        return None

    df = df.sort_values("resolved_at").reset_index(drop=True)
    feature_cols = [c for c in df.columns if c not in ("label", "resolved_at")]

    split_idx = max(int(len(df) * (1 - VALIDATION_HOLDOUT_FRACTION)), 1)
    train_df, valid_df = df.iloc[:split_idx], df.iloc[split_idx:]

    params = {"objective": "binary", "metric": "auc", "verbosity": -1, "num_leaves": 15, "min_data_in_leaf": 5}
    valid_auc = None
    best_iteration = 100

    if len(valid_df) >= 5:
        train_set = lgb.Dataset(train_df[feature_cols], label=train_df["label"])
        valid_set = lgb.Dataset(valid_df[feature_cols], label=valid_df["label"], reference=train_set)
        probe = lgb.train(
            params, train_set, num_boost_round=MAX_BOOST_ROUNDS, valid_sets=[valid_set],
            callbacks=[lgb.early_stopping(stopping_rounds=EARLY_STOPPING_ROUNDS, verbose=False)],
        )
        if probe.best_score.get("valid_0", {}).get("auc") is not None:
            valid_auc = float(probe.best_score["valid_0"]["auc"])
        best_iteration = probe.best_iteration or best_iteration
    else:
        logger.info("Holdout too small (%d rows) to validate -- training without a holdout check", len(valid_df))

    final_set = lgb.Dataset(df[feature_cols], label=df["label"])
    booster = lgb.train(params, final_set, num_boost_round=best_iteration)

    os.makedirs(os.path.dirname(MODEL_PATH) or ".", exist_ok=True)
    booster.save_model(MODEL_PATH)

    if valid_auc is not None:
        set_setting("ml_model_valid_auc", valid_auc)
        set_setting("ml_model_valid_samples", len(valid_df))
        set_setting("ml_model_trained_at", datetime.now(timezone.utc).isoformat())

    logger.info(
        "Trained ML model on %d samples (holdout AUC=%s on %d samples), saved to %s",
        len(df), f"{valid_auc:.3f}" if valid_auc is not None else "n/a", len(valid_df), MODEL_PATH,
    )
    return booster


_cached_booster = None
_cached_mtime = None


def _get_booster():
    global _cached_booster, _cached_mtime
    if not os.path.exists(MODEL_PATH):
        return None
    mtime = os.path.getmtime(MODEL_PATH)
    if _cached_booster is None or mtime != _cached_mtime:
        _cached_booster = lgb.Booster(model_file=MODEL_PATH)
        _cached_mtime = mtime
    return _cached_booster


def predict_win_probability(signal_row: dict, indicators: dict):
    """Returns the ML model's predicted win probability, or None if no trained model exists
    yet -- callers must fall back to the stats-based confidence alone in that case."""
    booster = _get_booster()
    if booster is None:
        return None
    feats = _row_to_features(signal_row, indicators)
    X = pd.DataFrame([feats])[booster.feature_name()]
    return float(booster.predict(X)[0])
