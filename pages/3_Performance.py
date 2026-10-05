"""How well past signals have actually done -- by pattern, timeframe and symbol, plus
an equity-if-followed curve. This is the visible half of the learning loop."""

import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.config import learning as learning_config
from core.db import get_db, init_db
from core.ui import apply_dark_chart_theme, page_header, pattern_label
from learning.ml_model import MODEL_PATH, get_validation_metrics, train_model, training_sample_count
from learning.paper_portfolio import compute_paper_history, get_starting_capital

init_db()
page_header("Performance", "What actually happened after past signals, whether or not you followed them.", icon="monitoring")

conn = get_db()

st.markdown("##### Learning diagnostics")
sample_count = training_sample_count()
model_trained = os.path.exists(MODEL_PATH)
val = get_validation_metrics()
lc1, lc2, lc3, lc4 = st.columns([1, 1, 1, 1])
lc1.metric("Labeled samples", f"{sample_count} / {learning_config.ml_training_threshold}")
lc2.metric("ML model", "Trained ✓" if model_trained else "Not yet trained")
lc3.metric(
    "Holdout AUC", f"{val['valid_auc']:.3f}" if val["valid_auc"] is not None else "n/a",
    help=(
        "Out-of-sample accuracy on the most recent slice of history, held out from training "
        "entirely -- not measured on data the model trained on. 0.50 = no better than a coin "
        "flip. 1.00 = perfect separation of wins from losses. This is the honest test of "
        "whether the model is predictive; 'Trained' alone doesn't tell you that."
    ),
)
lc4.markdown("<div style='height:1.85rem;'></div>", unsafe_allow_html=True)
if lc4.button("Train / retrain now", icon=":material/model_training:", use_container_width=True):
    with st.spinner("Training..."):
        model = train_model()
    if model is None:
        st.warning(f"Still below the {learning_config.ml_training_threshold}-sample threshold. Keep scanning and resolving signals.")
    else:
        val = get_validation_metrics()
        auc_note = f" Holdout AUC: {val['valid_auc']:.3f} on {val['valid_samples']} unseen samples." if val["valid_auc"] is not None else ""
        st.success(f"Model trained and saved.{auc_note}")
        if val["valid_auc"] is not None and val["valid_auc"] < 0.55:
            st.warning(
                "That AUC is close to 0.50 -- barely better than a coin flip on data it didn't "
                "train on. Confidence scores from this model shouldn't be trusted much yet."
            )
st.caption(
    "Below the threshold, every signal's confidence comes purely from the stats-based pattern "
    "win-rates (now shrunk hierarchically -- a thin symbol-specific sample borrows strength "
    "from that pattern's broader performance across timeframes/symbols instead of collapsing "
    "straight to a 50/50 guess). After the threshold, each new signal also blends in the ML "
    "model's prediction, visible per-signal as `stats_confidence` vs `ml_win_probability`."
)
st.divider()

outcomes = conn.execute(
    """
    SELECT s.id, s.symbol, s.timeframe, s.pattern_type, s.direction, s.confidence,
           s.position_size_suggested, s.portfolio_size_at_signal, o.outcome, o.return_pct, o.resolved_at
    FROM outcomes o JOIN signals s ON s.id = o.signal_id
    ORDER BY o.resolved_at ASC
    """
).fetchall()
df = pd.DataFrame([dict(r) for r in outcomes])

open_count = conn.execute("SELECT COUNT(*) AS c FROM signals WHERE status = 'open'").fetchone()["c"]

if df.empty:
    st.info(
        f"No resolved signals yet ({open_count} still open). Outcomes are filled in by the "
        "background outcome-tracking job once enough time has passed for each signal's timeframe."
    )
    st.stop()

col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 0.8])
col1.metric("Resolved signals", len(df))
col2.metric("Win rate", f"{(df['outcome'] == 'win').mean() * 100:.1f}%")
col3.metric("Avg return / trade", f"{df['return_pct'].mean():.2f}%")
col4.metric("Still open", open_count)
col5.markdown("<div style='height:1.85rem;'></div>", unsafe_allow_html=True)
col5.download_button("CSV", df.to_csv(index=False), file_name="resolved_signals.csv", icon=":material/download:", use_container_width=True)

st.markdown("##### Equity if every signal had been followed")
df["pnl"] = df["position_size_suggested"] * (df["return_pct"] / 100)
df["cumulative_pnl"] = df["pnl"].cumsum()
fig = px.area(df, x="resolved_at", y="cumulative_pnl")
fig.update_traces(line_color="#6366F1", fillcolor="rgba(99,102,241,0.15)")
apply_dark_chart_theme(fig, height=320)
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "Position size here is whatever the live risk settings actually were at the moment each "
    "signal fired -- useful for judging dollar P&L as it really happened, not for comparing "
    "against a specific starting balance. Use the simulator below for that."
)

st.divider()
st.markdown("##### Backtest simulator")
sim_capital = get_starting_capital()
st.caption(
    f"Replays every resolved signal since the very first one, in the order it actually "
    f"resolved, compounding from a &euro;{sim_capital:,.2f} starting balance the same way live "
    "risk sizing does, scaled by conviction (bigger size for higher-confidence signals). This "
    "is the same automatic paper portfolio shown on the Positions page (change the starting "
    "balance there) -- also marks the point the ML model crossed its training threshold, so "
    "you can see performance before vs. after the model started blending into confidence scores.",
    unsafe_allow_html=True,
)

sim_df = compute_paper_history(sim_capital)
sim_df["resolved_at"] = pd.to_datetime(sim_df["resolved_at"])

final_balance = sim_df["simulated_balance"].iloc[-1]
total_return_pct = (final_balance - sim_capital) / sim_capital * 100
running_max = sim_df["simulated_balance"].cummax()
max_drawdown_pct = ((sim_df["simulated_balance"] - running_max) / running_max * 100).min()

sc1, sc2, sc3, sc4 = st.columns(4)
sc1.metric("Starting capital", f"€{sim_capital:,.2f}")
sc2.metric("Balance today", f"€{final_balance:,.2f}", f"{total_return_pct:+.1f}%")
sc3.metric("Max drawdown", f"{max_drawdown_pct:.1f}%")
sc4.metric("Trades replayed", len(sim_df))

threshold = learning_config.ml_training_threshold
trained_at = sim_df["resolved_at"].iloc[threshold - 1] if len(sim_df) >= threshold else None

fig_sim = px.area(sim_df, x="resolved_at", y="simulated_balance")
fig_sim.update_traces(line_color="#6366F1", fillcolor="rgba(99,102,241,0.15)")
apply_dark_chart_theme(fig_sim, height=340)
fig_sim.add_hline(
    y=sim_capital, line_dash="dot", line_color="#6B7280", opacity=0.6,
    annotation_text="Starting capital", annotation_position="right",
    annotation_font=dict(size=10, color="#8B93A7"),
)
if trained_at is not None:
    fig_sim.add_vline(
        x=trained_at, line_dash="dash", line_color="#F59E0B",
        annotation_text="ML model fully trained", annotation_position="top",
        annotation_font=dict(size=10, color="#F59E0B"),
    )
st.plotly_chart(fig_sim, use_container_width=True)

if trained_at is None:
    st.caption(
        f"Model not yet fully trained across this history: {len(sim_df)}/{threshold} resolved "
        "signals so far -- the whole curve above reflects the stats-only phase."
    )
else:
    pre_mask = sim_df["resolved_at"] < trained_at
    pre_win = (sim_df.loc[pre_mask, "outcome"] == "win").mean() * 100 if pre_mask.any() else None
    post_win = (sim_df.loc[~pre_mask, "outcome"] == "win").mean() * 100 if (~pre_mask).any() else None
    tc1, tc2 = st.columns(2)
    tc1.metric("Win rate before ML (stats-only)", f"{pre_win:.1f}%" if pre_win is not None else "n/a")
    tc2.metric("Win rate after ML kicked in", f"{post_win:.1f}%" if post_win is not None else "n/a")

st.divider()
st.markdown("##### Win rate by pattern")
stats_rows = conn.execute("SELECT * FROM pattern_stats ORDER BY total DESC").fetchall()
stats_df = pd.DataFrame([dict(r) for r in stats_rows])
if not stats_df.empty:
    by_pattern = stats_df.groupby("pattern_type").apply(
        lambda g: pd.Series({
            "total": g["total"].sum(),
            "win_rate": (g["wins"].sum() / g["total"].sum()) if g["total"].sum() else 0,
            "avg_return": (g["avg_return"] * g["total"]).sum() / g["total"].sum() if g["total"].sum() else 0,
        }),
        include_groups=False,
    ).reset_index().sort_values("total", ascending=False)
    by_pattern["label"] = by_pattern["pattern_type"].map(pattern_label)
    fig2 = px.bar(by_pattern, x="label", y="win_rate", hover_data=["total", "avg_return"])
    fig2.update_traces(marker_color="#6366F1")
    apply_dark_chart_theme(fig2, height=320)
    fig2.update_layout(yaxis_tickformat=".0%", xaxis_title=None, yaxis_title="Win rate")
    st.plotly_chart(fig2, use_container_width=True)

    with st.expander("Detail: pattern x timeframe x symbol"):
        st.dataframe(stats_df, use_container_width=True)
else:
    st.caption("No pattern statistics yet.")

st.divider()
st.markdown("##### Confidence calibration")
st.caption(
    "The real test of whether 'confidence' means what it claims: if it's honest, signals the "
    "tool rated ~70% confident should win close to 70% of the time. Points on the dashed "
    "diagonal are calibrated; points below it mean the tool has been overconfident at that "
    "level, points above mean it's been underconfident."
)
calib_df = df.copy()
calib_df["confidence_bucket"] = pd.cut(
    calib_df["confidence"], bins=[0.5, 0.6, 0.7, 0.8, 0.9, 1.0], include_lowest=True
)
calib = calib_df.groupby("confidence_bucket", observed=True).agg(
    predicted=("confidence", "mean"), actual=("outcome", lambda s: (s == "win").mean()), n=("outcome", "size"),
).reset_index()
calib = calib[calib["n"] >= 3]  # a bucket with 1-2 signals isn't a meaningful calibration read

if calib.empty:
    st.caption("Not enough resolved signals yet spread across enough different confidence levels for a meaningful calibration read.")
else:
    fig_cal = go.Figure()
    fig_cal.add_trace(go.Scatter(
        x=[0.5, 1.0], y=[0.5, 1.0], mode="lines", line=dict(dash="dot", color="#6B7280"),
        name="Perfectly calibrated", hoverinfo="skip",
    ))
    fig_cal.add_trace(go.Scatter(
        x=calib["predicted"], y=calib["actual"], mode="markers+text",
        marker=dict(size=(calib["n"].clip(upper=30) + 10), color="#6366F1", line=dict(width=1, color="#818CF8")),
        text=[f"n={n}" for n in calib["n"]], textposition="top center",
        textfont=dict(size=10, color="#8B93A7", family="JetBrains Mono, monospace"),
        name="Actual vs. predicted", hovertemplate="predicted %{x:.0%}<br>actual %{y:.0%}<extra></extra>",
    ))
    apply_dark_chart_theme(fig_cal, height=340)
    fig_cal.update_layout(
        xaxis_title="Predicted confidence", yaxis_title="Actual win rate",
        xaxis=dict(range=[0.5, 1.02], tickformat=".0%"), yaxis=dict(range=[0, 1.02], tickformat=".0%"),
        showlegend=False,
    )
    st.plotly_chart(fig_cal, use_container_width=True)
