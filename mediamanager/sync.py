"""One-way Jellyfin → database sync.

Everything is fetched before the first write, and all writes share one transaction,
so a failed sync leaves the database as it was. Nothing is ever written to Jellyfin.
"""

from __future__ import annotations

import json
import sqlite3

from . import items
from .db import norm_title, now, sort_title
from .media_info import label_from_filename, resolution_label
from .jellyfin import JellyfinClient

SYNCED_FIELDS = (
    "title", "year", "country", "summary", "poster_url", "trailer_url",
    "release_date", "community_rating", "runtime_minutes",
)
TICKS_PER_MINUTE = 600_000_000


def image_url(item_id: str, tag: str | None) -> str | None:
    # Served through our proxy, which keeps the API key out of the browser.
    return f"/api/jellyfin/image/{item_id}?tag={tag}" if tag else None


def _source_row(source: dict) -> tuple:
    """(path, size, width, height, label) for one version of a title."""
    video = next((s for s in source.get("MediaStreams") or [] if s.get("Type") == "Video"), {})
    width, height = video.get("Width"), video.get("Height")
    label = resolution_label(width, height) or label_from_filename(source["Path"])
    return source["Path"], source.get("Size"), width, height, label


def _files(raw: dict) -> list[tuple]:
    sources = [_source_row(s) for s in raw.get("MediaSources") or [] if s.get("Path")]
    if sources:
        return sources
    # A series' own path is its folder, which has no video stream or label.
    return [(raw["Path"], None, None, None, None)] if raw.get("Path") else []


def _tmdb_id(raw: dict) -> int | None:
    ids = raw.get("ProviderIds") or {}
    value = ids.get("Tmdb") or ids.get("TheMovieDb")
    return int(value) if value and str(value).isdigit() else None


def _fields(raw: dict) -> dict:
    trailers = raw.get("RemoteTrailers") or []
    countries = raw.get("ProductionLocations") or []
    ticks = raw.get("RunTimeTicks")
    return {
        "title": raw.get("Name") or "Untitled",
        "year": raw.get("ProductionYear"),
        "country": ", ".join(countries) or None,
        "summary": raw.get("Overview"),
        "poster_url": image_url(raw["Id"], (raw.get("ImageTags") or {}).get("Primary")),
        "trailer_url": trailers[0].get("Url") if trailers else None,
        "release_date": (raw.get("PremiereDate") or "")[:10] or None,
        "community_rating": raw.get("CommunityRating"),
        "runtime_minutes": round(ticks / TICKS_PER_MINUTE) if ticks else None,
    }


def _credits(conn: sqlite3.Connection, item_id: int, raw: dict) -> None:
    actors = [p for p in raw.get("People") or [] if p.get("Type") == "Actor"]
    if not actors:  # Jellyfin having none doesn't erase a cast filled in from TMDB
        return
    conn.execute("DELETE FROM credit WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT INTO credit (item_id, name, character, image_url, ord) VALUES (?, ?, ?, ?, ?)",
        [
            (
                item_id, p.get("Name") or "?", p.get("Role"),
                image_url(p["Id"], p.get("PrimaryImageTag")) if p.get("Id") else None, i,
            )
            for i, p in enumerate(actors[:30])
        ],
    )


def _genres(conn: sqlite3.Connection, item_id: int, raw: dict) -> None:
    genres = raw.get("Genres") or []
    if genres:  # an empty list doesn't erase what TMDB already supplied
        conn.execute("DELETE FROM item_genre WHERE item_id = ?", (item_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO item_genre (item_id, name) VALUES (?, ?)",
            [(item_id, name) for name in genres],
        )


def _locations(conn, item_id: int, episode_id: int | None, raw: dict) -> None:
    conn.executemany(
        "INSERT INTO location (item_id, episode_id, origin, path, size_bytes, width, height, resolution)"
        " VALUES (?, ?, 'jellyfin', ?, ?, ?, ?, ?)",
        [(item_id, episode_id, *row) for row in _files(raw)],
    )


def _find_existing(conn, kind: str, raw: dict, tmdb_id: int | None) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM media_item WHERE jellyfin_id = ?", (raw["Id"],)).fetchone()
    if row or not tmdb_id:
        return row
    # A title saved from TMDB or a scan has now arrived on Jellyfin: same record.
    return conn.execute(
        "SELECT * FROM media_item WHERE kind = ? AND tmdb_id = ? AND jellyfin_id IS NULL",
        (kind, tmdb_id),
    ).fetchone()


def _upsert_title(conn, raw: dict, stats: dict) -> int:
    kind = "series" if raw.get("Type") == "Series" else "movie"
    tmdb_id = _tmdb_id(raw)
    imdb_id = (raw.get("ProviderIds") or {}).get("Imdb")
    fields = _fields(raw)
    existing = _find_existing(conn, kind, raw, tmdb_id)
    stamp = now()

    if existing is None:
        item_id = conn.execute(
            """INSERT INTO media_item (kind, title, norm_title, sort_title, year, country, summary,
                   poster_url, trailer_url, release_date, community_rating, runtime_minutes,
                   source, on_jellyfin, jellyfin_id, tmdb_id, imdb_id, added_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'jellyfin', 1, ?, ?, ?, ?, ?)""",
            (
                kind, fields["title"], norm_title(fields["title"]), sort_title(fields["title"]),
                fields["year"], fields["country"], fields["summary"], fields["poster_url"],
                fields["trailer_url"], fields["release_date"], fields["community_rating"],
                fields["runtime_minutes"], raw["Id"], tmdb_id, imdb_id,
                raw.get("DateCreated") or stamp, stamp,
            ),
        ).lastrowid
        stats["added"] += 1
    else:
        item_id = existing["id"]
        locked = set(json.loads(existing["locked_fields"]))
        changes = {
            name: fields[name]
            for name in SYNCED_FIELDS
            if name not in locked
            # An empty Jellyfin value doesn't erase what TMDB already supplied.
            and fields[name] is not None and fields[name] != existing[name]
        }
        was_linked = existing["on_jellyfin"] and existing["jellyfin_id"] == raw["Id"]
        if changes or not was_linked:
            stats["updated"] += 1
        if "title" in changes:
            changes["norm_title"] = norm_title(changes["title"])
            changes["sort_title"] = sort_title(changes["title"])
        assignments = {
            **changes,
            "on_jellyfin": 1,
            "jellyfin_id": raw["Id"],
            "tmdb_id": tmdb_id or existing["tmdb_id"],
            "imdb_id": imdb_id or existing["imdb_id"],
        }
        if changes or not was_linked:
            assignments["updated_at"] = stamp
        columns = ", ".join(f"{name} = ?" for name in assignments)
        conn.execute(
            f"UPDATE media_item SET {columns} WHERE id = ?", (*assignments.values(), item_id)
        )

    _credits(conn, item_id, raw)
    _genres(conn, item_id, raw)
    conn.execute("DELETE FROM location WHERE item_id = ? AND origin = 'jellyfin'", (item_id,))
    _locations(conn, item_id, None, raw)
    return item_id


def _upsert_episode(conn, item_id: int, raw: dict) -> int:
    season, number = raw.get("ParentIndexNumber"), raw.get("IndexNumber")
    row = conn.execute("SELECT id FROM episode WHERE jellyfin_id = ?", (raw["Id"],)).fetchone()
    if row is None and season is not None and number is not None:
        row = conn.execute(
            """SELECT id FROM episode WHERE item_id = ? AND season_number = ?
               AND episode_number = ? AND jellyfin_id IS NULL""",
            (item_id, season, number),
        ).fetchone()
    values = (
        item_id, season, number, raw.get("Name"), raw.get("Overview"),
        (raw.get("PremiereDate") or "")[:10] or None, raw["Id"],
    )
    if row:
        conn.execute(
            """UPDATE episode SET item_id = ?, season_number = ?, episode_number = ?, title = ?,
                   summary = ?, air_date = ?, jellyfin_id = ? WHERE id = ?""",
            (*values, row["id"]),
        )
        return row["id"]
    return conn.execute(
        """INSERT INTO episode (item_id, season_number, episode_number, title, summary,
               air_date, jellyfin_id) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        values,
    ).lastrowid


def _exclusions(conn) -> tuple[set[str], set[tuple[str, int]]]:
    rows = conn.execute("SELECT kind, jellyfin_id, tmdb_id FROM excluded_item").fetchall()
    return (
        {r["jellyfin_id"] for r in rows if r["jellyfin_id"]},
        {(r["kind"], r["tmdb_id"]) for r in rows if r["tmdb_id"]},
    )


def run_sync(conn: sqlite3.Connection, client: JellyfinClient, progress=lambda _: None) -> dict:
    progress("Fetching titles from Jellyfin")
    titles = client.titles()
    progress(f"Fetching episodes for {len(titles)} titles")
    episodes = client.episodes()
    progress("Saving")

    stats = {"added": 0, "updated": 0, "excluded": 0, "removed": 0, "episodes": 0,
             "seen": len(titles)}
    excluded_ids, excluded_tmdb = _exclusions(conn)

    with conn:
        series_items: dict[str, int] = {}
        seen_jellyfin: set[str] = set()
        for raw in titles:
            kind = "series" if raw.get("Type") == "Series" else "movie"
            seen_jellyfin.add(raw["Id"])
            if raw["Id"] in excluded_ids or (kind, _tmdb_id(raw)) in excluded_tmdb:
                stats["excluded"] += 1
                continue
            item_id = _upsert_title(conn, raw, stats)
            if kind == "series":
                series_items[raw["Id"]] = item_id

        for item_id in series_items.values():
            conn.execute(
                "DELETE FROM location WHERE item_id = ? AND origin = 'jellyfin'"
                " AND episode_id IS NOT NULL",
                (item_id,),
            )
        seen_episodes: set[str] = set()
        for raw in episodes:
            item_id = series_items.get(raw.get("SeriesId"))
            if item_id is None:
                continue
            episode_id = _upsert_episode(conn, item_id, raw)
            _locations(conn, item_id, episode_id, raw)
            seen_episodes.add(raw["Id"])
            stats["episodes"] += 1

        # Episodes gone from Jellyfin. One backed by a scanned file stays, unlinked.
        for item_id in series_items.values():
            rows = conn.execute(
                "SELECT id, jellyfin_id FROM episode WHERE item_id = ? AND jellyfin_id IS NOT NULL",
                (item_id,),
            ).fetchall()
            for row in rows:
                if row["jellyfin_id"] in seen_episodes:
                    continue
                has_file = conn.execute(
                    "SELECT 1 FROM location WHERE episode_id = ?", (row["id"],)
                ).fetchone()
                if has_file:
                    conn.execute("UPDATE episode SET jellyfin_id = NULL WHERE id = ?", (row["id"],))
                else:
                    conn.execute("DELETE FROM episode WHERE id = ?", (row["id"],))

        # Gone from Jellyfin: flag it, keep the record. This only changes a flag, so an
        # API key with narrower library access can't wipe anything.
        linked = conn.execute(
            "SELECT id, jellyfin_id FROM media_item WHERE on_jellyfin = 1"
        ).fetchall()
        for row in linked:
            if row["jellyfin_id"] not in seen_jellyfin:
                conn.execute("UPDATE media_item SET on_jellyfin = 0 WHERE id = ?", (row["id"],))
                conn.execute(
                    "DELETE FROM location WHERE item_id = ? AND origin = 'jellyfin'", (row["id"],)
                )
                stats["removed"] += 1

        for row in conn.execute("SELECT id FROM media_item").fetchall():
            items.refresh_resolution(conn, row["id"])

        conn.execute(
            "INSERT INTO sync_run (finished_at, status, stats) VALUES (?, 'ok', ?)",
            (now(), json.dumps(stats)),
        )
    return stats
