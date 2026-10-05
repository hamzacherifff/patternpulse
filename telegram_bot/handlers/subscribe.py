from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_db


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO telegram_subscribers (chat_id, added_at) VALUES (?, ?)",
        (chat_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    await update.message.reply_text("Subscribed. You'll get a message whenever a new signal fires.")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    conn = get_db()
    conn.execute("DELETE FROM telegram_subscribers WHERE chat_id = ?", (chat_id,))
    conn.commit()
    await update.message.reply_text("Unsubscribed.")
