from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_db


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_db()
    open_count = conn.execute("SELECT COUNT(*) AS c FROM signals WHERE status = 'open'").fetchone()["c"]
    resolved = conn.execute(
        "SELECT COUNT(*) AS c, SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS w FROM outcomes"
    ).fetchone()
    total_resolved = resolved["c"] or 0
    wins = resolved["w"] or 0
    win_rate = (wins / total_resolved * 100) if total_resolved else 0.0

    await update.message.reply_text(
        f"Open signals: {open_count}\n"
        f"Resolved signals: {total_resolved}\n"
        f"Overall win rate: {win_rate:.1f}%"
    )
