import ast
from pathlib import Path

CONCIERGE_ROOT = Path(__file__).resolve().parents[1]


def _function_kind(relative_path: str, function_name: str):
    tree = ast.parse((CONCIERGE_ROOT / relative_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return type(node)
    raise AssertionError(f"Missing function {function_name} in {relative_path}")


def test_synchronous_request_handlers_run_in_fastapi_threadpool():
    handlers = {
        "routers/music/core.py": ("downloads_page", "library_page", "search_page"),
        "routers/music/library.py": ("library_facets", "library_tracks"),
        "routers/music/personalization.py": (
            "record_listen_event",
            "list_mixes",
            "get_mix",
            "save_mix_as_playlist",
        ),
        "routers/music/recommendations.py": ("discovery_feed",),
        "routers/music/streaming.py": ("stream_track", "stream_track_authorized", "get_track_gain", "get_random_track"),
        "routers/federation.py": ("federation_stream",),
    }

    for relative_path, names in handlers.items():
        for name in names:
            assert _function_kind(relative_path, name) is ast.FunctionDef


def test_async_image_proxy_offloads_pillow_processing():
    source = (CONCIERGE_ROOT / "routers" / "music" / "core.py").read_text(encoding="utf-8")

    assert "await asyncio.to_thread(_convert_image_to_webp" in source


def test_radio_proxy_offloads_blocking_dns_resolution():
    source = (CONCIERGE_ROOT / "main.py").read_text(encoding="utf-8")

    assert "await asyncio.to_thread(_validate_radio_proxy_url, current_url)" in source
