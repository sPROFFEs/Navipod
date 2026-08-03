from datetime import datetime, timedelta, timezone

import database
import library_service


def add_track(db, title, artist, album, genre, year, days_old=0):
    track = database.Track(
        title=title,
        artist=artist,
        album=album,
        genre=genre,
        year=year,
        filepath=f"/pool/{title}.mp3",
        source_id=f"local:{title}",
        file_hash=f"hash:{title}",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
    )
    db.add(track)
    db.flush()
    return track


def test_library_facets_and_track_filtering(db_session):
    add_track(db_session, "One", "Artist A", "Album A", "Rock", 2024)
    add_track(db_session, "Two", "Artist A", "Album B", "Jazz", 2023)
    add_track(db_session, "Three", "Artist B", "Album C", "Rock", 2022)
    db_session.commit()

    assert library_service.list_facets(db_session, "artists") == [
        {"name": "Artist A", "track_count": 2},
        {"name": "Artist B", "track_count": 1},
    ]
    rock = library_service.list_tracks(db_session, genre="Rock")
    assert [track["title"] for track in rock] == ["One", "Three"]


def test_album_facets_keep_same_named_albums_separate_and_page(db_session):
    add_track(db_session, "One", "Artist A", "Greatest Hits", "Rock", 2024)
    add_track(db_session, "Two", "Artist B", "Greatest Hits", "Jazz", 2023)
    add_track(db_session, "Three", "Artist C", "Other", "Rock", 2022)
    db_session.commit()

    first_page = library_service.list_facets(db_session, "albums", limit=1)
    second_page = library_service.list_facets(db_session, "albums", limit=1, offset=1)

    assert library_service.count_facets(db_session, "albums") == 3
    assert first_page[0]["name"] == "Greatest Hits"
    assert first_page[0]["artist"] == "Artist A"
    assert second_page[0]["name"] == "Greatest Hits"
    assert second_page[0]["artist"] == "Artist B"


def test_album_facet_without_artist_keeps_empty_filter_value(db_session):
    add_track(db_session, "Untitled", "", "Mystery Album", "Ambient", 2020)
    db_session.commit()

    facet = library_service.list_facets(db_session, "albums")[0]

    assert facet["artist"] == ""
    assert [track["title"] for track in library_service.list_tracks(db_session, album=facet["name"])] == ["Untitled"]


def test_track_paging_search_and_sort(db_session):
    add_track(db_session, "Beta", "Artist", "Album", "Rock", 2022)
    add_track(db_session, "Alpha", "Artist", "Album", "Rock", 2024)
    db_session.commit()

    first = library_service.list_tracks(db_session, query="Artist", limit=1, sort="title")
    second = library_service.list_tracks(db_session, query="Artist", limit=1, offset=1, sort="title")

    assert library_service.count_tracks(db_session, query="Artist") == 2
    assert [item["title"] for item in first + second] == ["Alpha", "Beta"]


def test_smart_rule_summary_is_human_readable():
    summary = library_service.describe_smart_rules(
        {"artist": "Bowie", "favorite_only": True, "limit": 25, "sort": "most_played"}
    )
    assert summary == "Artist: Bowie · Favorites · Up to 25 · most played"


def test_smart_playlist_rules_combine_metadata_favorites_and_activity(db_session, monkeypatch):
    user = database.User(username="alice", hashed_password="unused")
    db_session.add(user)
    favorite = add_track(db_session, "Favorite", "Artist A", "Album A", "Rock", 2024)
    ignored = add_track(db_session, "Ignored", "Artist A", "Album A", "Rock", 2024)
    db_session.flush()
    db_session.add(database.UserFavorite(user_id=user.id, track_id=favorite.id))
    db_session.commit()
    monkeypatch.setattr(
        library_service,
        "_activity_stats",
        lambda _username: {
            favorite.id: {"play_count": 5, "last_played_at": None},
            ignored.id: {"play_count": 10, "last_played_at": None},
        },
    )

    result = library_service.smart_track_ids(
        db_session,
        user,
        {"genre": "Rock", "favorite_only": True, "min_plays": 3, "limit": 10},
    )

    assert result == [favorite.id]
