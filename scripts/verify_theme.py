"""Headless verification of the app-level theme engine.

Sets the segmented-control session key to Light/Dark and checks that tokens(),
plotly_tokens() and a real Plotly chart follow the active theme. No browser
required.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest

SCRIPT = """
import streamlit as st
import pandas as pd
from src.dashboard import theme, charts

result = {
    "active": theme.get_active_theme(),
    "text_token": theme.tokens()["text"],
    "plotly_paper": theme.plotly_tokens()["paper"],
}
df = pd.DataFrame({"x": ["a", "b", "c"], "y": [1, 2, 3]})
fig = charts.bar(df, "x", "y", title="probe")
result["fig_paper"] = fig.layout.paper_bgcolor
result["fig_font"] = fig.layout.font.color
result["fig_grid"] = fig.layout.yaxis.gridcolor
result["pie_line"] = charts.pie(df, "x", "y").data[0].marker.line.color
st.session_state["_result"] = result
"""


def run(theme_value):
    at = AppTest.from_string(SCRIPT, default_timeout=60)
    if theme_value:
        at.session_state["app_theme_control"] = theme_value
    at.run()
    assert not at.exception, f"exception in theme probe: {at.exception}"
    return at.session_state["_result"]


light = run("Light")
dark = run("Dark")
default = run(None)

checks = {
    "default_is_light": default["active"] == "light",
    "light_tokens": light["active"] == "light" and light["text_token"] == "#111827",
    "light_plotly": light["plotly_paper"] == "#ffffff" and light["fig_font"] == "#374151",
    "light_fig_paper": light["fig_paper"] == "#ffffff",
    "light_pie_line": light["pie_line"] == "#ffffff",
    "dark_tokens": dark["active"] == "dark" and dark["text_token"] == "#e7ecf5",
    "dark_plotly": dark["plotly_paper"] == "#0f172a" and dark["fig_font"] == "#cbd5e1",
    "dark_fig_paper": dark["fig_paper"] == "#0f172a",
    "dark_pie_line": dark["pie_line"] == "#0f172a",
    "dark_grid": dark["fig_grid"] == "#22304a",
}
print("LIGHT :", json.dumps(light))
print("DARK  :", json.dumps(dark))
print("DEFAULT:", json.dumps(default))
failed = [k for k, v in checks.items() if not v]
print("checks:", "ALL PASS" if not failed else f"FAILED {failed}")
sys.exit(1 if failed else 0)