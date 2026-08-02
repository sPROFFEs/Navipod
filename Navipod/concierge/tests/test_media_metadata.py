from types import SimpleNamespace

import media_metadata


def test_read_audio_metadata_extracts_common_tags(monkeypatch, tmp_path):
    path = tmp_path / "fallback.mp3"
    path.write_bytes(b"not real audio")
    easy = {
        "title": ["Tagged title"],
        "artist": ["Tagged artist"],
        "album": ["Tagged album"],
        "genre": ["Ambient"],
        "date": ["2021-04-02"],
    }
    full = SimpleNamespace(info=SimpleNamespace(length=185.8))
    calls = iter([easy, full])
    monkeypatch.setattr(media_metadata.mutagen, "File", lambda *_args, **_kwargs: next(calls))

    metadata = media_metadata.read_audio_metadata(path)

    assert metadata.title == "Tagged title"
    assert metadata.genre == "Ambient"
    assert metadata.year == 2021
    assert metadata.duration == 185
