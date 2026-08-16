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


def test_playlist_actions_use_minimal_outline_controls_on_desktop_and_mobile():
    assets_root = Path(__file__).resolve().parents[2] / "assets"
    desktop_css = (assets_root / "css" / "ui_components.css").read_text(encoding="utf-8")
    mobile_css = (assets_root / "css" / "mobile_fixes.css").read_text(encoding="utf-8")
    javascript = (assets_root / "js" / "modules" / "playlists.js").read_text(encoding="utf-8")

    assert ".playlist-actions .btn-play-circle" in desktop_css
    assert "background: transparent;" in desktop_css
    assert "box-shadow: none;" in desktop_css
    assert "fill: none;" in desktop_css
    assert ".playlist-btn-label" in mobile_css
    assert "display: none !important;" in mobile_css
    assert "document.getElementById('playlist-title-${playlistId}').textContent" in javascript
    assert 'maxlength="120"' in javascript


def test_admin_user_statistics_have_responsive_styles_and_independent_polling():
    assets_root = Path(__file__).resolve().parents[2] / "assets"
    css = (assets_root / "css" / "admin_system.css").read_text(encoding="utf-8")
    javascript = (assets_root / "js" / "modules" / "system_monitor.js").read_text(encoding="utf-8")
    views_javascript = (assets_root / "js" / "modules" / "views.js").read_text(encoding="utf-8")
    template = (Path(__file__).resolve().parents[1] / "templates" / "system_monitor.html").read_text(encoding="utf-8")

    assert 'id="user-statistics-panel"' in template
    assert 'id="user-statistics-body"' in template
    assert ".monitor-statistics-table td::before" in css
    assert "/admin/api/user-statistics" in javascript
    assert "window.setInterval(refreshUserStatistics, 30000)" in javascript
    assert "document.hidden" in javascript
    assert "import { initSystemMonitor } from './system_monitor.js';" in views_javascript
    assert "if (url === '/admin/system')" in views_javascript
    assert "initSystemMonitor(container);" in views_javascript
