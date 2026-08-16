from pathlib import Path

import database
import pytest
from m3u_service import M3UService
from playlist_files import MAX_PLAYLIST_NAME_LENGTH, normalize_playlist_name, playlist_m3u_filename


def test_playlist_display_names_keep_supported_punctuation():
    name = "Rock/Metal: 80's & more?"

    assert normalize_playlist_name(f"  {name}  ") == name


@pytest.mark.parametrize("name", ["", "   ", "line\nbreak", "x" * (MAX_PLAYLIST_NAME_LENGTH + 1)])
def test_playlist_display_name_rejects_only_invalid_text(name):
    with pytest.raises(ValueError):
        normalize_playlist_name(name)


def test_playlist_m3u_filenames_are_safe_and_collision_free():
    first = playlist_m3u_filename("Rock/Metal", 12)
    second = playlist_m3u_filename("Rock:Metal", 13)

    assert first == "Rock-Metal--12.m3u"
    assert second == "Rock-Metal--13.m3u"
    assert Path(first).name == first
    assert first != second


def test_playlist_m3u_filename_stays_within_filesystem_byte_limit():
    filename = playlist_m3u_filename("🎵" * MAX_PLAYLIST_NAME_LENGTH, 99)

    assert len(filename.encode("utf-8")) < 255


def test_regenerating_playlist_migrates_legacy_m3u_without_changing_name(db_session, tmp_path, monkeypatch):
    user = database.User(username="listener", hashed_password="hash")
    db_session.add(user)
    db_session.commit()
    playlist = database.Playlist(name="Rock/Metal: 80's", owner_id=user.id)
    db_session.add(playlist)
    db_session.commit()
    db_session.refresh(playlist)

    legacy_path = tmp_path / "legacy.m3u"
    legacy_path.write_text("#EXTM3U\n", encoding="utf-8")
    playlist.m3u_path = str(legacy_path)
    db_session.commit()
    monkeypatch.setattr("m3u_service.settings.MUSIC_ROOT", str(tmp_path))

    service = M3UService(db_session, user)
    service.regenerate_all_m3u()
    generated_path = playlist.m3u_path

    assert generated_path is not None
    assert Path(generated_path).name == f"Rock-Metal- 80's--{playlist.id}.m3u"
    assert Path(generated_path).is_file()
    assert not legacy_path.exists()
    assert playlist.name == "Rock/Metal: 80's"


def test_migrating_a_legacy_collision_does_not_remove_a_path_still_in_use(db_session, tmp_path, monkeypatch):
    user = database.User(username="listener", hashed_password="hash")
    db_session.add(user)
    db_session.commit()
    legacy_path = tmp_path / "RockMetal.m3u"
    legacy_path.write_text("#EXTM3U\n", encoding="utf-8")
    first = database.Playlist(name="Rock/Metal", owner_id=user.id, m3u_path=str(legacy_path))
    second = database.Playlist(name="Rock:Metal", owner_id=user.id, m3u_path=str(legacy_path))
    db_session.add_all([first, second])
    db_session.commit()
    monkeypatch.setattr("m3u_service.settings.MUSIC_ROOT", str(tmp_path))
    service = M3UService(db_session, user)

    service._write_m3u(first)

    assert legacy_path.exists()
    service._write_m3u(second)
    assert not legacy_path.exists()
