"""Small async coordination helpers shared by provider integrations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


async def gather_named(
    calls: Mapping[str, Awaitable[Any]],
    *,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> dict[str, Any]:
    """Run independent calls concurrently and isolate provider failures."""
    if not calls:
        return {}
    names = list(calls)
    values = await asyncio.gather(*(calls[name] for name in names), return_exceptions=True)
    results: dict[str, Any] = {}
    for name, value in zip(names, values, strict=True):
        if isinstance(value, BaseException):
            if on_error:
                on_error(name, value)
            continue
        results[name] = value
    return results
