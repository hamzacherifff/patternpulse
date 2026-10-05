"""Current buy/sell recommendations: pattern + indicator confirmed, with risk & position sizing."""

import pandas as pd
import streamlit as st

from core.config import risk as risk_config
from core.config import app as app_config
from core.db import get_db, init_db
from core.settings import get_float_setting, set_setting
from core.ui import direction_badge, page_header, pattern_label
from data.watchlist import get_watchlist
from learning.tracker import record_signal
from signals.engine import SignalEngine

init_db()
page_header(
    "Signal Feed",
    "Advisory only. Confidence, risk %, and position size are recommendations for you to "
    "evaluate, not instructions. This tool never places trades.",
    icon="bolt",
)

watchlist = get_watchlist(enabled_only=True)
if not watchlist:
    st.warning("Watchlist is empty. Add symbols from Settings.")
    st.stop()

default_portfolio = get_float_setting("portfolio_size", risk_config.default_portfolio_size)
default_risk_pct = get_float_setting("max_risk_pct", risk_config.default_max_risk_pct)

with st.container():
    col1, col2, col3 = st.columns([2, 2, 1])
    portfolio_size = col1.number_input("Portfolio size ($)", min_value=0.0, value=default_portfolio, step=100.0)
    max_risk_pct = col2.number_input("Max risk per trade (%)", min_value=0.1, max_value=10.0, value=default_risk_pct, step=0.1)
    col3.markdown("<div style='height:1.85rem;'></div>", unsafe_allow_html=True)
    run_scan = col3.button("Scan now", icon=":material/search:", type="primary", use_container_width=True)

if run_scan:
    set_setting("portfolio_size", portfolio_size)
    set_setting("max_risk_pct", max_risk_pct)
    engine = SignalEngine(portfolio_size=portfolio_size, max_risk_pct=max_risk_pct)
    with st.spinner(f"Scanning {len(watchlist)} symbols across {app_config.timeframes}..."):
        signals = engine.scan_watchlist(watchlist, app_config.timeframes)
    saved = 0
    for sig in signals:
        if record_signal(sig) is not None:
            saved += 1
    st.success(f"Scan complete: {len(signals)} signal(s) found, {saved} newly recorded.")

st.markdown("##### Open signals")

conn = get_db()
rows = conn.execute(
    "SELECT * FROM signals WHERE status = 'open' ORDER BY created_at DESC LIMIT 100"
).fetchall()

if not rows:
    st.info("No open signals yet. Click 'Scan now' to generate some.")
else:
    for row in rows:
        d = dict(row)
        accent = "card-accent-long" if d["direction"] == "long" else "card-accent-short"
        st.markdown(
            f"""<div class="card card-hover {accent}">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:0.5rem;">
                    <div>
                        <div class="pattern-title">{d['symbol']} &middot; {d['timeframe']} &middot; {pattern_label(d['pattern_type'])}</div>
                        <div class="pattern-meta">signaled {d['created_at'][:16].replace('T', ' ')} UTC</div>
                    </div>
                    {direction_badge(d['direction'])}
                </div>
                <div style="display:grid; grid-template-columns:repeat(5,1fr); gap:0.8rem; margin-top:0.9rem;">
                    <div><div class="mini-label">Confidence</div><div class="mini-value">{d['confidence']*100:.0f}%</div></div>
                    <div><div class="mini-label">Entry</div><div class="mini-value">{d['entry_price']:.4f}</div></div>
                    <div><div class="mini-label">Stop</div><div class="mini-value">{d['stop_price']:.4f}</div></div>
                    <div><div class="mini-label">Target</div><div class="mini-value">{d['target_price']:.4f}</div></div>
                    <div><div class="mini-label">Size</div><div class="mini-value">${d['position_size_suggested']:.2f}</div></div>
                </div>
                <div class="pattern-meta" style="margin-top:0.6rem;">
                    Risking {d['risk_pct']:.2f}% of a ${d['portfolio_size_at_signal']:.0f} portfolio
                </div>
            </div>""",
            unsafe_allow_html=True,
        )

st.divider()
with st.expander("Recently closed signals"):
    closed = conn.execute(
        """
        SELECT s.symbol, s.timeframe, s.pattern_type, s.direction, s.confidence,
               o.outcome, o.return_pct, o.exit_reason, o.resolved_at
        FROM outcomes o JOIN signals s ON s.id = o.signal_id
        ORDER BY o.resolved_at DESC LIMIT 50
        """
    ).fetchall()
    if closed:
        st.dataframe(pd.DataFrame([dict(r) for r in closed]), use_container_width=True)
    else:
        st.caption("Nothing resolved yet. Outcomes are filled in by the background outcome-tracking job.")
