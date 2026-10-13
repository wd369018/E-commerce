"""Scan all Streamlit widget calls for duplicate/conflicting `key=` values.

Reports every explicit `key="..."` passed to a widget call, plus any widget
calls that pass no explicit key (usable for the audit but not an error).
Exits non-zero when a duplicate key is found.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "src").rglob("*.py"))

KEY_CALLS = {}

for path in FILES:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        print(f"SYNTAX ERROR {path}: {exc}")
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Attribute call like st.sidebar.slider or st.date_input
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if not name:
            continue
        key_value = None
        has_key = False
        for kw in node.keywords:
            if kw.arg == "key":
                has_key = True
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    key_value = kw.value.value
        if not has_key:
            continue
        loc = f"{path.relative_to(ROOT)}:{node.lineno}"
        if key_value is None:
            print(f"  dynamic key  {name}  at {loc}")
            continue
        KEY_CALLS.setdefault(key_value, []).append((name, loc))

dupes = {k: v for k, v in KEY_CALLS.items() if len(v) > 1}
if dupes:
    print("=== DUPLICATE WIDGET KEYS ===")
    for key, uses in sorted(dupes.items()):
        print(f"key={key!r}")
        for name, loc in uses:
            print(f"    {name}  {loc}")
    sys.exit(1)

print(f"Scanned {len(FILES)} files; {len(KEY_CALLS)} distinct explicit keys; no duplicates.")