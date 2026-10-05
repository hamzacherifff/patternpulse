"""Home: portfolio-level snapshot (watchlist size, open signals, win rate, ML status) plus
quick links into every other page."""

import logging
import os

import streamlit as st

from core.db import get_db
from core.ui import APP_ICON, APP_NAME, page_header
from data.watchlist import seed_watchlist_if_empty
from learning.ml_model import MODEL_PATH, training_sample_count

logger = logging.getLogger(__name__)

page_header(
    APP_NAME,
    "Advisory only. Every signal is a recommendation based on pattern recognition and "
    "indicator confirmation. This tool never places trades on your behalf.",
    icon=APP_ICON,
    show_brand=False,
)

with st.spinner("Loading watchlist..."):
    try:
        watchlist = seed_watchlist_if_empty()
    except Exception as exc:
        logger.exception("Failed to seed watchlist")
        st.error(f"Could not load watchlist from Binance/CoinGecko: {exc}")
        watchlist = []

conn = get_db()
open_signals = conn.execute("SELECT COUNT(*) AS c FROM signals WHERE status = 'open'").fetchone()["c"]
resolved = conn.execute(
    "SELECT COUNT(*) AS c, SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS w FROM outcomes"
).fetchone()
total_resolved = resolved["c"] or 0
win_rate = (resolved["w"] or 0) / total_resolved * 100 if total_resolved else None
ml_trained = os.path.exists(MODEL_PATH)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Watchlist size", len(watchlist))
m2.metric("Open signals", open_signals)
m3.metric("Win rate", f"{win_rate:.0f}%" if win_rate is not None else "n/a", help="Across every resolved signal so far")
m4.metric("ML model", "Trained" if ml_trained else f"{training_sample_count()} samples")

st.markdown("##### Watchlist")
if watchlist:
    st.markdown(
        '<div class="chip-row">' + "".join(f'<span class="chip">{s}</span>' for s in watchlist) + "</div>",
        unsafe_allow_html=True,
    )
else:
    st.info("No symbols yet. Check Settings to add some.")

st.divider()
st.markdown("##### Explore")
nav_items = [
    ("pages/1_Live_Charts.py", ":material/candlestick_chart:", "Live Charts", "Charts with every detected pattern drawn in place"),
    ("pages/5_Screener.py", ":material/radar:", "Screener", "The whole watchlist at a glance, sortable"),
    ("pages/2_Signal_Feed.py", ":material/bolt:", "Signal Feed", "Current recommendations with risk & position sizing"),
    ("pages/6_Positions.py", ":material/account_balance_wallet:", "Positions", "Trades you actually took, with live P&L"),
    ("pages/3_Performance.py", ":material/monitoring:", "Performance", "How past signals have actually done"),
    ("pages/4_Settings.py", ":material/settings:", "Settings", "Watchlist, risk defaults, Telegram alerts"),
]
nav_cols = st.columns(3)
for idx, (path, icon, label, desc) in enumerate(nav_items):
    col = nav_cols[idx % 3]
    with col:
        st.markdown(
            f'<div class="card card-hover" style="min-height:96px;">'
            f'<div style="font-weight:700; margin:0 0 0.2rem 0;">{label}</div>'
            f'<div class="pattern-meta">{desc}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )
        st.page_link(path, label=f"Open {label}", icon=icon)
