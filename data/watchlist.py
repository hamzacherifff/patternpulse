"""Watchlist management: seeded from CoinGecko market-cap ranking, editable afterwards."""

from datetime import datetime, timezone

from core.config import app
from core.db import get_db
from data.binance_client import fetch_top_symbols_by_market_cap


def get_watchlist(enabled_only: bool = True) -> list[str]:
    conn = get_db()
    query = "SELECT symbol FROM watchlist"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY market_cap_rank IS NULL, market_cap_rank ASC"
    return [r["symbol"] for r in conn.execute(query).fetchall()]


def seed_watchlist_if_empty() -> list[str]:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()["c"]
    if count > 0:
        return get_watchlist(enabled_only=False)

    symbols = fetch_top_symbols_by_market_cap(app.watchlist_size, app.quote_asset)
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO watchlist (symbol, enabled, market_cap_rank, updated_at) VALUES (?, 1, ?, ?)",
        [(sym, rank + 1, now) for rank, sym in enumerate(symbols)],
    )
    conn.commit()
    return symbols


def set_symbol_enabled(symbol: str, enabled: bool) -> None:
    conn = get_db()
    conn.execute("UPDATE watchlist SET enabled = ? WHERE symbol = ?", (1 if enabled else 0, symbol))
    conn.commit()


def add_symbol(symbol: str) -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (symbol, enabled, market_cap_rank, updated_at) VALUES (?, 1, NULL, ?)",
        (symbol.upper(), now),
    )
    conn.commit()
