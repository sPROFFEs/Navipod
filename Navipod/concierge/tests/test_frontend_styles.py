import re
from pathlib import Path


def test_library_facet_buttons_reset_native_background():
    css_path = Path(__file__).resolve().parents[2] / "assets" / "css" / "ui_components.css"
    css = css_path.read_text(encoding="utf-8")
    rule = re.search(r"\.library-facet-row,\s*\.library-track-row\s*\{(?P<body>.*?)\}", css, re.DOTALL)

    assert rule is not None
    assert "background: transparent;" in rule.group("body")
    assert "appearance: none;" in rule.group("body")


def test_library_search_controls_and_facets_have_explicit_dark_styles():
    css_path = Path(__file__).resolve().parents[2] / "assets" / "css" / "ui_components.css"
    css = css_path.read_text(encoding="utf-8")

    assert 'grid-template-areas: "query sort submit";' in css
    assert ".library-sort select.modal-input option" in css
    assert "color-scheme: dark;" in css
    assert ".library-sort .library-icon-btn" in css
    assert "width: 44px;" in css
    assert ".library-facet-row:focus-visible" in css
