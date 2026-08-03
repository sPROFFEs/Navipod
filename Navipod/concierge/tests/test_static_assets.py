from pathlib import Path

from static_assets import build_javascript_import_map, find_javascript_modules_root


def test_import_map_versions_every_javascript_module(tmp_path):
    modules_root = tmp_path / "modules"
    modules_root.mkdir()
    for name in ("api.js", "library.js", "search.js"):
        (modules_root / name).touch()

    import_map = build_javascript_import_map("abc123", modules_root)

    assert import_map == {
        "imports": {
            "/assets/js/modules/api.js": "/assets/js/modules/api.js?v=abc123",
            "/assets/js/modules/library.js": "/assets/js/modules/library.js?v=abc123",
            "/assets/js/modules/search.js": "/assets/js/modules/search.js?v=abc123",
        }
    }


def test_production_import_map_covers_every_module():
    modules_root = Path(__file__).resolve().parents[2] / "assets" / "js" / "modules"
    expected_urls = {f"/assets/js/modules/{module.name}" for module in modules_root.glob("*.js")}

    imports = build_javascript_import_map("release-sha")["imports"]

    assert set(imports) == expected_urls
    assert all(url == f"{path}?v=release-sha" for path, url in imports.items())


def test_module_root_supports_repository_and_container_layouts(tmp_path):
    repository_concierge = tmp_path / "repository" / "Navipod" / "concierge"
    repository_modules = repository_concierge.parent / "assets" / "js" / "modules"
    repository_modules.mkdir(parents=True)

    container_root = tmp_path / "container" / "app"
    container_modules = container_root / "assets" / "js" / "modules"
    container_modules.mkdir(parents=True)

    assert find_javascript_modules_root(repository_concierge) == repository_modules
    assert find_javascript_modules_root(container_root) == container_modules


def test_base_template_installs_import_map_before_application_module():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "base.html"
    template = template_path.read_text(encoding="utf-8")

    import_map_position = template.index('<script type="importmap">')
    application_position = template.index('<script type="module" src="/assets/js/main.js')

    assert "javascript_import_map | tojson" in template
    assert import_map_position < application_position
