"""Candle cache: upserts fetched candles into SQLite, serves cached history back as DataFrames."""

import pandas as pd

from core.db import get_db
from data.binance_client import fetch_klines


def upsert_candles(candles: list[dict]) -> None:
    if not candles:
        return
    conn = get_db()
    conn.executemany(
        """
        INSERT INTO candles (symbol, timeframe, ts, open, high, low, close, volume)
        VALUES (:symbol, :timeframe, :ts, :open, :high, :low, :close, :volume)
        ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume
        """,
        candles,
    )
    conn.commit()


def load_candles(symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT ts, open, high, low, close, volume FROM candles
        WHERE symbol = ? AND timeframe = ?
        ORDER BY ts DESC LIMIT ?
        """,
        (symbol, timeframe, limit),
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in reversed(rows)])
    if not df.empty:
        df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def refresh_candles(symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """Pulls fresh candles from Binance, caches them, and returns the up-to-date DataFrame."""
    candles = fetch_klines(symbol, timeframe, limit=limit)
    upsert_candles(candles)
    return load_candles(symbol, timeframe, limit=limit)
