"""Find new media files in scan folders and queue them for review. Read-only on the drive."""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

from . import items, media_info, paths, volumes
from .db import norm_title, now
from .filename_parser import parse_path
from .tmdb import TmdbClient, TmdbError

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".webm",
    ".flv", ".iso", ".divx",
}
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "lost+found", "@eadir", "extras", "featurettes",
    "behind the scenes", "deleted scenes", "trailers", "samples", "sample",
}
HIGH_CONFIDENCE = 0.9


def add_root(conn, path: str, label: str = "") -> int:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise items.Conflict(f"Folder not found: {path}")
    volume_id, rel_path = volumes.identify(path)
    row = conn.execute(
        "SELECT id FROM scan_root WHERE volume_id = ? AND rel_path = ?", (volume_id, rel_path)
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO scan_root (label, volume_id, rel_path, last_path) VALUES (?, ?, ?, ?)",
        (label or Path(path).name or path, volume_id, rel_path, path),
    ).lastrowid


def remove_root(conn, root_id: int) -> None:
    # Ignored candidates stay: they record decisions, which outlive the scan folder.
    conn.execute("DELETE FROM scan_candidate WHERE root_id = ? AND status = 'pending'", (root_id,))
    conn.execute("DELETE FROM scan_root WHERE id = ?", (root_id,))


def list_roots(conn) -> list[dict]:
    mounts = volumes.list_mounts()
    return [
        {**dict(row), "current_path": (p := volumes.resolve(row["volume_id"], row["rel_path"], mounts)),
         "connected": p is not None}
        for row in conn.execute("SELECT * FROM scan_root ORDER BY label")
    ]


def find_media_files(root: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    # Unreadable subfolders are skipped by os.walk's default error handling.
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.casefold() not in SKIP_DIRS]
        for name in files:
            if name.startswith(".") or Path(name).suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            if re.search(r"\bsample\b", Path(name).stem, re.I):
                continue
            full = os.path.join(folder, name)
            try:
                found.append((full, os.path.getsize(full)))
            except OSError:
                continue
    return found


def score_match(title: str, year: int | None, match: dict, kind: str) -> float:
    ratio = difflib.SequenceMatcher(None, norm_title(title), norm_title(match["title"])).ratio()
    if year and match.get("year") and year != match["year"]:
        # Off by one is common: release vs. production year.
        ratio *= 0.95 if abs(year - match["year"]) == 1 else 0.7
    elif not year and kind == "movie":
        # Remakes share titles, so a movie with no year can't be certain.
        ratio *= 0.85
    return round(ratio, 3)


def suggest(client: TmdbClient, kind: str, title: str, year: int | None) -> tuple[list[dict], float]:
    results = client.search_kind(kind, title, year)
    if not results and year:
        results = client.search_kind(kind, title)
    scored = [{**r, "confidence": score_match(title, year, r, kind)} for r in results[:5]]
    scored.sort(key=lambda r: -r["confidence"])
    return scored, scored[0]["confidence"] if scored else 0.0


def run_scan(conn, root_id: int, settings, client: TmdbClient | None, progress=lambda _: None) -> dict:
    root = conn.execute("SELECT * FROM scan_root WHERE id = ?", (root_id,)).fetchone()
    if root is None:
        raise items.NotFound(f"No scan folder {root_id}")
    root_path = volumes.resolve(root["volume_id"], root["rel_path"])
    if root_path is None:
        raise items.Conflict(f"'{root['label']}' isn't connected.")

    progress(f"Scanning {root_path}")
    files = find_media_files(root_path)
    known_volume = {
        (r["volume_id"], r["rel_path"])
        for r in conn.execute("SELECT volume_id, rel_path FROM location WHERE origin = 'scan'")
    }
    # A folder Jellyfin already serves (via a path mapping) isn't new either.
    known_paths = {
        os.path.normcase(paths.apply_mappings(r["path"], settings.path_mappings))
        for r in conn.execute("SELECT path FROM location WHERE origin = 'jellyfin'")
    }
    existing = {
        r["rel_path"]: r
        for r in conn.execute(
            "SELECT rel_path, status FROM scan_candidate WHERE volume_id = ?", (root["volume_id"],)
        )
    }

    stats = {"files": len(files), "new": 0, "known": 0, "ignored": 0, "pending": 0,
             "stale_removed": 0, "tmdb_error": None}
    cache: dict[tuple, tuple[list[dict], float]] = {}
    tmdb_ok = client is not None
    seen_rel: set[str] = set()
    stamp = now()

    for index, (full, size) in enumerate(files):
        rel = Path(os.path.relpath(full, root_path)).as_posix()
        rel = f"{root['rel_path']}/{rel}" if root["rel_path"] else rel
        seen_rel.add(rel)
        if (root["volume_id"], rel) in known_volume or os.path.normcase(full) in known_paths:
            stats["known"] += 1
            continue
        if rel in existing:
            stats["ignored" if existing[rel]["status"] == "ignored" else "pending"] += 1
            continue

        parsed = parse_path(rel)
        progress(f"Reading {index + 1}/{len(files)}: {Path(full).name}")
        width, height, resolution = media_info.probe_file(full)
        matches, confidence = [], 0.0
        key = (parsed.kind, parsed.title, parsed.year)
        if key in cache:
            matches, confidence = cache[key]
        elif tmdb_ok:
            progress(f"Matching {index + 1}/{len(files)}: {parsed.title}")
            try:
                matches, confidence = suggest(client, *key)
                cache[key] = (matches, confidence)
            except TmdbError as exc:
                # Keep scanning: files still queue, just without suggestions.
                stats["tmdb_error"] = str(exc)
                tmdb_ok = False
        with conn:
            conn.execute(
                """INSERT INTO scan_candidate (root_id, volume_id, rel_path, abs_path, size_bytes,
                       width, height, resolution, status, kind, parsed_title, parsed_year, season,
                       episode, matches, confidence, found_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    root_id, root["volume_id"], rel, full, size, width, height, resolution,
                    parsed.kind, parsed.title,
                    parsed.year, parsed.season, parsed.episode, json.dumps(matches), confidence,
                    stamp,
                ),
            )
        stats["new"] += 1

    # Pending files that have since disappeared from this folder.
    with conn:
        for row in conn.execute(
            "SELECT id, rel_path FROM scan_candidate WHERE root_id = ? AND status = 'pending'",
            (root_id,),
        ).fetchall():
            if row["rel_path"] not in seen_rel:
                conn.execute("DELETE FROM scan_candidate WHERE id = ?", (row["id"],))
                stats["stale_removed"] += 1
        conn.execute(
            "UPDATE scan_root SET last_path = ?, last_scanned_at = ? WHERE id = ?",
            (root_path, stamp, root_id),
        )
    return stats


def list_candidates(conn, status: str = "pending") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM scan_candidate WHERE status = ? ORDER BY confidence DESC, rel_path LIMIT 1000",
        (status,),
    ).fetchall()
    return [{**dict(r), "matches": json.loads(r["matches"])} for r in rows]


def _ensure_episode(conn, client, item_id, tmdb_id, season, number, season_cache) -> int:
    row = conn.execute(
        "SELECT id FROM episode WHERE item_id = ? AND season_number = ? AND episode_number = ?",
        (item_id, season, number),
    ).fetchone()
    if row:
        return row["id"]
    info: dict = {}
    if client and tmdb_id:
        key = (tmdb_id, season)
        if key not in season_cache:
            try:
                season_cache[key] = client.season_episodes(tmdb_id, season)
            except TmdbError:
                season_cache[key] = []  # episode details are optional
        info = next((e for e in season_cache[key] if e["episode_number"] == number), {})
    return conn.execute(
        """INSERT INTO episode (item_id, season_number, episode_number, title, summary, air_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (item_id, season, number, info.get("title"), info.get("summary"), info.get("air_date")),
    ).lastrowid


def accept_candidate(
    conn, candidate_id: int, client: TmdbClient | None, *, tmdb_id: int | None = None,
    kind: str | None = None, title: str | None = None, year: int | None = None,
    season_cache: dict | None = None,
) -> int:
    """Map a reviewed file into the database, from a TMDB match or typed-in details."""
    cand = conn.execute("SELECT * FROM scan_candidate WHERE id = ?", (candidate_id,)).fetchone()
    if cand is None:
        raise items.NotFound(f"No candidate {candidate_id}")
    kind = kind or cand["kind"]
    if tmdb_id:
        if client is None:
            raise items.Conflict("The TMDB API key isn't set.")
        item_id = items.ensure_tmdb_item(conn, client, kind, tmdb_id, "scan")
    else:
        final_title = (title or cand["parsed_title"]).strip()
        final_year = year if year is not None else cand["parsed_year"]
        row = conn.execute(
            "SELECT id FROM media_item WHERE kind = ? AND norm_title = ? AND year IS ?",
            (kind, norm_title(final_title), final_year),
        ).fetchone()
        item_id = row["id"] if row else items.create_manual(
            conn, kind, final_title, final_year, None, "scan"
        )
    episode_id = None
    if kind == "series" and cand["season"] is not None and cand["episode"] is not None:
        episode_id = _ensure_episode(
            conn, client, item_id, tmdb_id, cand["season"], cand["episode"],
            season_cache if season_cache is not None else {},
        )
    conn.execute(
        """INSERT INTO location (item_id, episode_id, origin, path, volume_id, rel_path, size_bytes,
               width, height, resolution)
           VALUES (?, ?, 'scan', ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id, episode_id, cand["abs_path"], cand["volume_id"], cand["rel_path"],
            cand["size_bytes"], cand["width"], cand["height"], cand["resolution"],
        ),
    )
    items.refresh_resolution(conn, item_id)
    conn.execute("DELETE FROM scan_candidate WHERE id = ?", (candidate_id,))
    return item_id


def accept_high_confidence(conn, client: TmdbClient) -> dict:
    """Bulk-accept each file whose top TMDB suggestion is high confidence."""
    rows = conn.execute(
        "SELECT id, kind, matches FROM scan_candidate WHERE status = 'pending' AND confidence >= ?",
        (HIGH_CONFIDENCE,),
    ).fetchall()
    accepted, errors, season_cache = 0, [], {}
    for row in rows:
        top = json.loads(row["matches"])[0]
        try:
            with conn:
                accept_candidate(
                    conn, row["id"], client, tmdb_id=top["tmdb_id"], kind=row["kind"],
                    season_cache=season_cache,
                )
            accepted += 1
        except (items.Conflict, TmdbError) as exc:
            errors.append(f"{top['title']}: {exc}")
    return {"accepted": accepted, "errors": errors}


def set_ignored(conn, candidate_id: int, ignored: bool) -> None:
    conn.execute(
        "UPDATE scan_candidate SET status = ? WHERE id = ?",
        ("ignored" if ignored else "pending", candidate_id),
    )
