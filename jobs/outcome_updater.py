"""Background job: periodically resolves open signals into win/loss/timeout outcomes and
feeds the result into learning/weights.py's adaptive pattern_stats. This is what makes the
tool 'still monitor what happens after the signal' regardless of whether the user acted on it.
"""

import logging
import time
from datetime import datetime

from core.config import app as app_config
from core.config import learning as learning_config
from core.db import get_db, init_db
from core.security import setup_logging
from core.settings import get_float_setting, set_setting
from data.cache import refresh_candles
from learning.ml_model import train_model, training_sample_count
from learning.tracker import lookforward_candles_for, resolve_outcome
from learning.weights import update_pattern_stats

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
RETRAIN_EVERY_N_NEW_SAMPLES = 20


def _entry_ts_ms(created_at: str) -> int:
    return int(datetime.fromisoformat(created_at).timestamp() * 1000)


def _try_resolve_one(signal_row) -> bool:
    """Returns True if the signal was resolved (and persisted), False if left open."""
    symbol, timeframe = signal_row["symbol"], signal_row["timeframe"]
    lookforward = lookforward_candles_for(timeframe, learning_config.outcome_lookforward_candles)

    df = refresh_candles(symbol, timeframe, limit=max(lookforward + 5, 60))
    if df.empty:
        return False

    entry_ts = _entry_ts_ms(signal_row["created_at"])
    future = df[df["ts"] > entry_ts].reset_index(drop=True)
    if future.empty:
        return False

    window = future.iloc[:lookforward]
    has_full_window = len(window) >= lookforward

    result = resolve_outcome(
        window, signal_row["direction"], signal_row["entry_price"], signal_row["stop_price"], signal_row["target_price"]
    )
    if result is None:
        return False

    outcome, return_pct, exit_price, reason = result
    if not has_full_window and reason == "window_expired":
        return False  # not enough data yet and no early stop/target touch -- keep waiting

    conn = get_db()
    resolved_at = datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO outcomes (signal_id, resolved_at, outcome, return_pct, exit_price, exit_reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (signal_row["id"], resolved_at, outcome, return_pct, exit_price, reason),
    )
    conn.execute("UPDATE signals SET status = 'closed' WHERE id = ?", (signal_row["id"],))
    conn.commit()

    update_pattern_stats(signal_row["pattern_type"], timeframe, symbol, outcome == "win", return_pct)
    logger.info("Resolved signal %s (%s %s %s): %s %.2f%%", signal_row["id"], symbol, timeframe,
                signal_row["pattern_type"], outcome, return_pct)
    return True


def resolve_open_signals() -> int:
    conn = get_db()
    open_signals = conn.execute("SELECT * FROM signals WHERE status = 'open'").fetchall()
    resolved = 0
    for row in open_signals:
        try:
            if _try_resolve_one(row):
                resolved += 1
        except Exception:
            logger.exception("Failed to resolve signal %s", row["id"])
    return resolved


def maybe_retrain_model() -> bool:
    """Retrains the ML model once at least RETRAIN_EVERY_N_NEW_SAMPLES new labeled
    outcomes have accumulated since the last training pass. Cheap no-op otherwise."""
    current_count = training_sample_count()
    last_trained_count = get_float_setting("ml_last_trained_count", 0)
    if current_count - last_trained_count < RETRAIN_EVERY_N_NEW_SAMPLES:
        return False
    model = train_model()
    if model is not None:
        set_setting("ml_last_trained_count", current_count)
        return True
    return False


def main() -> None:
    setup_logging(app_config.log_dir)
    init_db()
    logger.info("outcome_updater started, polling every %ss", POLL_INTERVAL_SECONDS)
    while True:
        try:
            n = resolve_open_signals()
            if n:
                logger.info("Resolved %d signal(s) this pass", n)
            if maybe_retrain_model():
                logger.info("ML model retrained")
        except Exception:
            logger.exception("outcome_updater pass failed")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
