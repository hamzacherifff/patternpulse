"""Streamlit entrypoint: logging -> config validation -> DB init, then the branded
sidebar/navigation shell that routes into every page."""

import logging

import streamlit as st

from core.config import app as app_config
from core.db import init_db
from core.security import setup_logging, validate_config
from core.ui import set_page_config, sidebar_brand

setup_logging(app_config.log_dir)
validate_config()
init_db()

logger = logging.getLogger(__name__)

set_page_config(layout="wide")
sidebar_brand()

pages = {
    "": [st.Page("pages/0_Home.py", title="Home", icon=":material/home:", default=True)],
    "Markets": [
        st.Page("pages/1_Live_Charts.py", title="Live Charts", icon=":material/candlestick_chart:"),
        st.Page("pages/5_Screener.py", title="Screener", icon=":material/radar:"),
    ],
    "Trading": [
        st.Page("pages/2_Signal_Feed.py", title="Signal Feed", icon=":material/bolt:"),
        st.Page("pages/6_Positions.py", title="Positions", icon=":material/account_balance_wallet:"),
    ],
    "Insights": [
        st.Page("pages/3_Performance.py", title="Performance", icon=":material/monitoring:"),
    ],
    "Configuration": [
        st.Page("pages/4_Settings.py", title="Settings", icon=":material/settings:"),
    ],
}
st.navigation(pages).run()
