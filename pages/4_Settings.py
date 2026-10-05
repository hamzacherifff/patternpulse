"""Watchlist editor, portfolio/risk defaults, Telegram linking, and manual pattern-history backtest."""

import streamlit as st

from core.config import app as app_config
from core.config import risk as risk_config
from core.config import telegram as telegram_config
from core.db import get_db, init_db
from core.settings import get_float_setting, set_setting
from core.ui import page_header
from data.watchlist import add_symbol, get_watchlist, set_symbol_enabled
from patterns.symbol_history import seed_symbol_history

init_db()
page_header("Settings", "Watchlist, risk defaults, Telegram alerts, and the pattern-history backtest.", icon="settings")

st.markdown("##### Risk defaults")
st.caption("Used by the Signal Feed page and the background live scanner when computing suggested position sizes.")
c1, c2, c3 = st.columns([2, 2, 1])
portfolio_size = c1.number_input(
    "Default portfolio size ($)", min_value=0.0,
    value=get_float_setting("portfolio_size", risk_config.default_portfolio_size), step=100.0,
)
max_risk_pct = c2.number_input(
    "Default max risk per trade (%)", min_value=0.1, max_value=10.0,
    value=get_float_setting("max_risk_pct", risk_config.default_max_risk_pct), step=0.1,
)
c3.markdown("<div style='height:1.85rem;'></div>", unsafe_allow_html=True)
if c3.button("Save", icon=":material/save:", use_container_width=True):
    set_setting("portfolio_size", portfolio_size)
    set_setting("max_risk_pct", max_risk_pct)
    st.success("Saved.")

st.divider()
st.markdown("##### Watchlist")
conn = get_db()
rows = conn.execute(
    "SELECT symbol, enabled, market_cap_rank FROM watchlist ORDER BY market_cap_rank IS NULL, market_cap_rank"
).fetchall()

grid = st.columns(3)
for i, row in enumerate(rows):
    d = dict(row)
    with grid[i % 3]:
        c1, c2 = st.columns([3, 1])
        rank = f" #{d['market_cap_rank']}" if d["market_cap_rank"] else ""
        c1.markdown(f"**{d['symbol']}**<br><span class='pattern-meta'>rank{rank}</span>", unsafe_allow_html=True)
        enabled = c2.toggle("On", value=bool(d["enabled"]), key=f"wl_{d['symbol']}", label_visibility="collapsed")
        if enabled != bool(d["enabled"]):
            set_symbol_enabled(d["symbol"], enabled)
            st.rerun()

st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
ac1, ac2 = st.columns([3, 1])
new_symbol = ac1.text_input("Add a symbol", placeholder="e.g. MATICUSDT", label_visibility="collapsed")
if ac2.button("Add", icon=":material/add:", use_container_width=True) and new_symbol:
    add_symbol(new_symbol.strip().upper())
    st.rerun()

st.divider()
st.markdown("##### Telegram alerts")
if not telegram_config.bot_token:
    st.warning("TELEGRAM_BOT_TOKEN is not set. Add it to .env to enable Telegram alerts and commands.")
else:
    sub_count = conn.execute("SELECT COUNT(*) AS c FROM telegram_subscribers").fetchone()["c"]
    st.markdown(
        f'<div class="card"><span class="mini-value">{sub_count}</span> '
        f'<span class="pattern-meta">subscriber(s) currently linked</span></div>',
        unsafe_allow_html=True,
    )
    st.caption("Open a chat with your bot on Telegram and send `/subscribe` to start receiving signal alerts there.")

st.divider()
st.markdown("##### Pattern history backtest")
st.caption(
    "Replays this tool's own pattern detectors over each symbol's cached history so new "
    "symbols start with a real win-rate prior instead of a flat 50/50 guess."
)
if st.button("Backtest watchlist pattern history now", icon=":material/sync:"):
    watchlist = get_watchlist(enabled_only=True)
    with st.spinner(f"Backtesting {len(watchlist)} symbols across {app_config.timeframes}... this can take a minute."):
        n = seed_symbol_history(watchlist, app_config.timeframes, limit=500)
    st.success(f"Recorded {n} historical pattern outcome(s) into pattern_stats.")
