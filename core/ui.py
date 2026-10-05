"""Shared visual language: colors, CSS, and small HTML-snippet helpers used by every page
so the app reads as one designed product instead of five separate Streamlit scripts.

Design direction: a data terminal, not a consumer dashboard. Color is reserved strictly for
three meanings -- price direction (green/red), selection/focus (the single accent color),
and alert severity (amber/red) -- everything else (chrome, nav, borders, inactive states) is
grayscale. Hierarchy comes from typography and line weight instead: Inter for UI chrome and
labels, JetBrains Mono for every number (prices, percentages, timestamps) so figures read in
aligned columns the way they do on a real trading terminal. No drop shadows anywhere -- panel
separation is a 1px hairline border at low opacity, nothing else."""

import streamlit as st

APP_NAME = "PatternPulse"
APP_ICON = "sensors"  # Material Symbols name -- used wherever a real icon glyph is needed

LONG_COLOR = "#22C55E"
SHORT_COLOR = "#EF4444"
NEUTRAL_COLOR = "#8B93A7"
ACCENT_COLOR = "#6366F1"
BG_COLOR = "#0A0C11"
BG_ELEVATED = "#0D0F15"
CARD_BG = "rgba(255,255,255,0.025)"
CARD_BG_HOVER = "rgba(255,255,255,0.045)"
CARD_BORDER = "rgba(255,255,255,0.08)"
TEXT_PRIMARY = "#E5E7EE"

_CSS = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    .num, .mini-value, .pattern-meta, [data-testid="stMetricValue"], [data-testid="stMetricDelta"],
    div[data-testid="stDataFrame"] * , .ticker-strip, code {{
        font-family: 'JetBrains Mono', 'IBM Plex Mono', monospace !important;
        font-variant-numeric: tabular-nums;
    }}

    div[data-testid="stAppViewContainer"] {{ background: {BG_COLOR}; }}
    section[data-testid="stMain"] {{ background: transparent; }}

    * {{ transition: background-color 0.12s ease, border-color 0.12s ease, color 0.12s ease; }}

    ::-webkit-scrollbar {{ width: 9px; height: 9px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.1); border-radius: 6px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: rgba(255,255,255,0.18); }}

    /* ---- Sidebar / navigation: flat, grayscale, hairline separation only -------------- */
    section[data-testid="stSidebar"] {{
        background: {BG_ELEVATED};
        border-right: 1px solid rgba(255,255,255,0.07);
    }}
    section[data-testid="stSidebar"] > div {{ padding-top: 0.25rem; }}

    .sidebar-brand {{
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.75rem 1rem 0.75rem 1rem;
        margin-bottom: 0.25rem;
        border-bottom: 1px solid rgba(255,255,255,0.07);
    }}
    .sidebar-brand .mark {{
        width: 26px; height: 26px;
        border-radius: 5px;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.8rem;
        font-weight: 800;
        font-family: 'JetBrains Mono', monospace;
        color: {ACCENT_COLOR};
        background: rgba(99,102,241,0.1);
        border: 1px solid rgba(99,102,241,0.4);
    }}
    .sidebar-brand .name {{
        font-weight: 700;
        font-size: 0.95rem;
        letter-spacing: -0.01em;
        color: {TEXT_PRIMARY};
        line-height: 1.1;
    }}
    .sidebar-brand .tag {{
        font-size: 0.63rem;
        font-weight: 600;
        letter-spacing: 0.09em;
        text-transform: uppercase;
        color: #5B6478;
    }}

    section[data-testid="stSidebar"] [data-testid="stNavSectionHeader"] {{
        font-size: 0.63rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.12em !important;
        text-transform: uppercase;
        color: #4B5364 !important;
        padding: 0.9rem 1rem 0.3rem 1rem !important;
    }}
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {{
        border-radius: 0;
        margin: 0 0.5rem;
        padding: 0.44rem 0.5rem !important;
        color: #A7AEBE !important;
        font-weight: 500;
        font-size: 0.86rem;
        border-left: 1px solid transparent;
        background: transparent !important;
    }}
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover {{
        color: {TEXT_PRIMARY} !important;
        border-left: 1px solid rgba(255,255,255,0.25);
    }}
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] {{
        background: transparent !important;
        border-left: 1px solid {ACCENT_COLOR};
        color: {TEXT_PRIMARY} !important;
        font-weight: 600;
    }}
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] svg {{ opacity: 0.85; }}

    /* ---- Header / branding ---------------------------------------------------- */
    .brand-eyebrow {{
        display: flex;
        align-items: center;
        gap: 0.4rem;
        font-size: 0.68rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: #6B7280;
        margin-bottom: 0.4rem;
    }}
    .brand-eyebrow .dot {{
        width: 5px; height: 5px; border-radius: 50%;
        background: {LONG_COLOR};
    }}

    div[data-testid="stMarkdownContainer"] h1 {{
        font-size: 1.4rem !important;
        font-weight: 700 !important;
        letter-spacing: -0.01em;
        color: {TEXT_PRIMARY} !important;
        margin: 0 0 0.2rem 0 !important;
    }}
    div[data-testid="stMarkdownContainer"] h1 [data-testid="stHeadingIcon"] {{
        color: {NEUTRAL_COLOR};
        vertical-align: -2px;
    }}
    div[data-testid="stMarkdownContainer"] h5 {{
        font-size: 0.82rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #7A8296 !important;
        margin: 1.4rem 0 0.6rem 0 !important;
    }}
    .app-subtitle {{ color: {NEUTRAL_COLOR}; font-size: 0.86rem; margin-bottom: 1.4rem; line-height: 1.5; max-width: 60rem; }}

    /* ---- Ticker strip: reported data, not buttons ------------------------------- */
    .ticker-strip {{
        display: flex; flex-wrap: wrap; align-items: center;
        gap: 0; margin: 0.4rem 0 1rem 0;
        border-top: 1px solid {CARD_BORDER}; border-bottom: 1px solid {CARD_BORDER};
        padding: 0.5rem 0;
    }}
    .ticker-cell {{
        display: flex; align-items: baseline; gap: 0.4rem;
        padding: 0 0.9rem;
        border-right: 1px solid {CARD_BORDER};
    }}
    .ticker-cell:last-child {{ border-right: none; }}
    .ticker-label {{ font-size: 0.68rem; font-weight: 600; letter-spacing: 0.04em; color: #5B6478; }}
    .ticker-value {{ font-size: 0.78rem; font-weight: 600; }}

    /* ---- Cards: hairline borders, no shadows ------------------------------------ */
    .card {{
        background: {CARD_BG};
        border: 1px solid {CARD_BORDER};
        border-radius: 8px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.5rem;
    }}
    .card-hover:hover {{
        background: {CARD_BG_HOVER};
        border-color: rgba(255,255,255,0.16);
    }}
    .card-accent-long {{ border-left: 2px solid {LONG_COLOR}; }}
    .card-accent-short {{ border-left: 2px solid {SHORT_COLOR}; }}

    /* ---- Badges / chips ----------------------------------------------------------- */
    .badge {{
        display: inline-block;
        padding: 2px 9px;
        border-radius: 3px;
        font-size: 0.68rem;
        font-weight: 700;
        letter-spacing: 0.03em;
        font-family: 'JetBrains Mono', monospace;
    }}
    .badge-long {{ background: rgba(34,197,94,0.12); color: {LONG_COLOR}; border: 1px solid rgba(34,197,94,0.3); }}
    .badge-short {{ background: rgba(239,68,68,0.12); color: {SHORT_COLOR}; border: 1px solid rgba(239,68,68,0.3); }}
    .badge-neutral {{ background: rgba(139,147,167,0.12); color: {NEUTRAL_COLOR}; border: 1px solid rgba(139,147,167,0.25); }}

    .chip-row {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.3rem 0 0.8rem 0; }}
    .chip {{
        background: rgba(255,255,255,0.03);
        color: #A7AEBE;
        border: 1px solid {CARD_BORDER};
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 0.76rem;
        font-weight: 600;
        font-family: 'JetBrains Mono', monospace;
    }}
    .chip:hover {{ border-color: rgba(255,255,255,0.24); }}

    /* ---- Stats / metrics ----------------------------------------------------------- */
    .mini-label {{
        color: #5B6478;
        font-size: 0.66rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 0.15rem;
    }}
    .mini-value {{ font-size: 0.98rem; font-weight: 700; }}

    .pattern-title {{ font-size: 0.98rem; font-weight: 700; margin-bottom: 0.15rem; color: {TEXT_PRIMARY}; }}
    .pattern-meta {{ color: {NEUTRAL_COLOR}; font-size: 0.78rem; margin-bottom: 0.5rem; }}

    div[data-testid="stMetric"] {{
        background: {CARD_BG};
        border: 1px solid {CARD_BORDER};
        border-radius: 6px;
        padding: 0.75rem 0.9rem 0.6rem 0.9rem;
    }}
    div[data-testid="stMetricValue"] {{ font-weight: 700; }}
    div[data-testid="stMetricLabel"] {{ color: #7A8296; font-weight: 600; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.04em; }}

    /* ---- Buttons: flat, hairline, no glow ------------------------------------------- */
    div[data-testid="stButton"] button, div[data-testid="stDownloadButton"] button, div[data-testid="stFormSubmitButton"] button {{
        border-radius: 6px;
        font-weight: 600;
        border: 1px solid rgba(255,255,255,0.14);
        box-shadow: none !important;
    }}
    div[data-testid="stButton"] button:hover, div[data-testid="stDownloadButton"] button:hover {{
        border-color: {ACCENT_COLOR};
        color: {TEXT_PRIMARY};
    }}
    div[data-testid="stButton"] button[kind="primary"] {{
        background: {ACCENT_COLOR};
        border: 1px solid {ACCENT_COLOR};
    }}
    div[data-testid="stButton"] button[kind="primary"]:hover {{ background: #5457E5; border-color: #5457E5; }}

    /* ---- Inputs -------------------------------------------------------------------- */
    div[data-baseweb="select"] > div, div[data-testid="stTextInput"] input, div[data-testid="stNumberInput"] input {{
        border-radius: 6px !important;
        background-color: rgba(255,255,255,0.02) !important;
        border-color: {CARD_BORDER} !important;
    }}
    div[data-baseweb="select"]:focus-within > div, div[data-testid="stTextInput"] input:focus, div[data-testid="stNumberInput"] input:focus {{
        border-color: {ACCENT_COLOR} !important;
        box-shadow: none !important;
    }}

    /* ---- Tables / dataframes --------------------------------------------------------- */
    div[data-testid="stDataFrame"] {{ border-radius: 6px; overflow: hidden; border: 1px solid {CARD_BORDER}; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def sidebar_brand() -> None:
    """Wordmark pinned above the nav links -- a flat monospace letterform in a hairline
    box, not a gradient badge, matching the grayscale-chrome/one-accent design rule."""
    with st.sidebar:
        st.markdown(
            f"""<div class="sidebar-brand">
                <div class="mark">PP</div>
                <div>
                    <div class="name">{APP_NAME}</div>
                    <div class="tag">Signal Intelligence</div>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )


def set_page_config(**kwargs) -> None:
    """st.set_page_config wrapper. Called exactly once, from app.py, before st.navigation
    builds the page -- individual pages no longer call this themselves since a page run
    under st.navigation executes in the same script pass as the entrypoint, and
    set_page_config can only run once per pass."""
    st.set_page_config(page_title=APP_NAME, page_icon=f":material/{APP_ICON}:", **kwargs)


def page_header(title: str, subtitle: str = "", icon: str = "", show_brand: bool = True) -> None:
    """`icon` is a Material Symbols name (e.g. "candlestick_chart"), rendered via Streamlit's
    native `:material/name:` shortcode -- real markdown text, not raw HTML, since that
    shortcode is only guaranteed to resolve there (nesting it inside a raw HTML string is
    unverified and risks printing the literal shortcode text instead of an icon)."""
    inject_css()
    if show_brand:
        st.markdown(
            f'<div class="brand-eyebrow"><span class="dot"></span>{APP_NAME}</div>',
            unsafe_allow_html=True,
        )
    icon_prefix = f":material/{icon}: " if icon else ""
    st.markdown(f"# {icon_prefix}{title}")
    if subtitle:
        st.markdown(f'<div class="app-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def direction_badge(direction: str) -> str:
    cls = "badge-long" if direction == "long" else "badge-short"
    label = "LONG" if direction == "long" else "SHORT"
    arrow = "▲" if direction == "long" else "▼"
    return f'<span class="badge {cls}">{arrow} {label}</span>'


def pattern_label(pattern_type: str) -> str:
    return pattern_type.replace("_", " ").title()


def mini_stat(label: str, value: str) -> str:
    return f'<div><div class="mini-label">{label}</div><div class="mini-value num">{value}</div></div>'


def direction_color(direction: str) -> str:
    return LONG_COLOR if direction == "long" else SHORT_COLOR


def apply_dark_chart_theme(fig, height: int = None) -> None:
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#C7CCDA", family="JetBrains Mono, monospace", size=11),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(t=30, b=20, l=10, r=50),
    )
    if height:
        fig.update_layout(height=height)
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.05)")
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.05)")
