"""Raw-SQL SQLite access layer. No ORM, WAL mode for multi-process concurrent access."""

import os
import sqlite3
import threading

from core.config import app

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS idx_candles_symbol_timeframe_ts ON candles(symbol, timeframe, ts);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    market_cap_rank INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    pattern_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('long', 'short')),
    confidence REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL NOT NULL,
    risk_pct REAL NOT NULL,
    position_size_suggested REAL NOT NULL,
    portfolio_size_at_signal REAL NOT NULL,
    indicators_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_timeframe ON signals(symbol, timeframe);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_pattern_type ON signals(pattern_type);

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
    resolved_at TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('win', 'loss', 'timeout')),
    return_pct REAL NOT NULL,
    exit_price REAL NOT NULL,
    exit_reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_outcome ON outcomes(outcome);

CREATE TABLE IF NOT EXISTS pattern_stats (
    pattern_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    symbol TEXT NOT NULL,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,
    avg_return REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pattern_type, timeframe, symbol)
);

CREATE TABLE IF NOT EXISTS telegram_subscribers (
    chat_id INTEGER PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('long', 'short')),
    entry_price REAL NOT NULL,
    size REAL NOT NULL,
    opened_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    notes TEXT,
    linked_signal_id INTEGER REFERENCES signals(id)
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);

CREATE TABLE IF NOT EXISTS position_closes (
    position_id INTEGER PRIMARY KEY REFERENCES positions(id),
    exit_price REAL NOT NULL,
    closed_at TEXT NOT NULL,
    realized_pnl REAL NOT NULL
);
"""


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(app.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(app.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
