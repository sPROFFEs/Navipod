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
