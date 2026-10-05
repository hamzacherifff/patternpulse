"""Interactive Telegram bot: /start, /subscribe, /unsubscribe, /watchlist, /status."""

import logging

from telegram.ext import Application, CommandHandler

from core.config import app as app_config
from core.config import telegram as telegram_config
from core.db import init_db
from core.security import setup_logging, validate_config
from telegram_bot.handlers.start import start
from telegram_bot.handlers.status import status
from telegram_bot.handlers.subscribe import subscribe, unsubscribe
from telegram_bot.handlers.watchlist import watchlist

logger = logging.getLogger(__name__)


def build_app() -> Application:
    application = Application.builder().token(telegram_config.bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("watchlist", watchlist))
    application.add_handler(CommandHandler("status", status))
    return application


def main() -> None:
    setup_logging(app_config.log_dir)
    validate_config(require_telegram=True)
    init_db()
    application = build_app()
    logger.info("Telegram bot starting (polling)...")
    application.run_polling()


if __name__ == "__main__":
    main()
