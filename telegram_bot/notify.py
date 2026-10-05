"""Fire-and-forget Telegram alert sending, shared by telegram_bot/bot.py (nothing here,
kept separate for the interactive polling app) and jobs/live_scanner.py."""

import asyncio
import logging

from telegram import Bot
from telegram.error import TelegramError

from core.config import telegram as telegram_config
from core.db import get_db

logger = logging.getLogger(__name__)


def get_subscriber_chat_ids() -> list[int]:
    conn = get_db()
    return [r["chat_id"] for r in conn.execute("SELECT chat_id FROM telegram_subscribers").fetchall()]


async def _send_async(chat_id: int, text: str) -> None:
    bot = Bot(token=telegram_config.bot_token)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


def send_message(chat_id: int, text: str) -> bool:
    if not telegram_config.bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set -- skipping Telegram notification")
        return False
    try:
        asyncio.run(_send_async(chat_id, text))
        return True
    except TelegramError:
        logger.exception("Failed to send Telegram message to %s", chat_id)
        return False


def format_signal_message(signal) -> str:
    direction_label = "\U0001F7E2 LONG" if signal.direction == "long" else "\U0001F534 SHORT"
    return (
        f"*{signal.symbol}* ({signal.timeframe}) · {signal.pattern_type.replace('_', ' ').title()} · {direction_label}\n"
        f"Confidence: {signal.confidence * 100:.0f}%\n"
        f"Entry: {signal.entry_price:.4f}\n"
        f"Stop: {signal.stop_price:.4f}\n"
        f"Target: {signal.target_price:.4f}\n"
        f"Risk: {signal.risk_pct:.2f}% · Suggested size: ${signal.position_size_suggested:.2f}\n\n"
        f"_Advisory only. Not financial advice, this is a recommendation, not an instruction._"
    )


def broadcast_signal(signal) -> int:
    text = format_signal_message(signal)
    sent = 0
    for chat_id in get_subscriber_chat_ids():
        if send_message(chat_id, text):
            sent += 1
    return sent


def format_forming_message(symbol: str, timeframe: str, match: dict) -> str:
    direction_label = "\U0001F7E2 leaning long" if match["direction"] == "long" else "\U0001F534 leaning short"
    pattern_name = match["pattern"].replace("_", " ").title()
    return (
        f"⏳ *{symbol}* ({timeframe}) · {pattern_name} forming · {direction_label}\n"
        f"Progress: {match['progress'] * 100:.0f}% toward confirmation\n"
        f"{match['missing']}\n"
        f"Historically wins {match['historical_win_rate'] * 100:.0f}% of the time here (n={match['sample_size']})\n\n"
        f"_Heads up only, not a signal yet. This tool will alert again if it confirms._"
    )


def broadcast_forming(symbol: str, timeframe: str, match: dict) -> int:
    text = format_forming_message(symbol, timeframe, match)
    sent = 0
    for chat_id in get_subscriber_chat_ids():
        if send_message(chat_id, text):
            sent += 1
    return sent
