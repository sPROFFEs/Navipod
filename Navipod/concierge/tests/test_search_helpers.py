import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from search_utils import build_fts_query, spotify_source_candidates, youtube_source_candidates


def test_build_fts_query_tokenizes_and_adds_prefix_wildcards():
    assert build_fts_query('Metallica "Master of Puppets"') == "metallica* master* of* puppets*"


def test_build_fts_query_strips_empty_tokens():
    assert build_fts_query("   ") == ""


def test_youtube_source_candidates_include_both_formats():
    assert youtube_source_candidates("abc123xyz00") == {"abc123xyz00", "youtube:abc123xyz00"}


def test_spotify_source_candidates_include_both_formats():
    assert spotify_source_candidates("7ouMYWpwJ422jRcDASZB7P") == {
        "7ouMYWpwJ422jRcDASZB7P",
        "spotify:track:7ouMYWpwJ422jRcDASZB7P",
    }
