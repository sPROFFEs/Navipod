import re
from pathlib import Path


def test_base_template_versions_every_javascript_module_without_new_runtime_globals():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "base.html"
    template = template_path.read_text(encoding="utf-8")
    modules_root = Path(__file__).resolve().parents[2] / "assets" / "js" / "modules"
    expected_urls = {f"/assets/js/modules/{module.name}" for module in modules_root.glob("*.js")}
    mappings = re.findall(r'"(/assets/js/modules/[^\"]+\.js)": "([^\"]+)"', template)

    import_map_position = template.index('<script type="importmap">')
    application_position = template.index('<script type="module" src="/assets/js/main.js')

    assert {source for source, _target in mappings} == expected_urls
    assert all(target == f"{source}?v={{{{ static_v }}}}" for source, target in mappings)
    assert "javascript_import_map" not in template
    assert import_map_position < application_position


def test_entrypoint_scripts_use_the_stable_release_cache_key():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "base.html"
    template = template_path.read_text(encoding="utf-8")

    assert "/assets/js/main.js?v={{ static_v }}" in template
    assert "/assets/js/modules/admin_system.js?v={{ static_v }}" in template
    assert "range(1, 99999) | random" not in template


def test_nginx_compresses_text_assets_and_keeps_streaming_unbuffered():
    nginx_path = Path(__file__).resolve().parents[2] / "nginx.conf"
    nginx = nginx_path.read_text(encoding="utf-8")

    assert "gzip on;" in nginx
    assert "text/css" in nginx
    assert "application/javascript" in nginx
    assert "application/json" in nginx
    assert "proxy_buffering off;" in nginx


def test_first_party_frontend_stays_inside_download_budgets():
    assets_root = Path(__file__).resolve().parents[2] / "assets"
    javascript = [assets_root / "js" / "main.js", *(assets_root / "js" / "modules").glob("*.js")]
    stylesheets = list((assets_root / "css").glob("*.css"))

    # These are uncompressed upper bounds. Nginx serves them compressed, so a
    # regression here catches accidental large bundles before transfer cost is
    # multiplied across every navigation.
    assert sum(path.stat().st_size for path in javascript) < 500_000
    assert sum(path.stat().st_size for path in stylesheets) < 300_000
