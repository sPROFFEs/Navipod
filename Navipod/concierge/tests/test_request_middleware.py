import asyncio
from contextvars import ContextVar
from pathlib import Path

import pytest
from request_middleware import RequestContextMiddleware
from starlette.datastructures import Headers

CONCIERGE_DIR = Path(__file__).resolve().parents[1]


def test_main_uses_pure_asgi_request_middleware():
    source = (CONCIERGE_DIR / "main.py").read_text()

    assert '@app.middleware("http")' not in source
    assert "app.add_middleware(\n    RequestContextMiddleware," in source


def _http_scope(*, cookie: bytes = b"") -> dict:
    headers = [(b"host", b"testserver")]
    if cookie:
        headers.append((b"cookie", cookie))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream",
        "raw_path": b"/stream",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


async def _receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def test_request_context_middleware_streams_directly_and_applies_headers():
    language_context = ContextVar("test_language", default="es")
    observed_languages = []
    validated_paths = []
    sent_messages = []

    async def streaming_app(scope, receive, send):
        observed_languages.append(language_context.get())
        await send({"type": "http.response.start", "status": 200, "headers": [(b"x-frame-options", b"SAMEORIGIN")]})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await send({"type": "http.response.body", "body": b"second", "more_body": False})

    async def capture_send(message):
        sent_messages.append(message)

    middleware = RequestContextMiddleware(
        streaming_app,
        language_context=language_context,
        supported_languages={"en", "es"},
        default_language="es",
        validate_request=lambda request: validated_paths.append(request.url.path),
    )

    asyncio.run(middleware(_http_scope(cookie=b"lang=en"), _receive, capture_send))

    headers = Headers(raw=sent_messages[0]["headers"])
    assert observed_languages == ["en"]
    assert validated_paths == ["/stream"]
    assert [message.get("body") for message in sent_messages[1:]] == [b"first", b"second"]
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "frame-src 'self' https://www.youtube.com" in headers["content-security-policy"]
    assert language_context.get() == "es"


def test_request_context_middleware_resets_language_after_client_disconnect():
    language_context = ContextVar("disconnect_language", default="es")

    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"chunk", "more_body": True})

    async def disconnected_send(message):
        if message["type"] == "http.response.body":
            raise ConnectionError("client disconnected")

    middleware = RequestContextMiddleware(
        streaming_app,
        language_context=language_context,
        supported_languages={"en", "es"},
        default_language="es",
        validate_request=lambda request: None,
    )

    with pytest.raises(ConnectionError, match="client disconnected"):
        asyncio.run(middleware(_http_scope(cookie=b"lang=en"), _receive, disconnected_send))

    assert language_context.get() == "es"
