from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_item (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('movie', 'series')),
    title         TEXT NOT NULL,
    norm_title    TEXT NOT NULL,
    sort_title    TEXT NOT NULL DEFAULT '',
    year          INTEGER,
    release_date  TEXT,
    community_rating REAL,
    runtime_minutes  INTEGER,
    resolution    TEXT,
    gaps_checked_at TEXT,
    country       TEXT,
    original_language TEXT,
    summary       TEXT,
    poster_url    TEXT,
    trailer_url   TEXT,
    source        TEXT NOT NULL CHECK (source IN ('jellyfin', 'tmdb', 'scan', 'csv')),
    on_jellyfin   INTEGER NOT NULL DEFAULT 0,
    jellyfin_id   TEXT UNIQUE,
    tmdb_id       INTEGER,
    imdb_id       TEXT,
    locked_fields TEXT NOT NULL DEFAULT '[]',
    added_at      TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_norm ON media_item (norm_title);
CREATE INDEX IF NOT EXISTS idx_item_tmdb ON media_item (kind, tmdb_id);

CREATE TABLE IF NOT EXISTS item_genre (
    item_id INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    PRIMARY KEY (item_id, name)
);
CREATE INDEX IF NOT EXISTS idx_genre_name ON item_genre (name);

CREATE TABLE IF NOT EXISTS episode (
    id             INTEGER PRIMARY KEY,
    item_id        INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    season_number  INTEGER,
    episode_number INTEGER,
    title          TEXT,
    summary        TEXT,
    air_date       TEXT,
    jellyfin_id    TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_episode_item ON episode (item_id, season_number, episode_number);

-- Where a title's or episode's file lives. Jellyfin paths are stored as Jellyfin
-- reports them and translated when read, so editing a mapping needs no re-sync.
-- Scanned paths are stored per volume, so a new mount point or drive letter is fine.
CREATE TABLE IF NOT EXISTS location (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    episode_id INTEGER REFERENCES episode (id) ON DELETE CASCADE,
    origin     TEXT NOT NULL CHECK (origin IN ('jellyfin', 'scan')),
    path       TEXT NOT NULL,
    volume_id  TEXT,
    rel_path   TEXT,
    size_bytes INTEGER,
    width      INTEGER,
    height     INTEGER,
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_location_item ON location (item_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_location_volume
    ON location (volume_id, rel_path) WHERE origin = 'scan';

CREATE TABLE IF NOT EXISTS credit (
    id        INTEGER PRIMARY KEY,
    item_id   INTEGER NOT NULL REFERENCES media_item (id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    character TEXT,
    image_url TEXT,
    ord       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_credit_item ON credit (item_id);
CREATE INDEX IF NOT EXISTS idx_credit_name ON credit (name);

-- Deleted records that a Jellyfin sync must not bring back.
CREATE TABLE IF NOT EXISTS excluded_item (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    year        INTEGER,
    jellyfin_id TEXT,
    tmdb_id     INTEGER,
    excluded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delete_log (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL,
    title      TEXT NOT NULL,
    snapshot   TEXT NOT NULL,
    deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_root (
    id              INTEGER PRIMARY KEY,
    label           TEXT NOT NULL,
    volume_id       TEXT NOT NULL,
    rel_path        TEXT NOT NULL,
    last_path       TEXT NOT NULL,
    last_scanned_at TEXT,
    UNIQUE (volume_id, rel_path)
);

CREATE TABLE IF NOT EXISTS scan_candidate (
    id           INTEGER PRIMARY KEY,
    root_id      INTEGER REFERENCES scan_root (id) ON DELETE SET NULL,
    volume_id    TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    abs_path     TEXT NOT NULL,
    size_bytes   INTEGER,
    width        INTEGER,
    height       INTEGER,
    resolution   TEXT,
    status       TEXT NOT NULL CHECK (status IN ('pending', 'ignored')),
    kind         TEXT NOT NULL,
    parsed_title TEXT NOT NULL,
    parsed_year  INTEGER,
    season       INTEGER,
    episode      INTEGER,
    matches      TEXT NOT NULL DEFAULT '[]',
    confidence   REAL NOT NULL DEFAULT 0,
    found_at     TEXT NOT NULL,
    UNIQUE (volume_id, rel_path)
);

CREATE TABLE IF NOT EXISTS sync_run (
    id          INTEGER PRIMARY KEY,
    finished_at TEXT NOT NULL,
    status      TEXT NOT NULL,
    stats       TEXT NOT NULL
);
"""


# Columns added after the first release, for databases created before them.
ADDED_COLUMNS = {
    "media_item": {
        "sort_title": "TEXT NOT NULL DEFAULT ''",
        "release_date": "TEXT",
        "community_rating": "REAL",
        "runtime_minutes": "INTEGER",
        "resolution": "TEXT",
        "gaps_checked_at": "TEXT",
        "original_language": "TEXT",
    },
    "location": {"width": "INTEGER", "height": "INTEGER", "resolution": "TEXT"},
    "scan_candidate": {"width": "INTEGER", "height": "INTEGER", "resolution": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                if name == "original_language":
                    # Only TMDB knows it: have the next sync look up titles already checked.
                    conn.execute("UPDATE media_item SET gaps_checked_at = NULL")
    for row in conn.execute("SELECT id, title FROM media_item WHERE sort_title = ''").fetchall():
        conn.execute(
            "UPDATE media_item SET sort_title = ? WHERE id = ?", (sort_title(row["title"]), row["id"])
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_sort ON media_item (sort_title)")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "media.db", timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_title(title: str) -> str:
    """Comparison form of a title: no case, accents or punctuation differences."""
    decomposed = unicodedata.normalize("NFKD", title)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", stripped.replace("&", " and ").casefold()).split())


def sort_title(title: str) -> str:
    """Sort key following Jellyfin: leading articles ignored, numbers in numeric order."""
    key = re.sub(r"^(?:the|a|an)\s+", "", norm_title(title))
    return re.sub(r"\d+", lambda m: m.group().zfill(10), key)
