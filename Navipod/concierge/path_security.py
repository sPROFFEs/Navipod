from pathlib import Path


class UnsafePathError(ValueError):
    pass


def resolve_under(path: str | Path, allowed_root: str | Path) -> Path:
    """Resolve a filesystem path and ensure it remains inside allowed_root."""
    root = Path(allowed_root).resolve()
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError(f"Path escapes allowed root: {candidate}") from exc
    return candidate


def safe_child_path(root: str | Path, child: str | Path) -> Path:
    return resolve_under(Path(root) / child, root)
