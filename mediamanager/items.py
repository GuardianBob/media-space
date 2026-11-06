"""Library records: browsing, editing, saving from TMDB, and verified deletion."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from . import media_info, paths, volumes
from .db import norm_title, now, sort_title
from .filename_parser import parse_path
from .tmdb import TmdbClient

EDITABLE_FIELDS = ("title", "year", "country", "summary", "poster_url", "trailer_url")
# Sort name → (SQL expression, default direction). Blank values always sort last.
SORTS = {
    "title": ("sort_title", "ASC"),
    # Records without a full date (e.g. from a CSV) sort by their year.
    "release": ("COALESCE(release_date, printf('%04d', year))", "DESC"),
    "added": ("added_at", "DESC"),
    "rating": ("community_rating", "DESC"),
    "runtime": ("runtime_minutes", "DESC"),
    "resolution": (
        "CASE resolution "
        + " ".join(f"WHEN '{label}' THEN {i}" for i, label in enumerate(media_info.RESOLUTIONS))
        + " END",
        "DESC",
    ),
}
CARD_COLUMNS = "id, kind, title, year, poster_url, source, on_jellyfin, jellyfin_id, tmdb_id, resolution"


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def _card(row: sqlite3.Row) -> dict:
    card = dict(row)
    card["on_jellyfin"] = bool(card["on_jellyfin"])
    # Was on Jellyfin once; a later sync no longer found it.
    card["removed_from_jellyfin"] = bool(card["jellyfin_id"]) and not card["on_jellyfin"]
    del card["jellyfin_id"]
    return card


def list_items(
    conn, q: str = "", kind: str = "", library: str = "", sort: str = "title",
    page: int = 1, size: int = 40, order: str = "", letter: str = "", genre: str = "",
    year: int | None = None, resolution: str = "",
    language: str = "",
) -> dict:
    where, params = [], []
    if q.strip():
        where.append(
            "(norm_title LIKE ? OR id IN (SELECT item_id FROM credit WHERE name LIKE ?))"
        )
        params += [f"%{norm_title(q)}%", f"%{q.strip()}%"]
    if kind in ("movie", "series"):
        where.append("kind = ?")
        params.append(kind)
    if library == "jellyfin":
        where.append("on_jellyfin = 1")
    elif library == "not_jellyfin":
        where.append("on_jellyfin = 0")
    if letter == "#":
        # Sort titles are lower-case: anything outside a-z belongs under "#".
        where.append("substr(sort_title, 1, 1) NOT GLOB '[a-z]'")
    elif len(letter) == 1 and letter.isascii() and letter.isalpha():
        where.append("substr(sort_title, 1, 1) = ?")
        params.append(letter.casefold())
    if genre:
        where.append("id IN (SELECT item_id FROM item_genre WHERE name = ?)")
        params.append(genre)
    if year is not None:
        where.append("year = ?")
        params.append(year)
    if resolution:
        where.append("resolution = ?")
        params.append(resolution)
    if language:
        where.append("original_language = ?")
        params.append(language)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM media_item {clause}", params).fetchone()[0]
    column, default_order = SORTS.get(sort, SORTS["title"])
    direction = {"asc": "ASC", "desc": "DESC"}.get(order, default_order)
    # sort_title, then id: a stable order, so paging never repeats or skips a title.
    rows = conn.execute(
        f"SELECT {CARD_COLUMNS} FROM media_item {clause} "
        f"ORDER BY {column} {direction} NULLS LAST, sort_title ASC, id ASC LIMIT ? OFFSET ?",
        (*params, size, (page - 1) * size),
    ).fetchall()
    return {"items": [_card(r) for r in rows], "total": total, "page": page, "size": size}


def filter_options(conn) -> dict:
    """The genres and years present in the library, for the filter menus."""
    return {
        "genres": [
            r["name"] for r in conn.execute("SELECT DISTINCT name FROM item_genre ORDER BY name")
        ],
        "years": [
            r["year"] for r in conn.execute(
                "SELECT DISTINCT year FROM media_item WHERE year IS NOT NULL ORDER BY year DESC"
            )
        ],
        "languages": [
            r["original_language"] for r in conn.execute(
                "SELECT DISTINCT original_language FROM media_item "
                "WHERE original_language IS NOT NULL AND original_language != '' "
                "ORDER BY original_language"
            )
        ],
        "resolutions": sorted(
            (r["resolution"] for r in conn.execute(
                "SELECT DISTINCT resolution FROM media_item WHERE resolution IS NOT NULL"
            )),
            key=media_info.rank, reverse=True,
        ),
    }


def random_selection(
    conn, count: int, kind: str = "", availability: str = "", year_min: int | None = None,
    year_max: int | None = None, genres: list[str] | None = None,
    resolutions: list[str] | None = None, min_rating: float | None = None,
    max_runtime: int | None = None, languages: list[str] | None = None,
) -> dict:
    """A random list of titles. Several genres or resolutions mean "any of these".

    Using a range or minimum leaves out titles with no value for it: SQL comparisons
    against NULL are never true, and an unrated title can't be called rated 8+.
    """
    if year_min is not None and year_max is not None and year_min > year_max:
        raise Conflict("The start of the year range is after its end.")
    where, params = [], []

    def add(condition: str, *values) -> None:
        where.append(condition)
        params.extend(values)

    def marks(values: list[str]) -> str:
        return ", ".join("?" for _ in values)

    if kind in ("movie", "series"):
        add("kind = ?", kind)
    if availability == "jellyfin":
        add("on_jellyfin = 1")
    elif availability == "not_jellyfin":
        add("on_jellyfin = 0")
    if year_min is not None:
        add("year >= ?", year_min)
    if year_max is not None:
        add("year <= ?", year_max)
    if genres:
        add(f"id IN (SELECT item_id FROM item_genre WHERE name IN ({marks(genres)}))", *genres)
    if resolutions:
        add(f"resolution IN ({marks(resolutions)})", *resolutions)
    if languages:
        add(f"original_language IN ({marks(languages)})", *languages)
    if min_rating is not None:
        add("community_rating >= ?", min_rating)
    if max_runtime is not None:
        add("runtime_minutes <= ?", max_runtime)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    matches = conn.execute(f"SELECT COUNT(*) FROM media_item {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {CARD_COLUMNS} FROM media_item {clause} ORDER BY RANDOM() LIMIT ?",
        (*params, count),
    ).fetchall()
    return {"items": [_card(r) for r in rows], "matches": matches}


def get_row(conn, item_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM media_item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise NotFound(f"No item {item_id}")
    return row


def _location_view(row, mappings, mounts) -> dict:
    resolved = paths.resolve_location(dict(row), mappings, mounts)
    return {
        "id": row["id"],
        "origin": row["origin"],
        "stored_path": row["path"],
        "local_path": resolved.path,
        "available": resolved.available,
        "reason": resolved.reason,
        "size_bytes": row["size_bytes"],
        "width": row["width"],
        "height": row["height"],
        "resolution": row["resolution"],
    }


def item_detail(conn, item_id: int, mappings: list[dict[str, str]]) -> dict:
    row = get_row(conn, item_id)
    mounts = volumes.list_mounts()
    detail = {**_card(row), **{k: row[k] for k in (
        "country", "summary", "trailer_url", "imdb_id", "added_at", "updated_at",
        "release_date", "community_rating", "runtime_minutes", "original_language",
    )}}
    detail["genres"] = [
        r["name"] for r in conn.execute(
            "SELECT name FROM item_genre WHERE item_id = ? ORDER BY name", (item_id,)
        )
    ]
    detail["locked_fields"] = json.loads(row["locked_fields"])
    breakdown: dict[str, int] = {}
    for label_row in conn.execute(
        "SELECT resolution FROM location WHERE item_id = ? AND resolution IS NOT NULL", (item_id,)
    ):
        breakdown[label_row["resolution"]] = breakdown.get(label_row["resolution"], 0) + 1
    detail["resolution_breakdown"] = [
        {"resolution": label, "count": breakdown[label]}
        for label in sorted(breakdown, key=media_info.rank, reverse=True)
    ]
    detail["credits"] = [
        dict(r) for r in conn.execute(
            "SELECT name, character, image_url FROM credit WHERE item_id = ? ORDER BY ord",
            (item_id,),
        )
    ]
    locations = conn.execute("SELECT * FROM location WHERE item_id = ?", (item_id,)).fetchall()
    detail["locations"] = [
        _location_view(r, mappings, mounts) for r in locations if r["episode_id"] is None
    ]
    by_episode: dict[int, list[dict]] = {}
    for loc in locations:
        if loc["episode_id"] is not None:
            by_episode.setdefault(loc["episode_id"], []).append(
                _location_view(loc, mappings, mounts)
            )
    seasons: dict[int | None, list[dict]] = {}
    for ep in conn.execute(
        """SELECT id, season_number, episode_number, title, summary, air_date FROM episode
           WHERE item_id = ? ORDER BY season_number IS NULL, season_number,
           episode_number IS NULL, episode_number, title""",
        (item_id,),
    ):
        seasons.setdefault(ep["season_number"], []).append(
            {**dict(ep), "locations": by_episode.get(ep["id"], [])}
        )
    detail["seasons"] = [{"number": n, "episodes": e} for n, e in seasons.items()]
    return detail


def best_location(conn, item_id: int, mappings: list[dict[str, str]]) -> paths.Resolved:
    """What the Hotlink opens: the title's own folder or file, else its first episode."""
    get_row(conn, item_id)
    rows = conn.execute(
        """SELECT * FROM location WHERE item_id = ?
           ORDER BY episode_id IS NOT NULL, origin = 'jellyfin', id""",
        (item_id,),
    ).fetchall()
    mounts = volumes.list_mounts()
    first_failure = None
    for row in rows:
        resolved = paths.resolve_location(dict(row), mappings, mounts)
        if resolved.available:
            return resolved
        first_failure = first_failure or resolved
    return first_failure or paths.Resolved(None, False, "No file is recorded for this title.")


def location_path(conn, location_id: int, mappings: list[dict[str, str]]) -> paths.Resolved:
    row = conn.execute("SELECT * FROM location WHERE id = ?", (location_id,)).fetchone()
    if row is None:
        raise NotFound(f"No location {location_id}")
    return paths.resolve_location(dict(row), mappings)


def refresh_resolution(conn, item_id: int) -> None:
    """Keep the title-level label, used by cards, filters and sorting, in step with its files."""
    row = get_row(conn, item_id)
    labels = [
        r["resolution"] for r in conn.execute(
            "SELECT resolution FROM location WHERE item_id = ? AND resolution IS NOT NULL", (item_id,)
        )
    ]
    label = media_info.summarize(labels, row["kind"])
    if label != row["resolution"]:
        conn.execute("UPDATE media_item SET resolution = ? WHERE id = ?", (label, item_id))


def update_fields(conn, item_id: int, changes: dict) -> None:
    """Apply an edit and lock the changed fields against future syncs."""
    row = get_row(conn, item_id)
    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS and v != row[k]}
    if "title" in changes and not (changes["title"] or "").strip():
        raise Conflict("Title can't be empty.")
    if not changes:
        return
    locked = sorted(set(json.loads(row["locked_fields"])) | set(changes))
    assignments = {**changes, "locked_fields": json.dumps(locked), "updated_at": now()}
    if "title" in changes:
        assignments["norm_title"] = norm_title(changes["title"])
        assignments["sort_title"] = sort_title(changes["title"])
    columns = ", ".join(f"{name} = ?" for name in assignments)
    conn.execute(f"UPDATE media_item SET {columns} WHERE id = ?", (*assignments.values(), item_id))


def unlock_field(conn, item_id: int, field: str) -> None:
    row = get_row(conn, item_id)
    locked = [f for f in json.loads(row["locked_fields"]) if f != field]
    conn.execute(
        "UPDATE media_item SET locked_fields = ?, updated_at = ? WHERE id = ?",
        (json.dumps(locked), now(), item_id),
    )


def is_excluded(conn, kind: str, tmdb_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM excluded_item WHERE kind = ? AND tmdb_id = ?", (kind, tmdb_id)
    ).fetchone() is not None


def ensure_tmdb_item(conn, client: TmdbClient, kind: str, tmdb_id: int, source: str) -> int:
    """The record for this TMDB title, created from TMDB's details if new."""
    row = conn.execute(
        "SELECT id FROM media_item WHERE kind = ? AND tmdb_id = ?", (kind, tmdb_id)
    ).fetchone()
    if row:
        return row["id"]
    if is_excluded(conn, kind, tmdb_id):
        raise Conflict("You deleted this title earlier. Restore it from Excluded items first.")
    details = client.details(kind, tmdb_id)
    stamp = now()
    item_id = conn.execute(
        """INSERT INTO media_item (kind, title, norm_title, sort_title, year, country, summary,
               poster_url, trailer_url, release_date, community_rating, runtime_minutes,
               original_language,
               source, on_jellyfin, tmdb_id, imdb_id, added_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
        (
            kind, details["title"], norm_title(details["title"]), sort_title(details["title"]),
            details["year"], details["country"], details["summary"], details["poster_url"],
            details["trailer_url"], details["release_date"], details["community_rating"],
            details["runtime_minutes"], details["original_language"], source, tmdb_id, details["imdb_id"], stamp, stamp,
        ),
    ).lastrowid
    conn.executemany(
        "INSERT OR IGNORE INTO item_genre (item_id, name) VALUES (?, ?)",
        [(item_id, name) for name in details["genres"]],
    )
    conn.executemany(
        "INSERT INTO credit (item_id, name, character, image_url, ord) VALUES (?, ?, ?, ?, ?)",
        [
            (item_id, c["name"], c["character"], c["image_url"], i)
            for i, c in enumerate(details["credits"])
        ],
    )
    return item_id


def create_manual(conn, kind: str, title: str, year: int | None, country: str | None,
                  source: str) -> int:
    stamp = now()
    return conn.execute(
        """INSERT INTO media_item (kind, title, norm_title, sort_title, year, country, source,
               on_jellyfin, added_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (kind, title, norm_title(title), sort_title(title), year, country, source, stamp, stamp),
    ).lastrowid


def annotate_results(conn, results: list[dict]) -> list[dict]:
    """Mark discovery results with their library status."""
    for result in results:
        row = conn.execute(
            "SELECT id, on_jellyfin FROM media_item WHERE kind = ? AND tmdb_id = ?",
            (result["kind"], result["tmdb_id"]),
        ).fetchone()
        if row is None and result["year"]:
            # Some Jellyfin items have no TMDB ID; title + year still identifies them.
            row = conn.execute(
                "SELECT id, on_jellyfin FROM media_item WHERE kind = ? AND norm_title = ? AND year = ?",
                (result["kind"], norm_title(result["title"]), result["year"]),
            ).fetchone()
        result["item_id"] = row["id"] if row else None
        if row:
            result["status"] = "jellyfin" if row["on_jellyfin"] else "saved"
        elif is_excluded(conn, result["kind"], result["tmdb_id"]):
            result["status"] = "excluded"
        else:
            result["status"] = None
    return results


# ---- Verify before Delete ----------------------------------------------------------

def _delete_token(row: sqlite3.Row, counts: dict) -> str:
    # Any change to the record after the preview invalidates the confirmation.
    seed = f"{row['id']}|{row['updated_at']}|{json.dumps(counts, sort_keys=True)}"
    return hashlib.sha256(seed.encode()).hexdigest()


def _delete_counts(conn, item_id: int) -> dict:
    count = lambda table: conn.execute(  # noqa: E731
        f"SELECT COUNT(*) FROM {table} WHERE item_id = ?", (item_id,)
    ).fetchone()[0]
    return {
        "episodes": count("episode"),
        "locations": count("location"),
        "credits": count("credit"),
    }


def delete_preview(conn, item_id: int, mappings: list[dict[str, str]]) -> dict:
    """Step 1: show exactly what will go. Changes nothing."""
    row = get_row(conn, item_id)
    counts = _delete_counts(conn, item_id)
    locations = conn.execute(
        "SELECT * FROM location WHERE item_id = ? ORDER BY episode_id IS NOT NULL, id LIMIT 10",
        (item_id,),
    ).fetchall()
    mounts = volumes.list_mounts()
    return {
        "id": row["id"],
        "title": row["title"],
        "year": row["year"],
        "kind": row["kind"],
        "on_jellyfin": bool(row["on_jellyfin"]),
        "will_exclude": bool(row["jellyfin_id"]),
        "locked_fields": json.loads(row["locked_fields"]),
        "counts": counts,
        "sample_locations": [_location_view(r, mappings, mounts) for r in locations],
        "token": _delete_token(row, counts),
    }


def delete_confirm(conn, item_id: int, token: str, confirm_title: str) -> None:
    """Step 2: remove the database record. Never touches a media file, by design."""
    row = get_row(conn, item_id)
    if token != _delete_token(row, _delete_counts(conn, item_id)):
        raise Conflict("This record changed after you reviewed it. Please review it again.")
    if confirm_title.strip() != row["title"].strip():
        raise Conflict("The typed title doesn't match.")

    stamp = now()
    if row["jellyfin_id"]:
        conn.execute(
            """INSERT INTO excluded_item (kind, title, year, jellyfin_id, tmdb_id, excluded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row["kind"], row["title"], row["year"], row["jellyfin_id"], row["tmdb_id"], stamp),
        )
    # Keep the next drive scan from offering this title's files as new.
    for loc in conn.execute(
        "SELECT * FROM location WHERE item_id = ? AND origin = 'scan'", (item_id,)
    ).fetchall():
        parsed = parse_path(loc["rel_path"] or loc["path"])
        conn.execute(
            """INSERT INTO scan_candidate (volume_id, rel_path, abs_path, size_bytes, status, kind,
                   parsed_title, parsed_year, season, episode, found_at)
               VALUES (?, ?, ?, ?, 'ignored', ?, ?, ?, ?, ?, ?)
               ON CONFLICT (volume_id, rel_path) DO UPDATE SET status = 'ignored'""",
            (
                loc["volume_id"], loc["rel_path"], loc["path"], loc["size_bytes"], parsed.kind,
                parsed.title, parsed.year, parsed.season, parsed.episode, stamp,
            ),
        )
    snapshot = {k: row[k] for k in row.keys()}
    conn.execute(
        "INSERT INTO delete_log (item_id, title, snapshot, deleted_at) VALUES (?, ?, ?, ?)",
        (row["id"], row["title"], json.dumps(snapshot), stamp),
    )
    conn.execute("DELETE FROM media_item WHERE id = ?", (item_id,))


def list_excluded(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM excluded_item ORDER BY excluded_at DESC")]


def restore_excluded(conn, excluded_id: int) -> None:
    done = conn.execute("DELETE FROM excluded_item WHERE id = ?", (excluded_id,))
    if done.rowcount == 0:
        raise NotFound(f"No excluded item {excluded_id}")
