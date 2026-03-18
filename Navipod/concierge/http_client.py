"""
Shared HTTP Client with connection pooling.
Use this instead of creating new httpx.AsyncClient() instances.
"""
import httpx

# Shared async client with connection pooling
http_client = httpx.AsyncClient(
    timeout=15.0,
    limits=httpx.Limits(
        max_connections=100,        # Max total connections
        max_keepalive_connections=20  # Keep alive for reuse
    ),
    follow_redirects=True
)


async def close_http_client():
    """Call on app shutdown to clean up connections."""
    await http_client.aclose()
