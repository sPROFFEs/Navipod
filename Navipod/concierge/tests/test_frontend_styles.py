import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


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


def test_admin_downloader_runtime_controls_have_explicit_status_styles():
    assets_root = Path(__file__).resolve().parents[2] / "assets"
    css = (assets_root / "css" / "admin_system.css").read_text(encoding="utf-8")
    template = (Path(__file__).resolve().parents[1] / "templates" / "system_monitor.html").read_text(encoding="utf-8")

    assert 'id="downloader-runtime-panel"' in template
    assert 'action="/admin/system/downloader/mode"' in template
    assert 'value="automatic"' in template
    assert 'value="worker"' in template
    assert 'value="legacy"' in template
    assert ".monitor-runtime-status.available" in css
    assert ".monitor-runtime-status.unavailable" in css


def test_system_monitor_renders_during_old_handler_new_template_update_window():
    templates_root = Path(__file__).resolve().parents[1] / "templates"
    environment = Environment(loader=FileSystemLoader(templates_root))
    environment.globals.update(static_v="test", _=lambda value: value)
    ram = type("Ram", (), {"percent": 2, "used": 1024**3, "total": 2 * 1024**3})()
    request = type("Request", (), {"query_params": {"msg": "Update applied successfully"}})()

    rendered = environment.get_template("system_monitor.html").render(
        request=request,
        stats={
            "cpu_usage": 1,
            "ram": ram,
            "disk_total": 3,
            "disk_used": 1,
            "disk_free": 2,
            "disk_percent": 33,
        },
        pool={"used": 1, "limit": 2, "percent": 50},
        build={},
        schema={},
        backups={"current": None, "previous": None, "autobackup_enabled": False},
        updates={"current": {}},
        timezone_options=[],
        wrapped={},
        wrapped_users=[],
        admin_jobs=[],
        active_lock=None,
        username="admin",
        is_admin=True,
    )

    assert "Update applied successfully" in rendered
    assert "Downloader status is temporarily unavailable while Concierge finishes updating." in rendered
