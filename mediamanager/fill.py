"""Fill empty metadata from TMDB. Only blanks are written, so nothing is ever overwritten."""

from __future__ import annotations

import json
import re
from collections import Counter

from . import items
from .db import norm_title, now
from .filename_parser import parse_path
from .tmdb import TmdbClient, TmdbError

FILL_FIELDS = (
    "year", "country", "summary", "poster_url", "trailer_url", "imdb_id", "release_date",
    "community_rating", "runtime_minutes", "original_language",
)
MAX_CONSECUTIVE_FAILURES = 5
_TITLE_NOISE = re.compile(
    r"\((?:english |eng )?dub\)|\b(?:english |eng )dub\b|\bdub\b|\(low\)|\.(?:480|720|1080)$", re.I
)


def _has_rows(conn, table: str, item_id: int) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} WHERE item_id = ? LIMIT 1", (item_id,)).fetchone() is not None


def _gaps(conn, row) -> set[str]:
    # A locked blank is deliberate: you cleared that field, so it stays clear.
    locked = set(json.loads(row["locked_fields"]))
    gaps = {name for name in FILL_FIELDS if row[name] in (None, "") and name not in locked}
    if not _has_rows(conn, "item_genre", row["id"]):
        gaps.add("genres")
    if not _has_rows(conn, "credit", row["id"]):
        gaps.add("cast")
    return gaps


def _search_terms(row) -> list[tuple[str, int]]:
    """The title as stored, then with filename leftovers removed: "Underworld EXTENDED (2003)"."""
    terms = []
    if row["year"]:
        terms.append((row["title"], row["year"]))
    # Slashes would read as folders ("Face/Off"), and episode names aren't titles.
    parsed = parse_path(_TITLE_NOISE.sub(" ", row["title"]).replace("/", " ").strip(" -") + ".mkv")
    year = parsed.year or row["year"]
    if year and parsed.season is None and (parsed.title, year) not in terms:
        terms.append((parsed.title, year))
    return terms


def _find_tmdb_id(conn, client: TmdbClient, row) -> int | None:
    """Link a title with no TMDB ID, but only on an exact, unique title and year match.

    A wrong link would write another film's data, so anything less certain is left blank.
    """
    for title, year in _search_terms(row):
        results = client.search_kind(row["kind"], title, year)
        exact = [
            r for r in results if norm_title(r["title"]) == norm_title(title) and r["year"] == year
        ]
        if len(exact) == 1:
            tmdb_id = exact[0]["tmdb_id"]
            taken = conn.execute(
                "SELECT 1 FROM media_item WHERE kind = ? AND tmdb_id = ?", (row["kind"], tmdb_id)
            ).fetchone()
            return None if taken or items.is_excluded(conn, row["kind"], tmdb_id) else tmdb_id
    return None


def _apply(conn, row, details: dict, gaps: set[str], tmdb_id: int | None) -> list[str]:
    values = {name: details[name] for name in gaps & set(FILL_FIELDS) if details.get(name)}
    filled = list(values)
    if tmdb_id:
        values["tmdb_id"] = tmdb_id
    values["gaps_checked_at"] = now()
    if filled:
        values["updated_at"] = values["gaps_checked_at"]
    columns = ", ".join(f"{name} = ?" for name in values)
    conn.execute(f"UPDATE media_item SET {columns} WHERE id = ?", (*values.values(), row["id"]))

    if "genres" in gaps and details["genres"]:
        conn.executemany(
            "INSERT OR IGNORE INTO item_genre (item_id, name) VALUES (?, ?)",
            [(row["id"], name) for name in details["genres"]],
        )
        filled.append("genres")
    if "cast" in gaps and details["credits"]:
        conn.executemany(
            "INSERT INTO credit (item_id, name, character, image_url, ord) VALUES (?, ?, ?, ?, ?)",
            [
                (row["id"], c["name"], c["character"], c["image_url"], i)
                for i, c in enumerate(details["credits"])
            ],
        )
        filled.append("cast")
    return filled


def still_missing(conn) -> dict[str, int]:
    count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        **{
            name: count(f"SELECT COUNT(*) FROM media_item WHERE {name} IS NULL OR {name} = ''")
            for name in FILL_FIELDS if name != "imdb_id"
        },
        "genres": count("SELECT COUNT(*) FROM media_item WHERE id NOT IN (SELECT item_id FROM item_genre)"),
        "cast": count("SELECT COUNT(*) FROM media_item WHERE id NOT IN (SELECT item_id FROM credit)"),
    }


def fill_gaps(conn, client: TmdbClient, progress=lambda _: None, recheck: bool = False) -> dict:
    """Look up titles with blank fields.

    A title TMDB can't help with is remembered, so routine syncs don't ask again.
    `recheck` asks again for everything, for when TMDB may have gained the data since.
    """
    query = "SELECT * FROM media_item"
    if not recheck:
        query += " WHERE gaps_checked_at IS NULL"
    rows = conn.execute(query).fetchall()
    stats = {"looked_up": 0, "improved": 0, "linked": 0, "no_match": 0, "failed": 0}
    fields: Counter = Counter()
    failures_in_a_row = 0

    for index, row in enumerate(rows):
        gaps = _gaps(conn, row)
        if not gaps:
            continue
        progress(f"Checking {index + 1}/{len(rows)}: {row['title']}")
        stats["looked_up"] += 1
        try:
            new_id = None if row["tmdb_id"] else _find_tmdb_id(conn, client, row)
            tmdb_id = row["tmdb_id"] or new_id
            if not tmdb_id:
                stats["no_match"] += 1
                with conn:
                    conn.execute(
                        "UPDATE media_item SET gaps_checked_at = ? WHERE id = ?", (now(), row["id"])
                    )
                failures_in_a_row = 0
                continue
            details = client.details(row["kind"], tmdb_id)
        except TmdbError as exc:
            stats["failed"] += 1
            failures_in_a_row += 1
            if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                # Finished titles are already saved, so running it again resumes from here.
                raise TmdbError(f"Stopped after repeated TMDB errors: {exc}") from None
            continue
        failures_in_a_row = 0
        with conn:  # each title commits by itself, so stopping part-way loses nothing
            filled = _apply(conn, row, details, gaps, new_id)
        stats["linked"] += 1 if new_id else 0
        stats["improved"] += 1 if filled else 0
        fields.update(filled)

    return {**stats, "fields": dict(fields), "still_missing": still_missing(conn)}
