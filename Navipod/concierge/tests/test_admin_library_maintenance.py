from datetime import datetime, timezone

import database
import library_maintenance


def test_library_audit_reports_files_metadata_sources_and_size(db_session, tmp_path):
    present = tmp_path / "present.mp3"
    present.write_bytes(b"audio")
    missing = tmp_path / "missing.mp3"
    db_session.add_all(
        [
            database.Track(
                title="Complete",
                artist="Artist",
                album="Album",
                genre="Rock",
                year=2024,
                filepath=str(present),
                source_id="local:present",
                file_hash="present",
                source_provider="local",
                metadata_scanned_at=datetime.now(timezone.utc),
            ),
            database.Track(
                title="Incomplete",
                artist="",
                album="Album",
                filepath=str(missing),
                source_id="local:missing",
                file_hash="missing",
                source_provider="download",
            ),
        ]
    )
    db_session.commit()
    result = library_maintenance.build_library_audit(db_session, roots=(str(tmp_path),))

    assert result["track_count"] == 2
    assert result["total_bytes"] == 5
    assert result["missing_file_count"] == 1
    assert result["missing_files"][0]["title"] == "Incomplete"
    assert result["missing_metadata_count"] == 1
    assert result["metadata_pending_count"] == 1
    assert result["source_counts"] == {"local": 1, "download": 1}
