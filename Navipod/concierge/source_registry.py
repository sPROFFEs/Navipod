from urllib.parse import urlparse


KNOWN_SOURCES = {
    "spotify",
    "youtube",
    "musicbrainz",
    "lastfm",
    "soundcloud",
    "audius",
    "jamendo",
}

SOURCE_DOMAIN_HINTS = (
    ("spotify", ("spotify.com", "open.spotify.com")),
    ("youtube", ("youtube.com", "youtu.be", "music.youtube.com")),
    ("musicbrainz", ("musicbrainz.org",)),
    ("lastfm", ("last.fm",)),
    ("soundcloud", ("soundcloud.com",)),
    ("audius", ("audius.co", "audius.com")),
    ("jamendo", ("jamendo.com",)),
)


def normalize_source(raw_source: str | None, default: str = "external") -> str:
    source = (raw_source or "").strip().lower()
    return source if source in KNOWN_SOURCES else default


def infer_source(raw_source: str | None = None, raw_url: str | None = None, default: str = "external") -> str:
    normalized = normalize_source(raw_source, default="")
    if normalized:
        return normalized

    url = (raw_url or "").strip().lower()
    if not url:
        return default

    parsed = urlparse(url)
    haystack = " ".join(part for part in [parsed.netloc, parsed.path, parsed.query, url] if part)
    for source, domains in SOURCE_DOMAIN_HINTS:
        if any(domain in haystack for domain in domains):
            return source
    return default
