"""Market screener: the whole watchlist at a glance (price change, trend, RSI, ADX,
forming-pattern count, open signal count, and a sparkline per symbol), sortable by any
column and refreshing on its own so you can scan for opportunities instead of checking
symbols one by one."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from core.config import app as app_config
from core.db import get_db, init_db
from core.ui import page_header
from data.cache import refresh_candles
from data.watchlist import get_watchlist
from indicators.technical import compute_indicators, is_strong_trend, trend_direction
from patterns.history import scan_forming_patterns_df

init_db()
page_header("Screener", "The whole watchlist at a glance. Click a column header to sort.", icon="radar")

watchlist = get_watchlist(enabled_only=True)
if not watchlist:
    st.warning("Watchlist is empty. Add symbols from Settings.")
    st.stop()

timeframe = st.selectbox("Timeframe", app_config.timeframes, index=0)

_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6, "1d": 1, "1w": 1}
REFRESH_SECONDS = 45


@st.fragment(run_every=REFRESH_SECONDS)
def _live_screener(timeframe: str):
    @st.cache_data(ttl=REFRESH_SECONDS - 5, show_spinner=False)
    def _load_screener_row(symbol: str, timeframe: str):
        df = refresh_candles(symbol, timeframe, limit=200)
        if df.empty or len(df) < 10:
            return None
        df = compute_indicators(df)
        i = len(df) - 1
        day_bars = _BARS_PER_DAY.get(timeframe, 24)
        ref_idx = max(0, i - day_bars)
        price = float(df["close"].iloc[i])
        ref_price = float(df["close"].iloc[ref_idx])
        change_pct = (price - ref_price) / ref_price * 100 if ref_price else 0.0
        rsi = df["rsi"].iloc[i]
        adx = df["adx"].iloc[i]
        try:
            forming_count = len(scan_forming_patterns_df(df, symbol, timeframe))
        except Exception:
            forming_count = 0
        return {
            "Symbol": symbol,
            "Price": price,
            "24h %": change_pct,
            "Trend": trend_direction(df, i).upper(),
            "RSI": float(rsi) if pd.notna(rsi) else None,
            "ADX": float(adx) if pd.notna(adx) else None,
            "Strong trend": is_strong_trend(df, i),
            "Forming": forming_count,
            "Sparkline": df["close"].tail(30).tolist(),
        }

    conn = get_db()
    open_counts = {
        r["symbol"]: r["c"] for r in conn.execute(
            "SELECT symbol, COUNT(*) AS c FROM signals WHERE status = 'open' GROUP BY symbol"
        ).fetchall()
    }

    def _safe_load(symbol: str):
        try:
            return _load_screener_row(symbol, timeframe)
        except Exception:
            return None

    # Independent per-symbol network calls -- fetching the whole watchlist concurrently
    # instead of one symbol at a time turns ~15x a single round-trip into ~2-3x.
    with ThreadPoolExecutor(max_workers=min(len(watchlist), 10)) as pool:
        loaded_rows = list(pool.map(_safe_load, watchlist))

    rows = []
    for row in loaded_rows:
        if row:
            row["Open signals"] = open_counts.get(row["Symbol"], 0)
            rows.append(row)

    if not rows:
        st.info("No data available yet.")
        return

    df_screen = pd.DataFrame(rows).sort_values("24h %", ascending=False)
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    st.caption(f"LIVE &middot; updated {now_str} &middot; refreshing every {REFRESH_SECONDS}s")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Advancing", int((df_screen["24h %"] > 0).sum()), f"of {len(df_screen)}")
    m2.metric("Declining", int((df_screen["24h %"] < 0).sum()), f"of {len(df_screen)}")
    m3.metric("Strong trend (ADX≥25)", int(df_screen["Strong trend"].sum()))
    m4.metric("Patterns forming", int(df_screen["Forming"].sum()))
    m5.metric("With open signals", int((df_screen["Open signals"] > 0).sum()))

    st.dataframe(
        df_screen,
        use_container_width=True,
        hide_index=True,
        height=min(38 * (len(df_screen) + 1), 700),
        column_config={
            "Price": st.column_config.NumberColumn(format="%.4f"),
            "24h %": st.column_config.NumberColumn(format="%.2f%%"),
            "RSI": st.column_config.NumberColumn(format="%.1f"),
            "ADX": st.column_config.NumberColumn(format="%.1f"),
            "Sparkline": st.column_config.LineChartColumn(width="small"),
            "Strong trend": st.column_config.CheckboxColumn(),
            "Forming": st.column_config.NumberColumn(format="%d", help="Patterns currently forming but not yet confirmed"),
            "Open signals": st.column_config.NumberColumn(format="%d"),
        },
    )


_live_screener(timeframe)
