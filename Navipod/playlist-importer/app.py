import asyncio
import base64
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import spotipy
import yt_dlp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Cookie, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from spotipy.oauth2 import SpotifyClientCredentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("navipod-playlist-importer")

NAVIPOD_BASE_URL = os.getenv("NAVIPOD_BASE_URL", "http://concierge:8000").rstrip("/")
NAVIPOD_PUBLIC_ORIGIN = os.getenv("NAVIPOD_PUBLIC_ORIGIN", "").strip().rstrip("/")
if not NAVIPOD_PUBLIC_ORIGIN:
    raise RuntimeError("NAVIPOD_PUBLIC_ORIGIN is required")
NAVIPOD_PUBLIC_URL = urlparse(NAVIPOD_PUBLIC_ORIGIN)
NAVIPOD_PUBLIC_HOST = NAVIPOD_PUBLIC_URL.netloc
if (
    NAVIPOD_PUBLIC_URL.scheme not in {"http", "https"}
    or not NAVIPOD_PUBLIC_HOST
    or NAVIPOD_PUBLIC_URL.path not in {"", "/"}
    or NAVIPOD_PUBLIC_URL.params
    or NAVIPOD_PUBLIC_URL.query
    or NAVIPOD_PUBLIC_URL.fragment
):
    raise RuntimeError("NAVIPOD_PUBLIC_ORIGIN must contain only an HTTP(S) scheme and host")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "importer.db"
IMPORT_CONCURRENCY = max(1, min(int(os.getenv("IMPORT_CONCURRENCY", "3")), 8))
MAX_ATTEMPTS = max(1, min(int(os.getenv("MAX_ATTEMPTS", "3")), 10))
RETRY_BASE_SECONDS = max(5, int(os.getenv("RETRY_BASE_SECONDS", "30")))
FAVORITE_DELAY_SECONDS = max(0.1, float(os.getenv("FAVORITE_DELAY_SECONDS", "0.5")))
FAVORITE_BATCH_SIZE = max(1, min(int(os.getenv("FAVORITE_BATCH_SIZE", "10")), 50))
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
YTDLP_COOKIE_FILE = Path(os.getenv("YTDLP_COOKIE_FILE", "/data/youtube-cookies.txt"))
NAVIPOD_COVER_CACHE_DIR = Path(os.getenv("NAVIPOD_COVER_CACHE_DIR", "/navipod-cover-cache"))
FERNET_KEY = os.getenv("IMPORTER_FERNET_KEY", "").strip()

if not FERNET_KEY:
    raise RuntimeError("IMPORTER_FERNET_KEY is required")

try:
    fernet = Fernet(FERNET_KEY.encode())
except Exception as exc:
    raise RuntimeError("IMPORTER_FERNET_KEY is not a valid Fernet key") from exc

TERMINAL_JOB_STATES = {"completed", "finished", "failed", "error"}
SUCCESS_JOB_STATES = {"completed", "finished"}
ACTIVE_TRACK_STATES = {"queued", "downloading"}
RUNNABLE_IMPORT_STATES = {"enumerating", "running", "finalizing"}


def now() -> float:
    return time.time()


def invalidate_cover_cache(track_id: int) -> bool:
    """Remove Navipod's generated cover cache for one track."""
    try:
        path = NAVIPOD_COVER_CACHE_DIR / f"{int(track_id)}.jpg"
        if path.exists():
            path.unlink()
            log.info("Invalidated cover cache for track %s", track_id)
            return True
    except Exception as exc:
        # Cover invalidation must never make an otherwise valid import fail.
        log.warning("Could not invalidate cover cache for track %s: %s", track_id, exc)
    return False


def connect_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS imports (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_title TEXT,
                destination TEXT NOT NULL,
                requested_playlist_id INTEGER,
                playlist_id INTEGER,
                playlist_name TEXT,
                temp_playlist INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                playlist_finalized INTEGER NOT NULL DEFAULT 0,
                token_enc TEXT NOT NULL,
                warning TEXT,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tracks (
                import_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                source_id TEXT,
                url TEXT,
                title TEXT,
                artist TEXT,
                album TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                navipod_job_id INTEGER,
                resolved_track_id INTEGER,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                favorite_applied INTEGER NOT NULL DEFAULT 0,
                favorite_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                PRIMARY KEY(import_id, position),
                FOREIGN KEY(import_id) REFERENCES imports(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tracks_state
                ON tracks(import_id, status, next_attempt_at);
            CREATE INDEX IF NOT EXISTS idx_tracks_job
                ON tracks(navipod_job_id);
            CREATE INDEX IF NOT EXISTS idx_tracks_resolved
                ON tracks(import_id, resolved_track_id);
            """
        )


def encode_token(token: str) -> str:
    return fernet.encrypt(token.encode()).decode()


def decode_token(enc: str) -> str:
    try:
        return fernet.decrypt(enc.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Stored authentication token cannot be decrypted") from exc


def jwt_subject_unverified(token: str) -> str | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        raw = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


class ImportCreate(BaseModel):
    url: str = Field(min_length=5, max_length=2048)
    destination: str
    playlist_name: str | None = Field(default=None, max_length=200)
    playlist_id: int | None = Field(default=None, ge=1)


async def navipod_request(
    token: str,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    follow_redirects: bool = False,
    timeout: float = 20.0,
) -> httpx.Response:
    method_upper = method.upper()

    headers = {
        "Host": NAVIPOD_PUBLIC_HOST,
        "X-Forwarded-Proto": NAVIPOD_PUBLIC_URL.scheme,
    }

    if method_upper in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["Origin"] = NAVIPOD_PUBLIC_ORIGIN
        headers["Referer"] = NAVIPOD_PUBLIC_ORIGIN + "/"

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
        return await client.request(
            method_upper,
            f"{NAVIPOD_BASE_URL}{path}",
            headers=headers,
            cookies={"access_token": token},
            json=json_body,
            data=data,
        )


async def validate_navipod_session(token: str) -> str:
    r = await navipod_request(token, "GET", "/api/playlists")
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Navipod session is not valid")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Navipod validation failed: HTTP {r.status_code} {r.text[:200]}")
    owner = jwt_subject_unverified(token)
    if not owner:
        raise HTTPException(status_code=401, detail="Could not identify Navipod user")
    return owner


def detect_source(url: str) -> str:
    try:
        p = urlparse(url.strip())
    except Exception as exc:
        raise ValueError("Invalid URL") from exc
    if p.scheme not in {"http", "https"}:
        raise ValueError("Playlist URL must use HTTP or HTTPS")
    host = p.hostname.lower() if p.hostname else ""
    if host in {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be"}:
        return "youtube"
    if host in {"open.spotify.com", "spotify.com", "www.spotify.com"}:
        return "spotify"
    raise ValueError("Only YouTube, YouTube Music and Spotify playlist URLs are accepted")


def enumerate_youtube(url: str) -> tuple[str, list[dict[str, Any]], str | None]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        "cachedir": False,
        "lazy_playlist": False,
    }
    if YTDLP_COOKIE_FILE.is_file():
        opts["cookiefile"] = str(YTDLP_COOKIE_FILE)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise RuntimeError("yt-dlp could not read the playlist")
    entries = info.get("entries") or []
    title = info.get("title") or "YouTube playlist"
    expected = info.get("playlist_count") or info.get("n_entries")
    result: list[dict[str, Any]] = []

    for index, entry in enumerate(entries, start=1):
        if not entry:
            result.append(
                {
                    "position": index,
                    "source_id": None,
                    "url": None,
                    "title": "Unavailable entry",
                    "artist": None,
                    "album": None,
                    "status": "permanent_failed",
                    "last_error": "Source entry was unavailable during playlist enumeration",
                }
            )
            continue
        video_id = entry.get("id")
        entry_url = entry.get("url") or entry.get("webpage_url")
        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(video_id)):
            track_url = f"https://www.youtube.com/watch?v={video_id}"
        elif isinstance(entry_url, str) and entry_url.startswith("http"):
            track_url = entry_url
        else:
            track_url = None
        artist = entry.get("artist") or entry.get("channel") or entry.get("uploader")
        result.append(
            {
                "position": index,
                "source_id": f"youtube:{video_id}" if video_id else None,
                "url": track_url,
                "title": entry.get("title") or f"Track {index}",
                "artist": artist,
                "album": entry.get("album"),
                "status": "pending" if track_url else "permanent_failed",
                "last_error": None if track_url else "Could not derive a playable YouTube URL",
            }
        )

    warning = None
    if expected and int(expected) != len(result):
        warning = f"Source reported {expected} entries but yt-dlp enumerated {len(result)}."
    if not result:
        raise RuntimeError("Playlist contained no readable entries")
    return title, result, warning


def spotify_playlist_id(url: str) -> str:
    p = urlparse(url)
    m = re.search(r"/playlist/([A-Za-z0-9]+)", p.path)
    if not m:
        raise RuntimeError("Spotify URL is not a playlist URL")
    return m.group(1)


def enumerate_spotify(url: str) -> tuple[str, list[dict[str, Any]], str | None]:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError("Spotify enumeration requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET")
    auth = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
    sp = spotipy.Spotify(auth_manager=auth, requests_timeout=15, retries=3)
    pid = spotify_playlist_id(url)
    meta = sp.playlist(pid, fields="name,tracks.total")
    title = meta.get("name") or "Spotify playlist"
    expected = int((meta.get("tracks") or {}).get("total") or 0)
    offset = 0
    result: list[dict[str, Any]] = []
    while True:
        page = sp.playlist_items(
            pid,
            offset=offset,
            limit=100,
            fields="items(track(id,name,is_local,artists(name),album(name),external_urls)),next,total",
        )
        items = page.get("items") or []
        for item in items:
            position = len(result) + 1
            track = (item or {}).get("track")
            if not track:
                result.append(
                    {
                        "position": position,
                        "source_id": None,
                        "url": None,
                        "title": "Unavailable Spotify track",
                        "artist": None,
                        "album": None,
                        "status": "permanent_failed",
                        "last_error": "Spotify returned an unavailable/deleted track",
                    }
                )
                continue
            artists = ", ".join(a.get("name") for a in (track.get("artists") or []) if a.get("name"))
            track_url = (track.get("external_urls") or {}).get("spotify")
            result.append(
                {
                    "position": position,
                    "source_id": f"spotify:track:{track.get('id')}" if track.get("id") else None,
                    "url": track_url,
                    "title": track.get("name") or f"Track {position}",
                    "artist": artists or None,
                    "album": (track.get("album") or {}).get("name"),
                    "status": "pending" if track_url and not track.get("is_local") else "permanent_failed",
                    "last_error": None
                    if track_url and not track.get("is_local")
                    else "Spotify local/unavailable tracks cannot be imported",
                }
            )
        if not page.get("next"):
            break
        offset += len(items)
        if not items:
            break
    warning = None
    if expected and expected != len(result):
        warning = f"Source reported {expected} entries but Spotify API enumerated {len(result)}."
    if not result:
        raise RuntimeError("Playlist contained no readable entries")
    return title, result, warning


def enumerate_source(source_type: str, url: str) -> tuple[str, list[dict[str, Any]], str | None]:
    if source_type == "youtube":
        return enumerate_youtube(url)
    if source_type == "spotify":
        return enumerate_spotify(url)
    raise RuntimeError("Unsupported source")


async def create_playlist(token: str, name: str) -> int:
    r = await navipod_request(token, "POST", "/api/playlists", json_body={"name": name})
    if r.status_code == 401:
        raise PermissionError("auth")
    if r.status_code != 200:
        raise RuntimeError(f"Could not create Navipod playlist: HTTP {r.status_code} {r.text[:300]}")
    return int(r.json()["id"])


async def verify_existing_playlist(token: str, playlist_id: int) -> None:
    r = await navipod_request(token, "GET", f"/api/playlists/{playlist_id}")
    if r.status_code == 401:
        raise PermissionError("auth")
    if r.status_code != 200:
        raise RuntimeError(f"Playlist {playlist_id} is not accessible")
    data = r.json()
    if not data.get("is_owner") or not data.get("is_editable"):
        raise RuntimeError("Target playlist is not editable by this user")


async def navipod_jobs(token: str) -> list[dict[str, Any]]:
    r = await navipod_request(token, "GET", "/api/jobs")
    if r.status_code == 401:
        raise PermissionError("auth")
    if r.status_code != 200:
        raise RuntimeError(f"Could not read Navipod jobs: HTTP {r.status_code}")
    data = r.json()
    return data if isinstance(data, list) else []


async def submit_track(token: str, playlist_id: int, track_url: str) -> int:
    before = await navipod_jobs(token)
    before_max = max((int(x.get("id") or 0) for x in before), default=0)
    r = await navipod_request(
        token,
        "POST",
        "/api/downloads/start",
        data={
            "url": track_url,
            "target_mode": "existing",
            "target_playlist_id": str(playlist_id),
            "new_playlist_name": "",
            "is_playlist": "false",
        },
        follow_redirects=False,
        timeout=30.0,
    )
    if r.status_code == 401:
        raise PermissionError("auth")
    if r.status_code not in {302, 303, 307}:
        raise RuntimeError(f"Navipod rejected download start: HTTP {r.status_code} {r.text[:300]}")

    for _ in range(10):
        await asyncio.sleep(0.2)
        jobs = await navipod_jobs(token)
        candidates = [
            j
            for j in jobs
            if int(j.get("id") or 0) > before_max
            and (
                j.get("original_input_url") == track_url or j.get("url") == track_url or j.get("input_url") == track_url
            )
        ]
        if candidates:
            return max(int(j["id"]) for j in candidates)
    raise RuntimeError("Download was started but the new Navipod job ID could not be identified")


def set_import_auth_required(import_id: str, error: str = "Navipod session expired") -> None:
    with connect_db() as db:
        db.execute(
            "UPDATE imports SET status='auth_required', last_error=?, updated_at=? WHERE id=?",
            (error, now(), import_id),
        )


def get_import_row(import_id: str) -> sqlite3.Row | None:
    with connect_db() as db:
        return db.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()


def import_token(imp: sqlite3.Row) -> str:
    return decode_token(imp["token_enc"])


async def ensure_destination_playlist(imp: sqlite3.Row, token: str) -> int:
    if imp["playlist_id"]:
        await verify_existing_playlist(token, int(imp["playlist_id"]))
        return int(imp["playlist_id"])

    requested_id = imp["requested_playlist_id"]
    if requested_id:
        await verify_existing_playlist(token, int(requested_id))
        with connect_db() as db:
            db.execute(
                "UPDATE imports SET playlist_id=?, updated_at=? WHERE id=?",
                (int(requested_id), now(), imp["id"]),
            )
        return int(requested_id)

    if imp["destination"] in {"playlist", "both"}:
        name = (imp["playlist_name"] or "Imported playlist").strip()
        pid = await create_playlist(token, name)
        with connect_db() as db:
            db.execute("UPDATE imports SET playlist_id=?, updated_at=? WHERE id=?", (pid, now(), imp["id"]))
        return pid

    temp_name = f"__import_{imp['id'][:8]}__"
    pid = await create_playlist(token, temp_name)
    with connect_db() as db:
        db.execute(
            "UPDATE imports SET playlist_id=?, playlist_name=?, temp_playlist=1, updated_at=? WHERE id=?",
            (pid, temp_name, now(), imp["id"]),
        )
    return pid


async def process_enumerating(imp: sqlite3.Row) -> None:
    token = import_token(imp)
    try:
        await ensure_destination_playlist(imp, token)
        title, entries, warning = await asyncio.to_thread(enumerate_source, imp["source_type"], imp["source_url"])
    except PermissionError:
        set_import_auth_required(imp["id"])
        return
    except Exception as exc:
        log.exception("Enumeration failed for %s", imp["id"])
        with connect_db() as db:
            db.execute(
                "UPDATE imports SET status='failed', last_error=?, updated_at=? WHERE id=?",
                (str(exc)[:2000], now(), imp["id"]),
            )
        return

    with connect_db() as db:
        db.execute("DELETE FROM tracks WHERE import_id=?", (imp["id"],))
        for e in entries:
            db.execute(
                """
                INSERT INTO tracks(import_id,position,source_id,url,title,artist,album,status,last_error)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    imp["id"],
                    e["position"],
                    e.get("source_id"),
                    e.get("url"),
                    e.get("title"),
                    e.get("artist"),
                    e.get("album"),
                    e["status"],
                    e.get("last_error"),
                ),
            )
        db.execute(
            """
            UPDATE imports
               SET source_title=?, total=?, warning=?, status='running', last_error=NULL, updated_at=?
             WHERE id=?
            """,
            (title, len(entries), warning, now(), imp["id"]),
        )
    log.info("Import %s enumerated %d tracks", imp["id"], len(entries))


def schedule_retry(import_id: str, position: int, attempts: int, error: str) -> None:
    if attempts >= MAX_ATTEMPTS:
        with connect_db() as db:
            db.execute(
                """
                UPDATE tracks SET status='permanent_failed', navipod_job_id=NULL, last_error=?
                 WHERE import_id=? AND position=?
                """,
                (error[:2000], import_id, position),
            )
        return
    delay = RETRY_BASE_SECONDS * (4 ** max(0, attempts - 1))
    with connect_db() as db:
        db.execute(
            """
            UPDATE tracks
               SET status='pending', navipod_job_id=NULL, next_attempt_at=?, last_error=?
             WHERE import_id=? AND position=?
            """,
            (now() + delay, error[:2000], import_id, position),
        )


async def process_running(imp: sqlite3.Row) -> None:
    token = import_token(imp)
    try:
        jobs = await navipod_jobs(token)
    except PermissionError:
        set_import_auth_required(imp["id"])
        return
    except Exception as exc:
        with connect_db() as db:
            db.execute("UPDATE imports SET last_error=?, updated_at=? WHERE id=?", (str(exc)[:2000], now(), imp["id"]))
        return

    job_map = {int(j["id"]): j for j in jobs if j.get("id") is not None}
    with connect_db() as db:
        active = db.execute(
            "SELECT * FROM tracks WHERE import_id=? AND status IN ('queued','downloading') ORDER BY position",
            (imp["id"],),
        ).fetchall()

    for tr in active:
        job_id = tr["navipod_job_id"]
        job = job_map.get(int(job_id)) if job_id else None
        if not job:
            # A recent active job should be visible in /api/jobs. If it is not,
            # treat it as interrupted/pruned and retry safely; Navipod dedupe
            # protects against a completed duplicate.
            schedule_retry(imp["id"], tr["position"], tr["attempts"], "Navipod job disappeared from recent job list")
            continue
        st = str(job.get("status") or "").lower()
        if st in SUCCESS_JOB_STATES:
            track_id = job.get("resolved_track_id")
            if not track_id:
                schedule_retry(imp["id"], tr["position"], tr["attempts"], "Navipod completed without resolved_track_id")
                continue
            track_id = int(track_id)
            invalidate_cover_cache(track_id)
            with connect_db() as db:
                db.execute(
                    """
                    UPDATE tracks SET status='downloaded', resolved_track_id=?, last_error=NULL
                     WHERE import_id=? AND position=?
                    """,
                    (track_id, imp["id"], tr["position"]),
                )
        elif st in {"failed", "error"}:
            err = job.get("error") or job.get("detail") or job.get("error_type") or "Navipod download failed"
            schedule_retry(imp["id"], tr["position"], tr["attempts"], str(err))
        else:
            with connect_db() as db:
                db.execute(
                    "UPDATE tracks SET status='downloading' WHERE import_id=? AND position=?",
                    (imp["id"], tr["position"]),
                )

    with connect_db() as db:
        global_active = db.execute("SELECT COUNT(*) FROM tracks WHERE status IN ('queued','downloading')").fetchone()[0]
        capacity = max(0, IMPORT_CONCURRENCY - int(global_active))
        pending = db.execute(
            """
            SELECT * FROM tracks
             WHERE import_id=? AND status='pending' AND next_attempt_at<=?
             ORDER BY position LIMIT ?
            """,
            (imp["id"], now(), capacity),
        ).fetchall()
        playlist_id = db.execute("SELECT playlist_id FROM imports WHERE id=?", (imp["id"],)).fetchone()[0]

    for tr in pending:
        if not tr["url"]:
            with connect_db() as db:
                db.execute(
                    "UPDATE tracks SET status='permanent_failed', last_error='Missing source URL' WHERE import_id=? AND position=?",
                    (imp["id"], tr["position"]),
                )
            continue
        try:
            job_id = await submit_track(token, int(playlist_id), tr["url"])
        except PermissionError:
            set_import_auth_required(imp["id"])
            return
        except Exception as exc:
            # Submission errors do not consume a full source-download attempt
            # unless Navipod actually created a job that we could identify.
            with connect_db() as db:
                db.execute(
                    "UPDATE tracks SET next_attempt_at=?, last_error=? WHERE import_id=? AND position=?",
                    (now() + RETRY_BASE_SECONDS, str(exc)[:2000], imp["id"], tr["position"]),
                )
            continue
        with connect_db() as db:
            db.execute(
                """
                UPDATE tracks
                   SET status='queued', navipod_job_id=?, attempts=attempts+1, last_error=NULL
                 WHERE import_id=? AND position=?
                """,
                (job_id, imp["id"], tr["position"]),
            )

    with connect_db() as db:
        counts = db.execute(
            """
            SELECT
              SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),
              SUM(CASE WHEN status IN ('queued','downloading') THEN 1 ELSE 0 END)
            FROM tracks WHERE import_id=?
            """,
            (imp["id"],),
        ).fetchone()
        if int(counts[0] or 0) == 0 and int(counts[1] or 0) == 0:
            db.execute(
                "UPDATE imports SET status='finalizing', last_error=NULL, updated_at=? WHERE id=?", (now(), imp["id"])
            )
        else:
            db.execute("UPDATE imports SET updated_at=? WHERE id=?", (now(), imp["id"]))


async def finalize_playlist(imp: sqlite3.Row, token: str) -> None:
    if imp["playlist_finalized"]:
        return
    playlist_id = int(imp["playlist_id"])
    r = await navipod_request(token, "GET", f"/api/playlists/{playlist_id}")
    if r.status_code == 401:
        raise PermissionError("auth")
    if r.status_code != 200:
        raise RuntimeError(f"Could not read target playlist: HTTP {r.status_code}")
    current = r.json().get("tracks") or []
    current_ids = [int(x["id"]) for x in current]

    with connect_db() as db:
        imported_rows = db.execute(
            """
            SELECT position,resolved_track_id FROM tracks
             WHERE import_id=? AND status='downloaded' AND resolved_track_id IS NOT NULL
             ORDER BY position
            """,
            (imp["id"],),
        ).fetchall()

    imported_unique: list[int] = []
    seen: set[int] = set()
    for row in imported_rows:
        tid = int(row["resolved_track_id"])
        if tid not in seen:
            imported_unique.append(tid)
            seen.add(tid)

    existing_non_imported = [tid for tid in current_ids if tid not in seen]
    final_ids = existing_non_imported + imported_unique
    body = {"items": [{"track_id": tid, "position": i + 1} for i, tid in enumerate(final_ids)]}
    r = await navipod_request(token, "PATCH", f"/api/playlists/{playlist_id}/reorder", json_body=body, timeout=60.0)
    if r.status_code == 401:
        raise PermissionError("auth")
    if r.status_code != 200:
        raise RuntimeError(f"Playlist reorder failed: HTTP {r.status_code} {r.text[:300]}")
    with connect_db() as db:
        db.execute("UPDATE imports SET playlist_finalized=1, updated_at=? WHERE id=?", (now(), imp["id"]))


async def apply_favorites_batch(imp: sqlite3.Row, token: str) -> int:
    with connect_db() as db:
        rows = db.execute(
            """
            SELECT MIN(position) AS position, resolved_track_id,
                   MAX(favorite_attempts) AS favorite_attempts
              FROM tracks
             WHERE import_id=? AND status='downloaded' AND resolved_track_id IS NOT NULL
               AND favorite_applied=0
             GROUP BY resolved_track_id
             ORDER BY MIN(position) DESC
             LIMIT ?
            """,
            (imp["id"], FAVORITE_BATCH_SIZE),
        ).fetchall()
    applied = 0
    for row in rows:
        tid = int(row["resolved_track_id"])
        r = await navipod_request(token, "POST", f"/api/favorites/{tid}", timeout=20.0)
        if r.status_code == 401:
            raise PermissionError("auth")
        if r.status_code == 200:
            with connect_db() as db:
                db.execute(
                    "UPDATE tracks SET favorite_applied=1, last_error=NULL WHERE import_id=? AND resolved_track_id=?",
                    (imp["id"], tid),
                )
            applied += 1
        else:
            attempts = int(row["favorite_attempts"] or 0) + 1
            if attempts >= MAX_ATTEMPTS:
                with connect_db() as db:
                    db.execute(
                        """
                        UPDATE tracks SET favorite_applied=-1, favorite_attempts=?, last_error=?
                         WHERE import_id=? AND resolved_track_id=?
                        """,
                        (attempts, f"Favorite HTTP {r.status_code}: {r.text[:500]}", imp["id"], tid),
                    )
            else:
                with connect_db() as db:
                    db.execute(
                        """
                        UPDATE tracks SET favorite_attempts=?, last_error=?
                         WHERE import_id=? AND resolved_track_id=?
                        """,
                        (attempts, f"Favorite HTTP {r.status_code}: {r.text[:500]}", imp["id"], tid),
                    )
        await asyncio.sleep(FAVORITE_DELAY_SECONDS)
    return applied


async def delete_temp_playlist(imp: sqlite3.Row, token: str) -> None:
    if not imp["temp_playlist"] or not imp["playlist_id"]:
        return
    r = await navipod_request(token, "DELETE", f"/api/playlists/{int(imp['playlist_id'])}", timeout=30.0)
    if r.status_code == 401:
        raise PermissionError("auth")
    if r.status_code not in {200, 404}:
        raise RuntimeError(f"Could not delete temporary playlist: HTTP {r.status_code}")
    with connect_db() as db:
        db.execute("UPDATE imports SET temp_playlist=0, updated_at=? WHERE id=?", (now(), imp["id"]))


async def process_finalizing(imp: sqlite3.Row) -> None:
    token = import_token(imp)
    try:
        if imp["destination"] in {"playlist", "both"}:
            await finalize_playlist(imp, token)

        if imp["destination"] in {"liked", "both"}:
            await apply_favorites_batch(imp, token)
            with connect_db() as db:
                remaining = db.execute(
                    """
                    SELECT COUNT(DISTINCT resolved_track_id) FROM tracks
                     WHERE import_id=? AND status='downloaded' AND resolved_track_id IS NOT NULL
                       AND favorite_applied=0
                    """,
                    (imp["id"],),
                ).fetchone()[0]
            if remaining:
                return

        # Refetch because playlist_finalized/temp_playlist may have changed.
        fresh = get_import_row(imp["id"])
        if fresh and fresh["temp_playlist"]:
            await delete_temp_playlist(fresh, token)

        with connect_db() as db:
            failed = db.execute(
                "SELECT COUNT(*) FROM tracks WHERE import_id=? AND status='permanent_failed'",
                (imp["id"],),
            ).fetchone()[0]
            fav_failed = db.execute(
                "SELECT COUNT(DISTINCT resolved_track_id) FROM tracks WHERE import_id=? AND favorite_applied=-1",
                (imp["id"],),
            ).fetchone()[0]
            status = "completed" if int(failed or 0) == 0 and int(fav_failed or 0) == 0 else "completed_with_errors"
            db.execute("UPDATE imports SET status=?, updated_at=? WHERE id=?", (status, now(), imp["id"]))
    except PermissionError:
        set_import_auth_required(imp["id"])
    except Exception as exc:
        log.exception("Finalization error for %s", imp["id"])
        with connect_db() as db:
            db.execute("UPDATE imports SET last_error=?, updated_at=? WHERE id=?", (str(exc)[:2000], now(), imp["id"]))


async def manager_loop() -> None:
    while True:
        try:
            with connect_db() as db:
                rows = db.execute(
                    "SELECT * FROM imports WHERE status IN ('enumerating','running','finalizing') ORDER BY created_at"
                ).fetchall()
            for imp in rows:
                if imp["status"] == "enumerating":
                    await process_enumerating(imp)
                elif imp["status"] == "running":
                    await process_running(imp)
                elif imp["status"] == "finalizing":
                    await process_finalizing(imp)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Manager loop error")
        await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(manager_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Navipod Playlist Importer", version="1.0", lifespan=lifespan)


def is_same_origin(source: str) -> bool:
    try:
        parsed = urlparse(source)
    except Exception:
        return False
    return (
        parsed.scheme.lower() == NAVIPOD_PUBLIC_URL.scheme.lower()
        and parsed.netloc.lower() == NAVIPOD_PUBLIC_HOST.lower()
    )


@app.middleware("http")
async def enforce_same_origin(request: Request, call_next):
    if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and request.cookies.get("access_token"):
        source = request.headers.get("origin") or request.headers.get("referer") or ""
        if not is_same_origin(source):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected"})
    return await call_next(request)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/playlists")
async def proxy_playlists(access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")
    owner = await validate_navipod_session(access_token)
    r = await navipod_request(access_token, "GET", "/api/playlists")
    return {"owner": owner, "playlists": r.json()}


@app.post("/api/imports", status_code=201)
async def create_import(body: ImportCreate, access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")
    owner = await validate_navipod_session(access_token)
    try:
        source_type = detect_source(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    destination = body.destination.strip().lower()
    if destination not in {"playlist", "liked", "both"}:
        raise HTTPException(status_code=400, detail="destination must be playlist, liked or both")
    if destination in {"playlist", "both"} and not body.playlist_id and not (body.playlist_name or "").strip():
        raise HTTPException(status_code=400, detail="A playlist name or existing playlist_id is required")
    if body.playlist_id:
        try:
            await verify_existing_playlist(access_token, int(body.playlist_id))
        except PermissionError:
            raise HTTPException(status_code=401, detail="Navipod session expired")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    import_id = uuid.uuid4().hex
    ts = now()
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO imports(
                id,owner,source_url,source_type,destination,requested_playlist_id,
                playlist_name,status,token_enc,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                import_id,
                owner,
                body.url.strip(),
                source_type,
                destination,
                body.playlist_id,
                (body.playlist_name or "").strip() or None,
                "enumerating",
                encode_token(access_token),
                ts,
                ts,
            ),
        )
    return {"id": import_id, "status": "enumerating", "owner": owner}


def require_owner(import_id: str, token: str) -> sqlite3.Row:
    owner = jwt_subject_unverified(token)
    imp = get_import_row(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Import not found")
    if not owner or owner != imp["owner"]:
        raise HTTPException(status_code=403, detail="Import belongs to another Navipod user")
    return imp


@app.get("/api/imports/{import_id}")
async def import_status(import_id: str, access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")
    await validate_navipod_session(access_token)
    imp = require_owner(import_id, access_token)
    with connect_db() as db:
        counts = db.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status='downloaded' THEN 1 ELSE 0 END) AS downloaded,
              SUM(CASE WHEN status IN ('queued','downloading') THEN 1 ELSE 0 END) AS active,
              SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN status='permanent_failed' THEN 1 ELSE 0 END) AS failed,
              SUM(CASE WHEN favorite_applied=1 THEN 1 ELSE 0 END) AS liked,
              SUM(CASE WHEN favorite_applied=-1 THEN 1 ELSE 0 END) AS favorite_failed
            FROM tracks WHERE import_id=?
            """,
            (import_id,),
        ).fetchone()
        failures = db.execute(
            """
            SELECT position,title,artist,last_error,attempts FROM tracks
             WHERE import_id=? AND (status='permanent_failed' OR favorite_applied=-1)
             ORDER BY position LIMIT 100
            """,
            (import_id,),
        ).fetchall()
    total = int(counts["total"] or imp["total"] or 0)
    downloaded = int(counts["downloaded"] or 0)
    failed = int(counts["failed"] or 0)
    processed = downloaded + failed
    return {
        "id": imp["id"],
        "owner": imp["owner"],
        "source_url": imp["source_url"],
        "source_type": imp["source_type"],
        "source_title": imp["source_title"],
        "destination": imp["destination"],
        "playlist_id": imp["playlist_id"],
        "playlist_name": imp["playlist_name"],
        "status": imp["status"],
        "warning": imp["warning"],
        "last_error": imp["last_error"],
        "total": total,
        "downloaded": downloaded,
        "active": int(counts["active"] or 0),
        "pending": int(counts["pending"] or 0),
        "failed": failed,
        "liked": int(counts["liked"] or 0),
        "favorite_failed": int(counts["favorite_failed"] or 0),
        "progress": round((processed / total * 100.0), 1) if total else 0.0,
        "failures": [dict(x) for x in failures],
    }


@app.post("/api/imports/{import_id}/refresh-covers")
async def refresh_covers(import_id: str, access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")

    await validate_navipod_session(access_token)
    require_owner(import_id, access_token)

    with connect_db() as db:
        rows = db.execute(
            """
            SELECT DISTINCT resolved_track_id
              FROM tracks
             WHERE import_id=? AND resolved_track_id IS NOT NULL
            """,
            (import_id,),
        ).fetchall()

    ids = [int(row["resolved_track_id"]) for row in rows]
    removed = sum(1 for track_id in ids if invalidate_cover_cache(track_id))

    response = JSONResponse(
        {
            "ok": True,
            "tracks": len(ids),
            "server_cache_removed": removed,
        }
    )

    # Same HTTPS origin as Navipod. Clear only browser cache, not cookies/session.
    response.headers["Clear-Site-Data"] = '"cache"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/imports/{import_id}/reauth")
async def reauth(import_id: str, access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")
    owner = await validate_navipod_session(access_token)
    imp = get_import_row(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Import not found")
    if imp["owner"] != owner:
        raise HTTPException(status_code=403, detail="Import belongs to another user")
    next_status = "running"
    if imp["status"] in {"enumerating", "auth_required"} and int(imp["total"] or 0) == 0:
        next_status = "enumerating"
    elif imp["status"] == "auth_required":
        with connect_db() as db:
            left = db.execute(
                "SELECT COUNT(*) FROM tracks WHERE import_id=? AND status IN ('pending','queued','downloading')",
                (import_id,),
            ).fetchone()[0]
        next_status = "running" if left else "finalizing"
    with connect_db() as db:
        db.execute(
            "UPDATE imports SET token_enc=?, status=?, last_error=NULL, updated_at=? WHERE id=?",
            (encode_token(access_token), next_status, now(), import_id),
        )
    return {"ok": True, "status": next_status}


@app.post("/api/imports/{import_id}/retry-failed")
async def retry_failed(import_id: str, access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")
    owner = await validate_navipod_session(access_token)
    imp = get_import_row(import_id)
    if not imp or imp["owner"] != owner:
        raise HTTPException(status_code=404, detail="Import not found")
    with connect_db() as db:
        cur = db.execute(
            """
            UPDATE tracks
               SET status='pending', attempts=0, navipod_job_id=NULL, next_attempt_at=0,
                   last_error=NULL, favorite_applied=0, favorite_attempts=0
             WHERE import_id=? AND status='permanent_failed' AND url IS NOT NULL
            """,
            (import_id,),
        )
        fav_cur = db.execute(
            """
            UPDATE tracks
               SET favorite_applied=0, favorite_attempts=0, last_error=NULL
             WHERE import_id=? AND status='downloaded' AND favorite_applied=-1
            """,
            (import_id,),
        )
        db.execute(
            """
            UPDATE imports
               SET token_enc=?, status='running', playlist_finalized=0, last_error=NULL, updated_at=?
             WHERE id=?
            """,
            (encode_token(access_token), now(), import_id),
        )
    return {"ok": True, "retried": cur.rowcount, "favorite_retried": fav_cur.rowcount}


@app.post("/api/imports/{import_id}/pause")
async def pause_import(import_id: str, access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")
    owner = await validate_navipod_session(access_token)
    imp = get_import_row(import_id)
    if not imp or imp["owner"] != owner:
        raise HTTPException(status_code=404, detail="Import not found")
    with connect_db() as db:
        db.execute("UPDATE imports SET status='paused', updated_at=? WHERE id=?", (now(), import_id))
    return {"ok": True, "note": "No new jobs will be submitted; currently active Navipod jobs may finish."}


@app.post("/api/imports/{import_id}/resume")
async def resume_import(import_id: str, access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Navipod session cookie missing")
    owner = await validate_navipod_session(access_token)
    imp = get_import_row(import_id)
    if not imp or imp["owner"] != owner:
        raise HTTPException(status_code=404, detail="Import not found")
    with connect_db() as db:
        left = db.execute(
            "SELECT COUNT(*) FROM tracks WHERE import_id=? AND status IN ('pending','queued','downloading')",
            (import_id,),
        ).fetchone()[0]
        next_status = "running" if left else "finalizing"
        db.execute(
            "UPDATE imports SET token_enc=?, status=?, last_error=NULL, updated_at=? WHERE id=?",
            (encode_token(access_token), next_status, now(), import_id),
        )
    return {"ok": True, "status": next_status}


STATUS_HTML = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Navipod Importer</title>
<style>
:root{color-scheme:dark}body{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:40px auto;padding:0 18px;background:#111;color:#eee}a{color:#9ecbff}.card{background:#1a1a1a;border:1px solid #333;border-radius:14px;padding:20px;margin:16px 0}.bar{height:14px;background:#333;border-radius:8px;overflow:hidden}.fill{height:100%;background:#7aa2ff;width:0;transition:width .3s}button{background:#2b2b2b;color:#fff;border:1px solid #555;border-radius:9px;padding:10px 14px;margin:4px;cursor:pointer}button:hover{background:#3a3a3a}.bad{color:#ff9e9e}.warn{color:#ffd479}.ok{color:#9ee6ad}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px;border-bottom:1px solid #333;text-align:left}code{word-break:break-all}.muted{color:#aaa}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}.metric{background:#151515;border-radius:8px;padding:10px}.metric b{display:block;font-size:1.4rem}</style>
</head>
<body>
<h1>Navipod Playlist Importer</h1>
<div class="card">
  <h2 id="title">Cargando…</h2>
  <div class="bar"><div class="fill" id="fill"></div></div>
  <p><strong id="status"></strong> · <span id="progress"></span></p>
  <div class="grid">
    <div class="metric"><b id="downloaded">0</b>descargadas</div>
    <div class="metric"><b id="active">0</b>activas</div>
    <div class="metric"><b id="pending">0</b>pendientes</div>
    <div class="metric"><b id="failed">0</b>fallidas</div>
    <div class="metric"><b id="liked">0</b>liked</div>
  </div>
  <p class="muted" id="dest"></p>
  <p class="warn" id="warning"></p>
  <p class="bad" id="error"></p>
  <button id="reauth">Revalidar sesión</button>
  <button id="pause">Pausar</button>
  <button id="resume">Reanudar</button>
  <button id="retry">Reintentar fallidas</button>
  <button id="covers">Refrescar carátulas</button>
  <a href="/" style="margin-left:10px">Volver a Navipod</a>
  <p class="ok" id="coverMsg"></p>
</div>
<div class="card" id="failsCard" style="display:none">
  <h3>Fallos</h3><table><thead><tr><th>#</th><th>Canción</th><th>Error</th></tr></thead><tbody id="fails"></tbody></table>
</div>
<script>
const id=location.pathname.split('/').filter(Boolean).pop();
const api='/importer/api/imports/'+encodeURIComponent(id);
async function post(s){const r=await fetch(api+s,{method:'POST',credentials:'same-origin'});if(!r.ok)alert(await r.text());await refresh()}
document.getElementById('reauth').onclick=()=>post('/reauth');
document.getElementById('pause').onclick=()=>post('/pause');
document.getElementById('resume').onclick=()=>post('/resume');
document.getElementById('retry').onclick=()=>post('/retry-failed');

let coverRefreshDone=false;
let coverRefreshInFlight=false;

async function refreshCovers(manual=false){
  if(coverRefreshInFlight || (coverRefreshDone && !manual)) return;
  coverRefreshInFlight=true;
  try{
    const r=await fetch(api+'/refresh-covers',{
      method:'POST',
      credentials:'same-origin',
      cache:'no-store'
    });
    if(!r.ok) throw new Error(await r.text());
    const d=await r.json();
    coverRefreshDone=true;
    document.getElementById('coverMsg').textContent=
      'Carátulas refrescadas: '+d.tracks+' pistas.';
  }catch(e){
    document.getElementById('coverMsg').textContent=
      'No se pudo refrescar la caché de carátulas: '+e.message;
  }finally{
    coverRefreshInFlight=false;
  }
}

document.getElementById('covers').onclick=()=>refreshCovers(true);

async function refresh(){
  const r=await fetch(api,{credentials:'same-origin'});
  if(r.status===401){document.getElementById('error').textContent='Tu sesión de Navipod ha caducado. Inicia sesión de nuevo y vuelve a esta página.';return}
  if(!r.ok){document.getElementById('error').textContent=await r.text();return}
  const d=await r.json();
  document.getElementById('title').textContent=d.source_title||'Enumerando playlist…';
  document.getElementById('status').textContent=d.status;
  document.getElementById('progress').textContent=(d.total?d.progress.toFixed(1):'0.0')+'% ('+(d.downloaded+d.failed)+'/'+d.total+')';
  document.getElementById('fill').style.width=Math.max(0,Math.min(100,d.progress))+'%';
  for(const k of ['downloaded','active','pending','failed','liked']) document.getElementById(k).textContent=d[k]||0;
  document.getElementById('dest').textContent='Destino: '+d.destination+(d.playlist_name?' · '+d.playlist_name:'');
  document.getElementById('warning').textContent=d.warning||'';
  document.getElementById('error').textContent=d.last_error||'';
  const tbody=document.getElementById('fails'); tbody.innerHTML='';
  for(const f of d.failures||[]){const tr=document.createElement('tr');tr.innerHTML='<td>'+f.position+'</td><td></td><td></td>';tr.children[1].textContent=((f.artist?f.artist+' - ':'')+(f.title||''));tr.children[2].textContent=f.last_error||'';tbody.appendChild(tr)}
  document.getElementById('failsCard').style.display=(d.failures&&d.failures.length)?'block':'none';

  if((d.status==='completed'||d.status==='completed_with_errors')&&!coverRefreshDone){
    refreshCovers(false);
  }
}
refresh();setInterval(refresh,3000);
</script>
</body></html>
"""


@app.get("/imports/{import_id}", response_class=HTMLResponse)
async def import_page(import_id: str):
    # API calls on the page enforce ownership/auth; the HTML itself contains no private data.
    return HTMLResponse(STATUS_HTML)


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(
        "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:30px'>"
        "<h2>Navipod Playlist Importer</h2><p>Servicio activo. Inicia las importaciones desde el bookmarklet.</p>"
        "<p><a style='color:#9ecbff' href='/'>Volver a Navipod</a></p></body></html>"
    )
