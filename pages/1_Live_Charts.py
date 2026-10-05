"""Live candlestick chart with every detected pattern drawn directly on it: confirmed
breakouts (solid, colored by direction) and patterns still forming (amber outline, with
a progress readout and what's still missing in the panel below), plus a full history of
past pattern occurrences for the selected symbol, including ones from before this tool
existed. Clicking any pattern card isolates just that one on the chart (zoomed in, full
geometry) instead of drawing every forming pattern at once, which is what made them pile
up on top of each other.

The chart mounts once per REFRESH_SECONDS (when a real new dataset/pattern scan is ready)
via a self-contained components.html() block holding its own Plotly.js instance; the seconds
in between are ticked entirely client-side by JS polling Binance's public ticker endpoint
directly (CORS-open, confirmed via curl) and patching just the last candle with
Plotly.restyle/relayout -- no Streamlit rerun is involved in a tick, so nothing can remount
for that part.

A real declare_component()-based custom component (a single Plotly instance persisting across
every refresh via Plotly.react(), with zoom/pan preserved through Plotly's own `uirevision`)
was built and tested first, since it's the architecturally correct fix for this -- but hit a
confirmed, unresolved compatibility issue in this Streamlit version: the parent frontend's
ComponentInstance never acknowledges the component's componentReady handshake (verified with a
real headless-Chrome run showing the message arriving at the top-level window correctly, but
no streamlit:render response ever comes back, even after 25s of retries, even in the simplest
possible standalone reproduction with no fragment or navigation involved). Given that, the
practical fix here is smaller: REFRESH_SECONDS-driven redraws still remount a fresh iframe
(components.html()'s element identity is also content-hashed, so that part can't be avoided
either), but the user's manual zoom/pan/autoscale is captured on every Plotly `relayout` event
and stashed on `window.parent` -- which is NOT destroyed by a fragment rerun, only this iframe
is -- then restored as the opening view the next time this exact pattern selection mounts."""

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from _plotly_utils.utils import PlotlyJSONEncoder
from plotly.subplots import make_subplots

from core.config import app as app_config
from core.db import init_db
from core.ui import apply_dark_chart_theme, direction_badge, direction_color, page_header, pattern_label
from data.cache import refresh_candles
from data.watchlist import get_watchlist
from indicators.levels import find_fibonacci_levels, find_support_resistance
from indicators.technical import compute_indicators, trend_direction
from patterns.history import scan_forming_patterns_df, scan_symbol_history_df

BOTTOM_PANELS = ["RSI", "MACD", "Stochastic", "ADX"]
_DIVERGENCE_COL = {"RSI": "rsi", "MACD": "macd"}
FORMING_COLOR = "#F59E0B"
LONG_COLOR = "#22C55E"
SHORT_COLOR = "#EF4444"
# Near-grayscale slate family for reference overlays (SMA/VWAP/Fibonacci) -- hue is reserved
# for price direction, selection, and alert severity only; these are distinguished by weight
# and dash pattern instead of competing colors.
SMA20_COLOR = "#94A3B8"
SMA50_COLOR = "#64748B"
VWAP_COLOR = "#CBD5E1"
FIB_COLOR = "#7C879C"
REFRESH_SECONDS = 15
CLIENT_TICK_MS = 2000
PLOTLY_CDN = "https://cdn.plot.ly/plotly-3.7.0.min.js"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")

init_db()
page_header("Live Charts", "Every pattern this tool can see, confirmed or still forming, drawn in place and refreshing on its own.", icon="candlestick_chart")

watchlist = get_watchlist(enabled_only=True)
if not watchlist:
    st.warning("Watchlist is empty. Add symbols from Settings.")
    st.stop()

col1, col2, col3 = st.columns([2, 1, 1])
symbol = col1.selectbox("Symbol", watchlist)
timeframe = col2.selectbox("Timeframe", app_config.timeframes, index=0)
limit = col3.slider("Candles", min_value=150, max_value=1000, value=400, step=50)

oc1, oc2, oc3, oc4 = st.columns([1.3, 1, 1, 1])
bottom_panel = oc1.selectbox("Bottom panel", BOTTOM_PANELS, index=0)
show_vwap = oc2.toggle("VWAP", value=True)
show_sr = oc3.toggle("Support/Resistance", value=True)
show_fib = oc4.toggle("Fibonacci", value=True)


@st.cache_data(ttl=120, show_spinner=False)
def _multi_timeframe_trend(symbol: str, timeframes: tuple):
    def _one(tf: str) -> str:
        try:
            df_tf = compute_indicators(refresh_candles(symbol, tf, limit=60))
            return trend_direction(df_tf, len(df_tf) - 1) if not df_tf.empty else "flat"
        except Exception:
            return "flat"

    # These are independent network calls -- fetching all timeframes concurrently instead
    # of one after another turns ~6x a single round-trip into ~1x.
    with ThreadPoolExecutor(max_workers=len(timeframes)) as pool:
        results = dict(zip(timeframes, pool.map(_one, timeframes)))
    return results


_trend_colors = {"up": LONG_COLOR, "down": SHORT_COLOR, "flat": "#6B7280"}
_trend_glyphs = {"up": "▲", "down": "▼", "flat": "–"}
mtf_trends = _multi_timeframe_trend(symbol, tuple(app_config.timeframes))
ticker_cells = "".join(
    f'<span class="ticker-cell"><span class="ticker-label">{tf.upper()}</span>'
    f'<span class="ticker-value num" style="color:{_trend_colors[t]};">{_trend_glyphs[t]} {t.upper()}</span></span>'
    for tf, t in mtf_trends.items()
)
st.markdown(f'<div class="ticker-strip">{ticker_cells}</div>', unsafe_allow_html=True)

# A pattern selected from a card only makes sense for the symbol/timeframe it came from --
# switching either clears it so the chart doesn't try to zoom to a stale pattern.
if st.session_state.get("chart_context") != (symbol, timeframe):
    st.session_state["chart_context"] = (symbol, timeframe)
    st.session_state["selected_pattern_key"] = None


@st.cache_data(ttl=180, show_spinner=False)
def _load_pattern_history(symbol: str, timeframe: str, limit: int):
    """Confirmed past signals: expensive full-history scan, doesn't need to be on the
    fast auto-refresh cycle since it only changes when a new pattern actually completes.
    Also returns the candle frame used for the scan, so the history list can show real
    dates without needing the (fast, separately-cached) live frame."""
    df_ind = compute_indicators(refresh_candles(symbol, timeframe, limit=limit))
    return scan_symbol_history_df(df_ind, symbol, timeframe), df_ind


try:
    history, history_df = _load_pattern_history(symbol, timeframe, limit)
except Exception as exc:
    st.error(f"Failed to load pattern history for {symbol}: {exc}")
    history, history_df = [], None

history = sorted(history, key=lambda m: m["trigger_index"])
for m in history:
    m["key"] = f"{m['trigger_index']}_{m['pattern']}"
confirmed_by_key = {m["key"]: m for m in history}

st.markdown('<div id="chart-anchor"></div>', unsafe_allow_html=True)


def _decode_bdata(obj):
    """Undoes plotly.py's own array compaction: this installed version (7.x) stores
    numpy-backed trace/shape arrays internally as {"dtype": "f8", "bdata": "<base64>"} --
    fig.to_dict() already returns them in this compact binary form, not something a later
    serialization step invents. Verified with a real headless-Chrome run against the deployed
    page that Plotly.js does not decode this back into a usable array on its own:
    gd._fullData[0].close stayed a plain object after Plotly.newPlot, the y-scale never got
    real bounds, and the x-axis autoranged to a nonsense 1970-2030 span. Decoded back to a
    plain list here so it never reaches the browser in the undecodable form. Everything else
    (raw numpy arrays for datetime x-values, NaN/Inf floats from SMA/VWAP's warm-up period)
    is left as-is for PlotlyJSONEncoder to normalize afterward -- the same encoder plotly.py's
    own to_json() pipeline uses, so it already knows how to make all of that JSON-safe."""
    if isinstance(obj, dict):
        if set(obj.keys()) == {"dtype", "bdata"}:
            return np.frombuffer(base64.b64decode(obj["bdata"]), dtype=obj["dtype"]).tolist()
        return {k: _decode_bdata(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode_bdata(v) for v in obj]
    return obj


def _fig_to_json(fig: go.Figure) -> str:
    """fig.to_dict() -> bdata decode -> re-serialized through PlotlyJSONEncoder to normalize
    any remaining numpy/datetime objects and turn NaN/Inf into the spec-compliant `null`
    (Plotly's own to_json() pipeline uses this same encoder for exactly that reason -- plain
    JSON doesn't have a NaN/Infinity token)."""
    decoded = _decode_bdata(fig.to_dict())
    return json.dumps(decoded, cls=PlotlyJSONEncoder)


def _build_chart_html(fig: go.Figure, symbol: str, height: int, zoom_key: str | None) -> str:
    """Wraps a serialized figure in a self-contained Plotly.js block. Mounts fresh every call
    (components.html()'s element identity is content-hashed just like st.plotly_chart's, so a
    real persistent instance isn't achievable this way -- see the module docstring for why the
    properly persistent version isn't in use), so REFRESH_SECONDS-cadence redraws are still a
    real remount. What survives that remount is the user's own zoom/pan/autoscale: every
    Plotly `relayout` (drag-zoom, scroll-zoom, double-click autoscale) is captured and stashed
    on `window.parent` -- the actual Streamlit page, which a fragment rerun does NOT destroy,
    unlike this iframe -- keyed by `zoom_key`, and restored as the opening view next time this
    exact pattern selection mounts, instead of snapping back to Python's computed range.
    `zoom_key` is None in the default (nothing isolated) state so "Show full chart" always
    opens genuinely unzoomed rather than restoring a stale remembered range."""
    config = {"scrollZoom": True, "displaylogo": False, "responsive": False}
    fig_json = _fig_to_json(fig)
    safe_symbol = symbol if _SYMBOL_RE.match(symbol) else ""
    zoom_key_json = json.dumps(zoom_key)
    poll_js = (
        f"""
        var lastPrice = null;
        async function tick() {{
            try {{
                var resp = await fetch('https://api.binance.com/api/v3/ticker/price?symbol={safe_symbol}');
                var j = await resp.json();
                var price = parseFloat(j.price);
                if (!isFinite(price)) return;
                var gd = document.getElementById('ppchart');
                if (!gd || !gd.data) return;
                var closeArr = gd.data[0].close.slice();
                var highArr = gd.data[0].high.slice();
                var lowArr = gd.data[0].low.slice();
                var i = closeArr.length - 1;
                closeArr[i] = price;
                highArr[i] = Math.max(highArr[i], price);
                lowArr[i] = Math.min(lowArr[i], price);
                Plotly.restyle(gd, {{close: [closeArr], high: [highArr], low: [lowArr]}}, [0]);
                var up = lastPrice === null || price >= lastPrice;
                var tagColor = up ? '{LONG_COLOR}' : '{SHORT_COLOR}';
                var nAnn = (gd.layout.annotations || []).length;
                if (nAnn > 0) {{
                    var update = {{}};
                    update['annotations[' + (nAnn - 1) + '].y'] = price;
                    update['annotations[' + (nAnn - 1) + '].text'] = price < 1 ? price.toFixed(6) : price.toFixed(4);
                    update['annotations[' + (nAnn - 1) + '].bgcolor'] = tagColor;
                    Plotly.relayout(gd, update);
                }}
                lastPrice = price;
            }} catch (e) {{ /* transient network hiccup -- keep last known state, try again next tick */ }}
        }}
        if ('{safe_symbol}') {{
            tick();
            setInterval(tick, {CLIENT_TICK_MS});
        }}
        """
    )
    return f"""
    <div id="ppchart" style="width:100%; height:{height}px;"></div>
    <script src="{PLOTLY_CDN}"></script>
    <script>
        var figSpec = {fig_json};
        var zoomKey = {zoom_key_json};
        var ppEl = document.getElementById('ppchart');

        if (zoomKey) {{
            try {{
                var saved = window.parent.__ppZoom && window.parent.__ppZoom[zoomKey];
                if (saved) {{
                    // The 3 stacked subplots (price/volume/bottom panel) share their x-axis via
                    // Plotly's `matches` linking (xaxis/xaxis2 both matches:"x3"), so xaxis3 is
                    // the actual range authority -- overriding xaxis alone is silently ignored.
                    // Confirmed by inspecting a real generated figSpec: matches was "x3" on the
                    // two upper axes. Setting the range on all of them is the robust fix.
                    Object.keys(figSpec.layout).forEach(function (k) {{
                        if (k === 'xaxis' || /^xaxis\\d+$/.test(k)) {{
                            figSpec.layout[k] = Object.assign({{}}, figSpec.layout[k], {{range: saved, autorange: false}});
                        }}
                    }});
                }}
            }} catch (e) {{}}
        }}

        Plotly.newPlot(ppEl, figSpec.data, figSpec.layout, {json.dumps(config)}).then(function(gd) {{
            requestAnimationFrame(function() {{ Plotly.Plots.resize(gd); }});
            if (zoomKey) {{
                gd.on('plotly_relayout', function() {{
                    try {{
                        var xr = gd._fullLayout.xaxis.range;
                        window.parent.__ppZoom = window.parent.__ppZoom || {{}};
                        window.parent.__ppZoom[zoomKey] = [xr[0], xr[1]];
                    }} catch (e) {{}}
                }});
            }}
        }});
        window.addEventListener('resize', function() {{
            var gd = document.getElementById('ppchart');
            if (gd) Plotly.Plots.resize(gd);
        }});
        {poll_js}
    </script>
    """


@st.fragment(run_every=REFRESH_SECONDS)
def _live_chart():
    """Rebuilds candle data + the forming-pattern scan on a REFRESH_SECONDS cadence -- this
    is a deliberate, honest redraw (new bars, new detector output) rather than the old
    every-second full remount. Everything selectable/isolated -- confirmed history, forming
    patterns -- lives in this same fragment so a click updates the chart and both card
    lists together."""

    @st.cache_data(ttl=REFRESH_SECONDS - 3, show_spinner=False)
    def _load_full_data(symbol: str, timeframe: str, limit: int):
        df_ind = compute_indicators(refresh_candles(symbol, timeframe, limit=limit))
        forming = scan_forming_patterns_df(df_ind, symbol, timeframe)
        return df_ind, forming

    try:
        df, forming = _load_full_data(symbol, timeframe, limit)
    except Exception as exc:
        st.error(f"Failed to fetch/analyze {symbol}: {exc}")
        return

    if df.empty:
        st.info("No candle data yet.")
        return

    last_idx = len(df) - 1
    for m in forming:
        m["key"] = f"forming_{m['pattern']}_{m['direction']}"
    forming_by_key = {m["key"]: m for m in forming}

    # Resolve what (if anything) is isolated on the chart right now. A forming-pattern
    # selection takes priority over a confirmed one if somehow both keys were stale; the
    # default (nothing clicked) features the most recent confirmed signal, matching the
    # dashboard's usual "most relevant recent thing" convention.
    selected_key = st.session_state.get("selected_pattern_key")
    if selected_key in forming_by_key:
        featured_kind, featured_key, featured_match = "forming", selected_key, forming_by_key[selected_key]
        zoom_to_featured = True
    elif selected_key in confirmed_by_key:
        featured_kind, featured_key, featured_match = "confirmed", selected_key, confirmed_by_key[selected_key]
        zoom_to_featured = True
    elif history:
        featured_key = history[-1]["key"]
        featured_kind, featured_match = "confirmed", confirmed_by_key[featured_key]
        zoom_to_featured = False
    else:
        featured_kind = featured_key = featured_match = None
        zoom_to_featured = False

    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    live_col, reset_col = st.columns([5, 1])
    if zoom_to_featured and featured_match:
        live_col.caption(
            f"LIVE &middot; {now_str} &middot; zoomed on **{pattern_label(featured_match['pattern'])}** "
            f"&middot; click below to view another or reset"
        )
        if reset_col.button("Show full chart", use_container_width=True):
            st.session_state["selected_pattern_key"] = None
            st.rerun()
    else:
        live_col.caption(f"LIVE &middot; {now_str} &middot; {len(forming)} pattern(s) forming right now")

    # ---- Chart ----------------------------------------------------------------
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.18, 0.22], vertical_spacing=0.03,
        subplot_titles=("Price & patterns", "Volume", bottom_panel),
    )

    fig.add_trace(
        go.Candlestick(
            x=df["dt"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name="Price", increasing_line_color=LONG_COLOR, decreasing_line_color=SHORT_COLOR,
        ),
        row=1, col=1,
    )
    fig.add_trace(go.Scatter(x=df["dt"], y=df["sma20"], name="SMA20", line=dict(width=1, color=SMA20_COLOR, dash="solid")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["sma50"], name="SMA50", line=dict(width=1.3, color=SMA50_COLOR, dash="dash")), row=1, col=1)
    if show_vwap:
        fig.add_trace(go.Scatter(x=df["dt"], y=df["vwap"], name="VWAP", line=dict(width=1.2, color=VWAP_COLOR, dash="dot")), row=1, col=1)

    if show_sr:
        for zone in find_support_resistance(df)[:6]:
            zone_color = LONG_COLOR if zone["kind"] == "support" else SHORT_COLOR
            fig.add_hline(
                y=zone["price"], line_color=zone_color, line_width=1, line_dash="dot", opacity=0.45,
                annotation_text=f"{zone['kind'][:1].upper()} x{zone['touches']}", annotation_position="right",
                annotation_font=dict(size=10, color=zone_color, family="JetBrains Mono, monospace"), row=1, col=1,
            )

    if show_fib:
        fib = find_fibonacci_levels(df)
        if fib:
            x0 = df["dt"].iloc[min(max(fib["start_index"], 0), last_idx)]
            for lvl in fib["levels"]:
                fig.add_shape(
                    type="line", x0=x0, x1=df["dt"].iloc[last_idx], y0=lvl["price"], y1=lvl["price"],
                    line=dict(color=FIB_COLOR, width=1, dash="dot"), opacity=0.55, row=1, col=1,
                )
                fig.add_annotation(
                    x=df["dt"].iloc[last_idx], y=lvl["price"], text=f"{lvl['ratio']*100:.1f}%", showarrow=False,
                    xanchor="left", font=dict(size=9, color=FIB_COLOR, family="JetBrains Mono, monospace"), row=1, col=1,
                )

    # -- confirmed historical patterns: an outlined region (~8% fill, thin severity-colored
    # border) plus the actual geometry. No floating text label -- the name/confidence lives
    # in the "Pattern history" panel below, reachable via hover here in the meantime. --
    for match in history:
        is_featured = featured_kind == "confirmed" and match["key"] == featured_key
        color = direction_color(match["direction"])
        opacity = 1.0 if is_featured else 0.22
        width = 3.5 if is_featured else 1.1
        dash = "solid" if is_featured else "dot"

        if is_featured:
            all_idxs = [p["index"] for p in match.get("key_points", [])] + [match["trigger_index"]]
            x0 = df["dt"].iloc[min(max(min(all_idxs) - 1, 0), last_idx)]
            x1 = df["dt"].iloc[min(max(match["trigger_index"] + 1, 0), last_idx)]
            fig.add_vrect(
                x0=x0, x1=x1, fillcolor=color, opacity=0.08, layer="below",
                line=dict(color=color, width=1), row=1, col=1,
            )

        for line in match.get("trend_lines", []):
            idxs = [min(max(p[0], 0), last_idx) for p in line["points"]]
            fig.add_trace(
                go.Scatter(
                    x=[df["dt"].iloc[idx] for idx in idxs], y=[p[1] for p in line["points"]],
                    mode="lines", line=dict(color=color, width=width, dash=dash),
                    opacity=opacity, showlegend=False, hoverinfo="skip",
                ),
                row=1, col=1,
            )

        ind_line = match.get("indicator_line")
        if ind_line and _DIVERGENCE_COL.get(bottom_panel) == ind_line["row"]:
            idxs = [min(max(p[0], 0), last_idx) for p in ind_line["points"]]
            fig.add_trace(
                go.Scatter(
                    x=[df["dt"].iloc[idx] for idx in idxs], y=[p[1] for p in ind_line["points"]],
                    mode="lines+markers", line=dict(color=color, width=width, dash=dash),
                    marker=dict(color=color, size=6 if is_featured else 4), opacity=opacity,
                    showlegend=False, hoverinfo="skip",
                ),
                row=3, col=1,
            )

        if match.get("key_points"):
            pxs = [df["dt"].iloc[min(max(p["index"], 0), last_idx)] for p in match["key_points"]]
            pys = [p["price"] for p in match["key_points"]]
            ptext = [p["label"] for p in match["key_points"]]
            fig.add_trace(
                go.Scatter(
                    x=pxs, y=pys, mode="markers",
                    marker=dict(color=color, size=9 if is_featured else 5, opacity=opacity,
                                line=dict(width=1.5, color="#E7EAF2") if is_featured else None),
                    showlegend=False, hovertext=ptext, hoverinfo="text",
                ),
                row=1, col=1,
            )

        trigger_idx = min(max(match["trigger_index"], 0), last_idx)
        trigger_x, trigger_y = df["dt"].iloc[trigger_idx], match["entry_price"]
        symbol_shape = "triangle-up" if match["direction"] == "long" else "triangle-down"

        fig.add_trace(
            go.Scatter(
                x=[trigger_x], y=[trigger_y], mode="markers",
                marker=dict(symbol=symbol_shape, size=16 if is_featured else 9, color=color, opacity=opacity,
                            line=dict(width=1.5, color="#E7EAF2") if is_featured else dict(width=1, color=color)),
                showlegend=False,
                hovertext=f"{pattern_label(match['pattern'])} ({match['direction'].upper()})<br>"
                          f"Confidence {match['confidence']*100:.0f}%<br>Entry {match['entry_price']:.4f}",
                hoverinfo="text",
            ),
            row=1, col=1,
        )

    # -- forming patterns: only the isolated one gets full geometry drawn. All forming
    # patterns trigger at the same current bar, so drawing every one of them at once is
    # exactly what caused them to pile on top of each other -- isolate on click instead.
    # Same outlined-region treatment as confirmed patterns; description text lives in the
    # "Forming now" panel below, not as a floating box on the chart. --
    featured_forming = featured_match if featured_kind == "forming" else None
    if featured_forming:
        match = featured_forming
        idxs = [p["index"] for p in match.get("key_points", [])] + [match["trigger_index"]]
        x0 = df["dt"].iloc[min(max(min(idxs) - 1, 0), last_idx)]
        x1 = df["dt"].iloc[min(max(match["trigger_index"] + 1, 0), last_idx)]
        fig.add_vrect(
            x0=x0, x1=x1, fillcolor=FORMING_COLOR, opacity=0.08, layer="below",
            line=dict(color=FORMING_COLOR, width=1), row=1, col=1,
        )

        for line in match.get("trend_lines", []):
            line_idxs = [min(max(p[0], 0), last_idx) for p in line["points"]]
            fig.add_trace(
                go.Scatter(
                    x=[df["dt"].iloc[idx] for idx in line_idxs], y=[p[1] for p in line["points"]],
                    mode="lines", line=dict(color=FORMING_COLOR, width=2.5, dash="dash"),
                    showlegend=False, hoverinfo="skip",
                ),
                row=1, col=1,
            )

        if match.get("key_points"):
            pxs = [df["dt"].iloc[min(max(p["index"], 0), last_idx)] for p in match["key_points"]]
            pys = [p["price"] for p in match["key_points"]]
            ptext = [p["label"] for p in match["key_points"]]
            fig.add_trace(
                go.Scatter(
                    x=pxs, y=pys, mode="markers",
                    marker=dict(color=FORMING_COLOR, size=10, symbol="diamond", line=dict(width=1.5, color="#E7EAF2")),
                    showlegend=False,
                    hovertext=f"{pattern_label(match['pattern'])}: {ptext[0] if ptext else ''}" if ptext else pattern_label(match["pattern"]),
                    hoverinfo="text",
                ),
                row=1, col=1,
            )

    vol_colors = [LONG_COLOR if c >= o else SHORT_COLOR for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df["dt"], y=df["volume"], marker_color=vol_colors, name="Volume", opacity=0.6), row=2, col=1)

    if bottom_panel == "RSI":
        fig.add_trace(go.Scatter(x=df["dt"], y=df["rsi"], name="RSI", line=dict(width=1.3, color="#9CA8BC")), row=3, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color=SHORT_COLOR, opacity=0.4, row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color=LONG_COLOR, opacity=0.4, row=3, col=1)
    elif bottom_panel == "MACD":
        fig.add_trace(go.Scatter(x=df["dt"], y=df["macd"], name="MACD", line=dict(width=1.3, color="#9CA8BC")), row=3, col=1)
        fig.add_trace(go.Scatter(x=df["dt"], y=df["macd_signal"], name="Signal", line=dict(width=1.3, color=SMA50_COLOR, dash="dash")), row=3, col=1)
        hist_colors = [LONG_COLOR if v >= 0 else SHORT_COLOR for v in df["macd_diff"].fillna(0)]
        fig.add_trace(go.Bar(x=df["dt"], y=df["macd_diff"], name="Histogram", marker_color=hist_colors, opacity=0.5), row=3, col=1)
    elif bottom_panel == "Stochastic":
        fig.add_trace(go.Scatter(x=df["dt"], y=df["stoch_k"], name="%K", line=dict(width=1.3, color="#9CA8BC")), row=3, col=1)
        fig.add_trace(go.Scatter(x=df["dt"], y=df["stoch_d"], name="%D", line=dict(width=1.3, color=SMA50_COLOR, dash="dash")), row=3, col=1)
        fig.add_hline(y=80, line_dash="dot", line_color=SHORT_COLOR, opacity=0.4, row=3, col=1)
        fig.add_hline(y=20, line_dash="dot", line_color=LONG_COLOR, opacity=0.4, row=3, col=1)
    elif bottom_panel == "ADX":
        fig.add_trace(go.Scatter(x=df["dt"], y=df["adx"], name="ADX", line=dict(width=1.6, color="#E7EAF2")), row=3, col=1)
        fig.add_trace(go.Scatter(x=df["dt"], y=df["di_plus"], name="+DI", line=dict(width=1.1, color=LONG_COLOR)), row=3, col=1)
        fig.add_trace(go.Scatter(x=df["dt"], y=df["di_minus"], name="-DI", line=dict(width=1.1, color=SHORT_COLOR)), row=3, col=1)
        fig.add_hline(y=25, line_dash="dot", line_color="#6B7280", opacity=0.4, row=3, col=1)

    apply_dark_chart_theme(fig, height=680)
    fig.update_layout(
        xaxis_rangeslider_visible=False, showlegend=True, legend=dict(orientation="h", y=1.06),
        dragmode="pan",
    )

    if zoom_to_featured and featured_match:
        all_idxs = [p["index"] for p in featured_match.get("key_points", [])] + [featured_match["trigger_index"]]
        span = max(max(all_idxs) - min(all_idxs), 5)
        pad = max(int(span * 0.6), 8)
        x_start = df["dt"].iloc[min(max(min(all_idxs) - pad, 0), last_idx)]
        x_end = df["dt"].iloc[min(max(featured_match["trigger_index"] + pad, 0), last_idx)]
        fig.update_xaxes(range=[x_start, x_end])

    # Live price tag on the y-axis: a filled rectangle (annotation bgcolor) that always sits
    # at the current price, colored by tick direction. Added last so its index in the
    # annotations array is deterministic for the client-side tick loop to target.
    last_close = float(df["close"].iloc[last_idx])
    price_text = f"{last_close:.6f}" if last_close < 1 else f"{last_close:.4f}"
    fig.add_annotation(
        x=1.0, xref="x domain", xanchor="left", y=last_close, yref="y", yanchor="middle",
        text=price_text, showarrow=False,
        font=dict(size=11, color="#0B0F19", family="JetBrains Mono, monospace"),
        bgcolor=LONG_COLOR, borderpad=4, bordercolor="rgba(0,0,0,0)",
        row=1, col=1,
    )

    # Zoom persistence only applies to an explicitly isolated pattern -- keyed on the user's
    # own selection, not the resolved featured_key (which can drift on its own as new
    # patterns appear) -- so "Show full chart" always opens genuinely unzoomed instead of
    # restoring a stale remembered range from the default state.
    zoom_key = f"{symbol}_{timeframe}_{selected_key}" if selected_key else None
    components.html(_build_chart_html(fig, symbol, height=680, zoom_key=zoom_key), height=700, scrolling=False)

    if st.session_state.pop("scroll_to_chart", False):
        components.html(
            """<script>
            var el = window.parent.document.getElementById('chart-anchor');
            if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); }
            </script>""",
            height=0,
        )

    # ---- Forming patterns panel ----------------------------------------------------------------
    if forming:
        st.markdown("##### Forming now")
        st.caption("Click any of these to isolate it on the chart above. Otherwise they'd all draw on top of each other.")
        for match in forming:
            is_featured = match["key"] == featured_key and featured_kind == "forming"
            card_col, btn_col = st.columns([6, 1])
            with card_col:
                st.markdown(
                    f"""<div class="card card-hover" style="border-left: 2px solid {FORMING_COLOR};
                        {'box-shadow: inset 0 0 0 1px ' + FORMING_COLOR + ';' if is_featured else ''}">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="pattern-title">{pattern_label(match['pattern'])}</div>
                                <div class="pattern-meta">{match['missing']}</div>
                                <div class="pattern-meta num">win rate {match['historical_win_rate']*100:.0f}% &middot; n={match['sample_size']}</div>
                            </div>
                            <div style="text-align:right;">
                                {direction_badge(match['direction'])}
                                <div class="mini-value num" style="color:{FORMING_COLOR}; margin-top:0.3rem;">{match['progress']*100:.0f}%</div>
                            </div>
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with btn_col:
                st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)
                label = "Shown" if is_featured else "Isolate"
                icon = ":material/check:" if is_featured else ":material/center_focus_strong:"
                if st.button(label, icon=icon, key=f"btn_{match['key']}", disabled=is_featured, use_container_width=True):
                    st.session_state["selected_pattern_key"] = match["key"]
                    st.session_state["scroll_to_chart"] = True
                    st.rerun()

    # ---- Confirmed history panel ----------------------------------------------------------------
    st.markdown(f"##### Pattern history for {symbol} ({timeframe})")
    if not history:
        st.caption("No qualifying confirmed pattern occurrences found in the loaded history.")
    else:
        st.caption(f"{len(history)} confirmed pattern occurrence(s) found, including any from before this tool existed. Most recent first.")
        last_hist_idx = len(history_df) - 1 if history_df is not None else 0
        for match in reversed(history):
            accent = "card-accent-long" if match["direction"] == "long" else "card-accent-short"
            is_featured = featured_kind == "confirmed" and match["key"] == featured_key
            when_str = ""
            if history_df is not None:
                idx = min(max(match["trigger_index"], 0), last_hist_idx)
                when_str = history_df["dt"].iloc[idx].strftime("%Y-%m-%d %H:%M UTC") + " &middot; "
            card_col, btn_col = st.columns([6, 1])
            with card_col:
                st.markdown(
                    f"""<div class="card card-hover {accent}" style="{'box-shadow: inset 0 0 0 1px ' + direction_color(match['direction']) + ';' if is_featured else ''}">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="pattern-title">{pattern_label(match['pattern'])}</div>
                                <div class="pattern-meta num">{when_str}entry {match['entry_price']:.4f}</div>
                            </div>
                            <div style="text-align:right;">
                                {direction_badge(match['direction'])}
                                <div class="pattern-meta num">confidence {match['confidence']*100:.0f}%</div>
                            </div>
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with btn_col:
                st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)
                label = "Shown" if is_featured else "View"
                icon = ":material/check:" if is_featured else ":material/visibility:"
                if st.button(label, icon=icon, key=f"btn_{match['key']}", disabled=is_featured, use_container_width=True):
                    st.session_state["selected_pattern_key"] = match["key"]
                    st.session_state["scroll_to_chart"] = True
                    st.rerun()


_live_chart()
