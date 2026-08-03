from pathlib import Path


def find_javascript_modules_root(concierge_root: Path | None = None) -> Path:
    root = concierge_root or Path(__file__).resolve().parent
    candidates = (root / "assets" / "js" / "modules", root.parent / "assets" / "js" / "modules")
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


def build_javascript_import_map(version: str, modules_root: Path | None = None) -> dict:
    """Version every ES module URL so one page cannot mix cached revisions."""
    root = modules_root or find_javascript_modules_root()
    imports = {
        f"/assets/js/modules/{module.name}": f"/assets/js/modules/{module.name}?v={version}"
        for module in sorted(root.glob("*.js"))
    }
    return {"imports": imports}
