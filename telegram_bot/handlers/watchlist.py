from telegram import Update
from telegram.ext import ContextTypes

from data.watchlist import get_watchlist


async def watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbols = get_watchlist(enabled_only=True)
    text = ", ".join(symbols) if symbols else "Watchlist is empty."
    await update.message.reply_text(text)
