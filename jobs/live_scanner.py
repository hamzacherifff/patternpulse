"""Long-lived loop: pull live candles for the whole watchlist, run the signal engine,
persist newly detected (confirmed) signals, and push Telegram alerts for them. Forming
(not yet confirmed) patterns are shown live in the dashboard but deliberately don't
trigger Telegram alerts -- only confirmed signals do. This is what makes signal
generation happen 24/7 instead of only when someone has the dashboard open."""

import logging
import time

from core.config import app as app_config
from core.config import risk as risk_config
from core.config import telegram as telegram_config
from core.db import init_db
from core.security import setup_logging
from core.settings import get_float_setting
from data.watchlist import get_watchlist, seed_watchlist_if_empty
from learning.tracker import record_signal
from signals.engine import SignalEngine
from telegram_bot.notify import broadcast_signal

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 300


def run_once() -> int:
    watchlist = get_watchlist(enabled_only=True)
    if not watchlist:
        watchlist = seed_watchlist_if_empty()
    if not watchlist:
        logger.warning("Watchlist is empty, nothing to scan")
        return 0

    portfolio_size = get_float_setting("portfolio_size", risk_config.default_portfolio_size)
    max_risk_pct = get_float_setting("max_risk_pct", risk_config.default_max_risk_pct)
    engine = SignalEngine(portfolio_size=portfolio_size, max_risk_pct=max_risk_pct)

    signals = engine.scan_watchlist(watchlist, app_config.timeframes)
    new_count = 0
    for sig in signals:
        signal_id = record_signal(sig)
        if signal_id is None:
            continue
        new_count += 1
        logger.info(
            "New signal #%s: %s %s %s (%.0f%%)", signal_id, sig.symbol, sig.timeframe, sig.pattern_type, sig.confidence * 100
        )
        if telegram_config.bot_token:
            sent = broadcast_signal(sig)
            logger.info("Broadcast to %d Telegram subscriber(s)", sent)
    return new_count


def main() -> None:
    setup_logging(app_config.log_dir)
    init_db()
    logger.info("live_scanner started, scanning every %ss", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            new_signals = run_once()
            logger.info("Scan complete: %d new confirmed signal(s)", new_signals)
        except Exception:
            logger.exception("Scan pass failed")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
