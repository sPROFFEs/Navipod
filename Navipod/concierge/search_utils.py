import re


def build_fts_query(raw_query: str) -> str:
    tokens = []
    for token in re.split(r"\s+", (raw_query or "").strip().lower()):
        cleaned = re.sub(r'["*]', "", token).strip()
        if cleaned:
            tokens.append(f"{cleaned}*")
    return " ".join(tokens)


def youtube_source_candidates(video_id: str) -> set[str]:
    if not video_id:
        return set()
    return {video_id, f"youtube:{video_id}"}


def spotify_source_candidates(track_id: str) -> set[str]:
    if not track_id:
        return set()
    return {track_id, f"spotify:track:{track_id}"}
