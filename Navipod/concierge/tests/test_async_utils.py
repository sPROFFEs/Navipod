import asyncio
import time

from async_utils import gather_named


def test_gather_named_runs_providers_concurrently_and_isolates_failures():
    errors = []

    async def provider(value, delay=0.05):
        await asyncio.sleep(delay)
        return value

    async def broken_provider():
        await asyncio.sleep(0.01)
        raise RuntimeError("offline")

    started = time.monotonic()
    result = asyncio.run(
        gather_named(
            {
                "youtube": provider(["youtube"]),
                "spotify": provider(["spotify"]),
                "lastfm": broken_provider(),
            },
            on_error=lambda name, exc: errors.append((name, str(exc))),
        )
    )
    elapsed = time.monotonic() - started

    assert result == {"youtube": ["youtube"], "spotify": ["spotify"]}
    assert errors == [("lastfm", "offline")]
    assert elapsed < 0.09
