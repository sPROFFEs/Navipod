import re
from pathlib import Path


def test_library_facet_buttons_reset_native_background():
    css_path = Path(__file__).resolve().parents[2] / "assets" / "css" / "ui_components.css"
    css = css_path.read_text(encoding="utf-8")
    rule = re.search(r"\.library-facet-row,\s*\.library-track-row\s*\{(?P<body>.*?)\}", css, re.DOTALL)

    assert rule is not None
    assert "background: transparent;" in rule.group("body")
    assert "appearance: none;" in rule.group("body")
