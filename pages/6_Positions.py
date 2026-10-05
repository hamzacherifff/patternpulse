"""Manual trade log: positions you actually took, with live unrealized P&L, a close
flow, realized P&L history, and, when linked to a signal, a comparison against what
the tool actually recommended. Also the automatic paper portfolio: every signal taken as
recommended, position size scaled by conviction, ticking live second-by-second."""

import json
import re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from core.config import app as app_config
from core.db import get_db, init_db
from core.positions import close_position, delete_position, get_closed_positions, get_open_positions, open_position, unrealized_pnl
from core.ui import direction_badge, page_header, pattern_label
from data.cache import refresh_candles
from data.watchlist import get_watchlist
from learning.paper_portfolio import (
    MAX_LEVERAGE, MIN_LEVERAGE, compute_paper_history, get_open_paper_positions,
    get_starting_capital, set_starting_capital,
)

_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")


def _build_paper_live_html(positions: list[dict], realized_balance: float, starting_capital: float) -> str:
    """A single self-contained HTML/JS block that renders the account balance header and
    every open position once, then ticks all of it -- balance, per-position P&L, everything
    -- every second via client-side JS polling Binance's public bulk ticker endpoint directly.
    No Streamlit rerun is involved in a tick, so nothing can blink or flash; this is the exact
    same architecture as the Live Charts page's price ticker, for the exact same reason: even
    a fragment-scoped Streamlit rerun dims/redraws its whole subtree on every tick, which at a
    1-second cadence reads as constant flicker instead of a smoothly moving number. This
    component only actually remounts when the *content* changes (a position opens, closes, or
    the realized balance moves) -- since neither happens on a 1-second cadence, it naturally
    stays mounted across ticks with no extra work needed to keep it that way."""
    safe_positions = []
    for p in positions:
        if not _SYMBOL_RE.match(p["symbol"]):
            continue
        safe_positions.append({
            "id": p["id"], "symbol": p["symbol"], "timeframe": p["timeframe"],
            "pattern": pattern_label(p["pattern_type"]), "direction": p["direction"],
            "entry_price": p["entry_price"], "size": p["simulated_position_size"],
            "confidence": p["confidence"], "leverage": p["confidence_leverage"],
        })
    symbols = sorted({p["symbol"] for p in safe_positions})
    positions_json = json.dumps(safe_positions)
    symbols_json = json.dumps(symbols)
    height = 150 + len(safe_positions) * 96

    return f"""
    <div id="pp-header"></div>
    <div id="pp-positions"></div>
    <style>
        body {{ margin:0; padding:0; background:transparent; font-family:'Inter',-apple-system,sans-serif; }}
        .ppp-balance-card {{
            background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 0.8rem;
        }}
        .ppp-balance-label {{ color:#7A8296; font-size:0.72rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; }}
        .ppp-balance-value {{ font-family:'JetBrains Mono',monospace; font-size:1.7rem; font-weight:800; color:#E5E7EE; margin-top:0.2rem; }}
        .ppp-balance-delta {{ font-family:'JetBrains Mono',monospace; font-size:0.92rem; font-weight:700; margin-left:0.6rem; }}
        .ppp-card {{
            background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px; padding: 0.85rem 1.1rem; margin-bottom: 0.5rem;
            display:flex; justify-content:space-between; align-items:center;
        }}
        .ppp-long {{ border-left: 2px solid #22C55E; }}
        .ppp-short {{ border-left: 2px solid #EF4444; }}
        .ppp-title {{ font-size:0.95rem; font-weight:700; color:#E5E7EE; margin-bottom:0.15rem; }}
        .ppp-meta {{ color:#8B93A7; font-size:0.76rem; font-family:'JetBrains Mono',monospace; }}
        .ppp-badge {{
            display:inline-block; padding:2px 9px; border-radius:3px; font-size:0.66rem; font-weight:700;
            letter-spacing:0.03em; font-family:'JetBrains Mono',monospace;
        }}
        .ppp-badge-long {{ background:rgba(34,197,94,0.12); color:#22C55E; border:1px solid rgba(34,197,94,0.3); }}
        .ppp-badge-short {{ background:rgba(239,68,68,0.12); color:#EF4444; border:1px solid rgba(239,68,68,0.3); }}
        .ppp-pnl {{ font-family:'JetBrains Mono',monospace; font-weight:700; text-align:right; margin-top:0.3rem; }}
    </style>
    <script>
        var positions = {positions_json};
        var symbols = {symbols_json};
        var realizedBalance = {realized_balance};
        var startingCapital = {starting_capital};

        function fmtEuro(v) {{
            var s = v < 0 ? '-€' : '€';
            return s + Math.abs(v).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
        }}

        var headerEl = document.getElementById('pp-header');
        headerEl.innerHTML =
            '<div class="ppp-balance-card">' +
                '<div class="ppp-balance-label">Balance now (live)</div>' +
                '<span class="ppp-balance-value" id="ppp-balance">' + fmtEuro(realizedBalance) + '</span>' +
                '<span class="ppp-balance-delta" id="ppp-balance-delta"></span>' +
            '</div>';

        var posRoot = document.getElementById('pp-positions');
        positions.forEach(function(p) {{
            var card = document.createElement('div');
            card.className = 'ppp-card ' + (p.direction === 'long' ? 'ppp-long' : 'ppp-short');
            card.innerHTML =
                '<div>' +
                    '<div class="ppp-title">' + p.symbol + ' &middot; ' + p.timeframe + ' &middot; ' + p.pattern + '</div>' +
                    '<div class="ppp-meta">entry ' + p.entry_price.toFixed(4) + ' &middot; size €' + p.size.toFixed(2) +
                        ' &middot; ' + p.leverage.toFixed(2) + 'x conviction (' + Math.round(p.confidence * 100) + '% confidence)</div>' +
                '</div>' +
                '<div style="text-align:right;">' +
                    '<span class="ppp-badge ppp-badge-' + p.direction + '">' + (p.direction === 'long' ? '▲ LONG' : '▼ SHORT') + '</span>' +
                    '<div class="ppp-pnl" id="ppp-pnl-' + p.id + '">&hellip;</div>' +
                '</div>';
            posRoot.appendChild(card);
        }});

        async function tick() {{
            var totalUnrealized = 0;
            if (symbols.length) {{
                try {{
                    var resp = await fetch('https://api.binance.com/api/v3/ticker/price?symbols=' + encodeURIComponent(JSON.stringify(symbols)));
                    var data = await resp.json();
                    var priceBySymbol = {{}};
                    data.forEach(function(d) {{ priceBySymbol[d.symbol] = parseFloat(d.price); }});
                    positions.forEach(function(p) {{
                        var price = priceBySymbol[p.symbol];
                        if (!isFinite(price)) return;
                        var pct = p.direction === 'long' ? (price / p.entry_price - 1) * 100 : (1 - price / p.entry_price) * 100;
                        var pnl = p.size * pct / 100;
                        totalUnrealized += pnl;
                        var el = document.getElementById('ppp-pnl-' + p.id);
                        if (el) {{
                            el.style.color = pnl >= 0 ? '#22C55E' : '#EF4444';
                            el.innerHTML = fmtEuro(pnl) + ' (' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%)';
                        }}
                    }});
                }} catch (e) {{ /* transient network hiccup -- keep last known values, try again next tick */ }}
            }}
            var total = realizedBalance + totalUnrealized;
            var totalReturnPct = startingCapital ? (total - startingCapital) / startingCapital * 100 : 0;
            var balEl = document.getElementById('ppp-balance');
            var deltaEl = document.getElementById('ppp-balance-delta');
            if (balEl) balEl.textContent = fmtEuro(total);
            if (deltaEl) {{
                deltaEl.style.color = totalReturnPct >= 0 ? '#22C55E' : '#EF4444';
                deltaEl.textContent = (totalReturnPct >= 0 ? '+' : '') + totalReturnPct.toFixed(2) + '%';
            }}
        }}
        tick();
        setInterval(tick, 1000);
    </script>
    """

init_db()
page_header("Positions", "Trades you actually took, tracked separately from the tool's own recommendations.", icon="account_balance_wallet")

conn = get_db()
watchlist = get_watchlist(enabled_only=True)

PAPER_REFRESH_SECONDS = 20


# ---- Paper portfolio: every signal, taken automatically, sized by conviction ----------------------------------------------------------------
@st.fragment(run_every=PAPER_REFRESH_SECONDS)
def _paper_portfolio():
    st.markdown("##### Paper portfolio")
    st.caption(
        "Automatic -- every signal the tool has ever generated, taken exactly as recommended, "
        "compounding from a starting balance you set once. Position size scales with "
        f"conviction: {MIN_LEVERAGE:.1f}x (the full normal risk-sized position) at the bare-minimum "
        f"confidence needed to fire at all, up to {MAX_LEVERAGE:.1f}x at maximum confidence -- "
        "higher multipliers cut both ways, amplifying wins and losses equally, not just wins. "
        "No logging required -- balance and every open position tick live, second by second."
    )

    starting_capital = get_starting_capital()
    cap_col, save_col = st.columns([3, 1])
    new_capital = cap_col.number_input(
        "Starting capital", min_value=10.0, value=starting_capital, step=50.0, format="%.2f", key="paper_capital_input",
    )
    save_col.markdown("<div style='height:1.85rem;'></div>", unsafe_allow_html=True)
    if save_col.button("Save", icon=":material/save:", use_container_width=True, key="save_paper_capital") and new_capital != starting_capital:
        set_starting_capital(new_capital)
        st.rerun()

    paper_history = compute_paper_history(starting_capital)
    paper_balance = float(paper_history["simulated_balance"].iloc[-1]) if not paper_history.empty else starting_capital
    open_paper_positions = get_open_paper_positions(paper_balance)
    # get_open_paper_positions() allocates capital sequentially against what's actually still
    # available (see its docstring) -- once the account's capital is fully committed to earlier
    # positions, later ones correctly get €0, not a bug. Only funded ones are worth a card;
    # the rest get a one-line count instead of 100+ empty-looking cards.
    funded_positions = [p for p in open_paper_positions if p["simulated_position_size"] > 0.01]
    unfunded_count = len(open_paper_positions) - len(funded_positions)

    pp1, pp2 = st.columns(2)
    pp1.metric("Signals resolved", len(paper_history))
    pp2.metric("Signals open now", len(open_paper_positions))

    # Balance + every FUNDED open position's P&L render once here, then tick live every second
    # entirely client-side (see _build_paper_live_html's docstring for why that's the only
    # way to get real per-second movement without a visible Streamlit-rerun blink).
    components.html(
        _build_paper_live_html(funded_positions, paper_balance, starting_capital),
        height=150 + len(funded_positions) * 96,
        scrolling=False,
    )
    if funded_positions:
        st.caption("Prices poll Binance directly every second, client-side. No page reload involved.")
    if unfunded_count:
        st.caption(
            f"{unfunded_count} more signal(s) are open but not currently funded -- the "
            "account's capital is already committed to the positions above; these will get "
            "sized once one of them resolves and frees up capital."
        )

    with st.expander(f"Closed (auto) -- {len(paper_history)} resolved", icon=":material/history:"):
        if paper_history.empty:
            st.caption("Nothing resolved yet -- the first signals to close out will show up here automatically.")
        else:
            show_df = paper_history[
                ["symbol", "timeframe", "pattern_type", "direction", "confidence", "confidence_leverage",
                 "outcome", "return_pct", "simulated_realized_pnl", "simulated_balance", "resolved_at"]
            ].sort_values("resolved_at", ascending=False).copy()
            show_df["pattern_type"] = show_df["pattern_type"].map(pattern_label)
            show_df = show_df.rename(columns={
                "pattern_type": "pattern", "confidence_leverage": "conviction", "return_pct": "return %",
                "simulated_realized_pnl": "pnl (€)", "simulated_balance": "balance after (€)", "resolved_at": "resolved",
            })
            st.dataframe(show_df, use_container_width=True, hide_index=True)
            st.download_button(
                "Export CSV", show_df.to_csv(index=False), file_name="paper_portfolio.csv",
                icon=":material/download:", key="export_paper_csv",
            )


_paper_portfolio()

st.divider()

# ---- Manual position log ----------------------------------------------------------------
st.markdown("##### Your own trade log")
st.caption("Log real or hypothetical trades you took by hand, separate from the automatic paper portfolio above.")

open_positions = get_open_positions()
closed_positions = get_closed_positions()

total_realized = sum(p["realized_pnl"] for p in closed_positions)
win_count = sum(1 for p in closed_positions if p["realized_pnl"] > 0)
win_rate = (win_count / len(closed_positions) * 100) if closed_positions else None
open_exposure = sum(p["size"] for p in open_positions)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Realized P&L", f"${total_realized:,.2f}")
m2.metric("Win rate (closed)", f"{win_rate:.0f}%" if win_rate is not None else "--")
m3.metric("Open exposure", f"${open_exposure:,.2f}")
m4.metric("Open positions", len(open_positions))

st.divider()

# ---- Log a new position ----------------------------------------------------------------
with st.expander("Log a new position", icon=":material/add:", expanded=len(open_positions) == 0):
    open_signals = conn.execute(
        "SELECT id, symbol, timeframe, pattern_type, direction FROM signals WHERE status = 'open' ORDER BY created_at DESC"
    ).fetchall()
    signal_options = {"None": None}
    for s in open_signals:
        signal_options[f"#{s['id']} {s['symbol']} {s['timeframe']} {pattern_label(s['pattern_type'])} ({s['direction']})"] = s["id"]

    c1, c2, c3 = st.columns(3)
    symbol_choice = c1.selectbox("Symbol", watchlist if watchlist else ["BTCUSDT"], key="pos_symbol")
    direction_choice = c2.selectbox("Direction", ["long", "short"], key="pos_direction")
    entry_price = c3.number_input("Entry price", min_value=0.0, value=0.0, step=0.01, format="%.4f", key="pos_entry")

    c4, c5 = st.columns(2)
    size = c4.number_input("Size ($)", min_value=0.0, value=100.0, step=10.0, key="pos_size")
    linked_label = c5.selectbox("Link to a signal (optional)", list(signal_options.keys()), key="pos_signal")
    notes = st.text_input("Notes (optional)", key="pos_notes")

    if st.button("Log position", type="primary"):
        if entry_price <= 0 or size <= 0:
            st.error("Entry price and size must be greater than zero.")
        else:
            open_position(
                symbol=symbol_choice, direction=direction_choice, entry_price=entry_price, size=size,
                notes=notes, linked_signal_id=signal_options[linked_label],
            )
            st.success(f"Logged {direction_choice} {symbol_choice} @ {entry_price}.")
            st.rerun()

st.divider()

# ---- Open positions ----------------------------------------------------------------
st.markdown("##### Open positions")
if not open_positions:
    st.caption("No open positions yet. Log one above.")
else:
    current_prices = {}
    for p in open_positions:
        if p["symbol"] not in current_prices:
            try:
                df = refresh_candles(p["symbol"], app_config.timeframes[0], limit=2)
                current_prices[p["symbol"]] = float(df["close"].iloc[-1]) if not df.empty else None
            except Exception:
                current_prices[p["symbol"]] = None

    for p in open_positions:
        price = current_prices.get(p["symbol"])
        pnl = unrealized_pnl(p, price) if price else None
        pnl_pct = (pnl / p["size"] * 100) if pnl is not None else None
        accent = "card-accent-long" if p["direction"] == "long" else "card-accent-short"
        pnl_color = "#22C55E" if (pnl or 0) >= 0 else "#EF4444"

        card_col, action_col = st.columns([6, 1])
        with card_col:
            link_note = f" &middot; linked to signal #{p['linked_signal_id']}" if p["linked_signal_id"] else ""
            st.markdown(
                f"""<div class="card card-hover {accent}">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div class="pattern-title">{p['symbol']}</div>
                            <div class="pattern-meta">entry {p['entry_price']:.4f} &middot; size ${p['size']:.2f}
                                &middot; opened {p['opened_at'][:16].replace('T', ' ')} UTC{link_note}</div>
                        </div>
                        <div style="text-align:right;">
                            {direction_badge(p['direction'])}
                            <div style="color:{pnl_color}; font-weight:700;">
                                {f"${pnl:,.2f} ({pnl_pct:+.1f}%)" if pnl is not None else "price unavailable"}
                            </div>
                        </div>
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )
        with action_col:
            st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)
            if st.button("Close", key=f"close_btn_{p['id']}", use_container_width=True):
                st.session_state["closing_position_id"] = p["id"]

        if st.session_state.get("closing_position_id") == p["id"]:
            with st.form(key=f"close_form_{p['id']}"):
                exit_price = st.number_input(
                    "Exit price", min_value=0.0, value=price or p["entry_price"], step=0.01, format="%.4f"
                )
                fc1, fc2 = st.columns(2)
                confirm = fc1.form_submit_button("Confirm close", type="primary")
                cancel = fc2.form_submit_button("Cancel")
                if confirm:
                    realized = close_position(p["id"], exit_price)
                    st.session_state["closing_position_id"] = None
                    st.success(f"Closed {p['symbol']}. Realized P&L ${realized:,.2f}.")
                    st.rerun()
                if cancel:
                    st.session_state["closing_position_id"] = None
                    st.rerun()

st.divider()

# ---- Closed positions ----------------------------------------------------------------
st.markdown("##### Closed positions")
if not closed_positions:
    st.caption("Nothing closed yet.")
else:
    df_closed = pd.DataFrame(closed_positions)[
        ["symbol", "direction", "entry_price", "exit_price", "size", "realized_pnl", "opened_at", "closed_at"]
    ]
    st.dataframe(df_closed, use_container_width=True, hide_index=True)
    st.download_button("Export CSV", df_closed.to_csv(index=False), file_name="closed_positions.csv", icon=":material/download:")
