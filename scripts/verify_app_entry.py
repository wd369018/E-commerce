"""Smoke-test the real entry point app/app.py through Streamlit's AppTest.

This exercises the parts the per-view tests skip: set_page_config, the sidebar
theme control, CSS injection, st.navigation + default page execution.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest

at = AppTest.from_file("app/app.py", default_timeout=180).run()

print("exceptions       :", len(at.exception))
for e in at.exception:
    print("  EXC:", e.value if hasattr(e, "value") else e)
print("titles           :", len(at.title), [t.value for t in at.title][:4])
print("markdown blocks  :", len(at.markdown))
print("dataframes       :", len(at.dataframe))
print("plotly_charts    :", len(at.get("plotly_chart")) or len(at.plotly_chart))
print("buttons          :", len(at.button))
print("button_groups    :", len(at.button_group), [bg.value for bg in at.button_group])
print("segmented_control:", len(at.segmented_control), [sc.value for sc in at.segmented_control])
print("date_inputs      :", len(at.date_input))
print("multiselects     :", len(at.multiselect))
print("session theme    :", at.session_state["app_theme_control"])

at2 = AppTest.from_file("app/app.py", default_timeout=180)
at2.session_state["app_theme_control"] = "Dark"
at2.run()
print("--- dark run ---")
print("dark exceptions  :", len(at2.exception))
print("dark session     :", at2.session_state["app_theme_control"])

sys.exit(1 if (at.exception or at2.exception) else 0)