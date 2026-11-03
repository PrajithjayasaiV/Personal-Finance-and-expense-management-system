"""Application entry point.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import logging

import streamlit as st

import config

config.setup_logging()
st.set_page_config(page_title=config.APP_NAME, page_icon="💰", layout="wide", initial_sidebar_state="expanded")

from database import get_engine  # noqa: E402  (after set_page_config so any message renders correctly)

try:
    get_engine()   # creates the database file and tables on first launch; never drops existing data
except Exception:  # noqa: BLE001
    logging.getLogger(__name__).exception("Database initialisation failed")
    st.error("The database could not be opened. Check that the data folder is writable and that no other program "
             "is locking the file, then reload. Details were written to data/app.log.")
    st.stop()

from components.navigation import run_navigation  # noqa: E402

run_navigation()
