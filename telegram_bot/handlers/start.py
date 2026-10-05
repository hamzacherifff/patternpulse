from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📡 PatternPulse\n"
        "Advisory pattern-recognition signals. Never financial advice, never auto-traded.\n\n"
        "/subscribe: get alerted on new signals\n"
        "/unsubscribe: stop alerts\n"
        "/watchlist: show tracked symbols\n"
        "/status: open signal count and recent performance"
    )
