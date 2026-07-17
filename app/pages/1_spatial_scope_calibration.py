"""Standalone page for calibrating deterministic spatial scopes."""
from pathlib import Path
import sys

import streamlit as st


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="空间范围标定", page_icon="🧭", layout="wide")

from urban_agent.spatial_scope_ui import render_spatial_scope_calibration  # noqa: E402


render_spatial_scope_calibration()
