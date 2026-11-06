import json

import httpx
import pytest

from mediamanager import csv_import, items, scanner, volumes
from mediamanager.config import Settings
from mediamanager.filename_parser import parse_path
from mediamanager.tmdb import TmdbClient


# ---- Filename parsing

@pytest.mark.parametrize("path, kind, title, year, season, episode", [
    ("Heat.1995.1080p.BluRay.x264-GRP.mkv", "movie", "Heat", 1995, None, None),
    ("Movies/Blade Runner (1982)/Blade Runner (1982).mkv", "movie", "Blade Runner", 1982, None, None),
    ("Movie Name (2019)/movie.mkv", "movie", "Movie Name", 2019, None, None),
    ("2001 A Space Odyssey (1968).mp4", "movie", "2001 A Space Odyssey", 1968, None, None),
    ("Alien.mkv", "movie", "Alien", None, None, None),
    ("Farscape.S01E02.Throne.720p.mkv", "series", "Farscape", None, 1, 2),
    ("Castle (2009) - S03E10.mkv", "series", "Castle", 2009, 3, 10),
    ("Show Name - 2x05 - Title.mkv", "series", "Show Name", None, 2, 5),
    ("TV/The Wire/Season 2/05 - Undertow.mkv", "series", "The Wire", None, 2, 5),
    ("TV/The Wire/Season 02/S02E05.mkv", "series", "The Wire", None, 2, 5),
    ("[Group] My.Hero.Academia.S01E03 [1080p].mkv", "series", "My Hero Academia", None, 1, 3),
])
def test_parse_path(path, kind, title, year, season, episode):
    parsed = parse_path(path)
    assert (parsed.kind, parsed.title, parsed.year, parsed.season, parsed.episode) == (
        kind, title, year, season, episode,
    )


# ---- Scanner

def tmdb_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/3/search/movie"):
        if request.url.params["query"] == "Heat":
            return httpx.Response(200, json={"results": [
                {"id": 949, "title": "Heat", "release_date": "1995-12-15", "overview": "x"},
                {"id": 2, "title": "Heat Wave", "release_date": "1995-01-01"},
            ]})
        return httpx.Response(200, json={"results": []})
    if path.startswith("/3/search/tv"):
        return httpx.Response(200, json={"results": [
            {"id": 7, "name": "Farscape", "first_air_date": "1999-03-19"},
        ]})
    if path == "/3/movie/949":
        return httpx.Response(200, json={
            "id": 949, "title": "Heat", "release_date": "1995-12-15", "overview": "Cops.",
            "poster_path": "/p.jpg", "production_countries": [{"name": "United States"}],
            "credits": {"cast": [{"name": "Al Pacino", "character": "Hanna"}]},
            "videos": {"results": [{"site": "YouTube", "type": "Trailer", "key": "abc", "official": True}]},
            "external_ids": {"imdb_id": "tt1"},
        })
    if path == "/3/tv/7":
        return httpx.Response(200, json={"id": 7, "name": "Farscape", "first_air_date": "1999-03-19"})
    if path == "/3/tv/7/season/1":
        return httpx.Response(200, json={"episodes": [{"episode_number": 2, "name": "I, ET"}]})
    return httpx.Response(404)


@pytest.fixture
def tmdb():
    return TmdbClient("key", transport=httpx.MockTransport(tmdb_handler))


@pytest.fixture
def drive(tmp_path, monkeypatch):
    """A fake external drive, and a volume list that can change between calls."""
    root = tmp_path / "drive"
    (root / "Movies").mkdir(parents=True)
    (root / "Movies" / "Heat.1995.1080p.mkv").write_bytes(b"x" * 10)
    (root / "Movies" / "Heat.1995.sample.mkv").touch()
    (root / "Movies" / "notes.txt").touch()
    (root / ".Trash-1000").mkdir()
    (root / ".Trash-1000" / "Deleted.2000.mkv").touch()
    (root / "TV" / "Farscape" / "Season 1").mkdir(parents=True)
    (root / "TV" / "Farscape" / "Season 1" / "Farscape.S01E02.mkv").touch()
    mounts = [volumes.Mount("uuid:drive", str(root), "Drive")]
    monkeypatch.setattr(volumes, "list_mounts", lambda platform=None: mounts)
    return root


def scan(conn, drive, tmdb=None):
    root_id = scanner.add_root(conn, str(drive))
    conn.commit()
    return root_id, scanner.run_scan(conn, root_id, Settings(), tmdb)


def pending(conn):
    return {c["rel_path"]: c for c in scanner.list_candidates(conn)}


def test_scan_queues_only_real_new_media_and_changes_no_records(conn, drive, tmdb):
    _, stats = scan(conn, drive, tmdb)
    assert stats["new"] == 2
    assert set(pending(conn)) == {"Movies/Heat.1995.1080p.mkv", "TV/Farscape/Season 1/Farscape.S01E02.mkv"}
    assert conn.execute("SELECT COUNT(*) FROM media_item").fetchone()[0] == 0


def test_candidate_gets_scored_suggestions(conn, drive, tmdb):
    scan(conn, drive, tmdb)
    cand = pending(conn)["Movies/Heat.1995.1080p.mkv"]
    assert cand["matches"][0]["tmdb_id"] == 949
    assert cand["confidence"] >= scanner.HIGH_CONFIDENCE


def test_scan_without_tmdb_still_queues_files(conn, drive):
    _, stats = scan(conn, drive)
    assert stats["new"] == 2 and pending(conn)["Movies/Heat.1995.1080p.mkv"]["matches"] == []


def test_accept_maps_file_to_db_and_rescan_treats_it_as_known(conn, drive, tmdb):
    root_id, _ = scan(conn, drive, tmdb)
    cand = pending(conn)["Movies/Heat.1995.1080p.mkv"]
    item_id = scanner.accept_candidate(conn, cand["id"], tmdb, tmdb_id=949, kind="movie")
    conn.commit()
    row = items.get_row(conn, item_id)
    assert (row["title"], row["country"], row["on_jellyfin"]) == ("Heat", "United States", 0)
    assert row["trailer_url"] == "https://www.youtube.com/watch?v=abc"
    stats = scanner.run_scan(conn, root_id, Settings(), tmdb)
    assert stats["new"] == 0 and stats["known"] == 1


def test_accept_series_creates_episode_with_location(conn, drive, tmdb):
    scan(conn, drive, tmdb)
    cand = pending(conn)["TV/Farscape/Season 1/Farscape.S01E02.mkv"]
    item_id = scanner.accept_candidate(conn, cand["id"], tmdb, tmdb_id=7, kind="series")
    episode = conn.execute("SELECT * FROM episode WHERE item_id = ?", (item_id,)).fetchone()
    assert (episode["season_number"], episode["episode_number"], episode["title"]) == (1, 2, "I, ET")
    assert conn.execute("SELECT episode_id FROM location").fetchone()[0] == episode["id"]


def test_bulk_accept_only_takes_high_confidence(conn, drive, tmdb, tmp_path):
    (drive / "Movies" / "Unknown.Film.2001.mkv").touch()
    scan(conn, drive, tmdb)
    result = scanner.accept_high_confidence(conn, tmdb)
    assert result["accepted"] == 2
    assert list(pending(conn)) == ["Movies/Unknown.Film.2001.mkv"]


def test_ignored_file_stays_ignored_on_rescan(conn, drive):
    root_id, _ = scan(conn, drive)
    cand = pending(conn)["Movies/Heat.1995.1080p.mkv"]
    scanner.set_ignored(conn, cand["id"], True)
    conn.commit()
    stats = scanner.run_scan(conn, root_id, Settings(), None)
    assert stats["ignored"] == 1 and stats["new"] == 0


def test_vanished_pending_file_leaves_queue(conn, drive):
    root_id, _ = scan(conn, drive)
    (drive / "Movies" / "Heat.1995.1080p.mkv").unlink()
    stats = scanner.run_scan(conn, root_id, Settings(), None)
    assert stats["stale_removed"] == 1 and len(pending(conn)) == 1


def test_disconnected_drive_is_a_clear_error_and_keeps_queue(conn, drive, monkeypatch):
    root_id, _ = scan(conn, drive)
    monkeypatch.setattr(volumes, "list_mounts", lambda platform=None: [])
    with pytest.raises(items.Conflict, match="connected"):
        scanner.run_scan(conn, root_id, Settings(), None)
    assert len(pending(conn)) == 2


def test_file_served_by_jellyfin_mapping_is_not_new(conn, drive):
    item_id = items.create_manual(conn, "movie", "Heat", 1995, None, "jellyfin")
    conn.execute(
        "INSERT INTO location (item_id, origin, path) VALUES (?, 'jellyfin', ?)",
        (item_id, "/media/Movies/Heat.1995.1080p.mkv"),
    )
    conn.commit()
    root_id = scanner.add_root(conn, str(drive))
    settings = Settings(path_mappings=[{"from": "/media", "to": str(drive)}])
    stats = scanner.run_scan(conn, root_id, settings, None)
    assert stats["known"] == 1 and stats["new"] == 1


def test_deleting_scanned_item_keeps_its_file_out_of_the_queue_and_on_disk(conn, drive, tmdb):
    root_id, _ = scan(conn, drive, tmdb)
    cand = pending(conn)["Movies/Heat.1995.1080p.mkv"]
    item_id = scanner.accept_candidate(conn, cand["id"], tmdb, tmdb_id=949, kind="movie")
    preview = items.delete_preview(conn, item_id, [])
    items.delete_confirm(conn, item_id, preview["token"], "Heat")
    conn.commit()
    assert (drive / "Movies" / "Heat.1995.1080p.mkv").exists()
    stats = scanner.run_scan(conn, root_id, Settings(), tmdb)
    assert stats["new"] == 0 and stats["ignored"] == 1


# ---- CSV

def add(conn, title, year, country=None):
    item_id = items.create_manual(conn, "movie", title, year, country, "tmdb")
    conn.commit()
    return item_id


def rows_by_title(conn, text):
    return {r["title"]: r for r in csv_import.preview(conn, text.encode())["rows"]}


def test_preview_classifies_rows_and_writes_nothing(conn):
    add(conn, "Heat", 1995)
    add(conn, "Alien", 1979, "UK")
    add(conn, "Dune", 1984)
    add(conn, "Dune", 2021)
    add(conn, "Solaris", 1972)
    rows = rows_by_title(conn, (
        "Title,Year,Country\nHeat,1995,USA\nAlien,1979,UK\nNew Film,2020,Japan\n"
        "Dune,,USA\nSolaris,2002,USA\n,2000,X\nBad Year,20x,X\n"
    ))
    assert rows["Heat"]["status"] == "update" and rows["Heat"]["changes"] == {"country": "USA"}
    assert rows["Alien"]["status"] == "unchanged"
    assert rows["New Film"]["status"] == "unmatched"
    assert rows["Dune"]["status"] == "ambiguous" and len(rows["Dune"]["candidates"]) == 2
    assert rows["Solaris"]["status"] == "ambiguous"  # same title, other year
    assert rows["Bad Year"]["status"] == "invalid" and rows[""]["status"] == "invalid"
    assert items.get_row(conn, 1)["country"] is None


def test_title_match_ignores_case_and_punctuation(conn):
    add(conn, "Spider-Man: Homecoming", 2017)
    row = rows_by_title(conn, "title,year\nspider man homecoming,2017\n")["spider man homecoming"]
    assert row["status"] == "update" and row["item_id"] == 1


def test_blank_cells_never_clear_values(conn):
    add(conn, "Heat", 1995, "USA")
    assert rows_by_title(conn, "Title,Year,Country\nHeat,1995,\n")["Heat"]["status"] == "unchanged"


@pytest.mark.parametrize("data", [
    "﻿Title;Year;Country\nHeat;1995;Frankreich\n".encode("utf-8"),
    "Title\tYear\tCountry\nHeat\t1995\tFrankreich\n".encode("utf-8"),
])
def test_excel_style_files(conn, data):
    add(conn, "Heat", 1995)
    assert csv_import.preview(conn, data)["rows"][0]["changes"] == {"country": "Frankreich"}


def test_cp1252_file(conn):
    add(conn, "Amélie", 2001)
    rows = csv_import.preview(conn, "Title,Year,Country\nAmélie,2001,France\n".encode("cp1252"))["rows"]
    assert rows[0]["status"] == "update" and rows[0]["changes"] == {"country": "France"}


@pytest.mark.parametrize("data", [b"", b"Year,Country\n1995,USA\n"])
def test_unusable_csv_rejected(conn, data):
    with pytest.raises(csv_import.CsvError):
        csv_import.preview(conn, data)


def test_apply_updates_locks_and_creates_flagged_entries(conn):
    item_id = add(conn, "Heat", 1995)
    result = csv_import.apply(conn, [
        {"action": "update", "item_id": item_id, "title": "Heat", "year": 1995, "country": "USA"},
        {"action": "create", "title": "New Film", "year": 2020, "country": "Japan", "kind": "movie"},
    ])
    assert result == {"updated": 1, "created": 1}
    assert json.loads(items.get_row(conn, item_id)["locked_fields"]) == ["country"]
    created = conn.execute("SELECT * FROM media_item WHERE title = 'New Film'").fetchone()
    assert (created["source"], created["on_jellyfin"], created["country"]) == ("csv", 0, "Japan")


def test_apply_is_all_or_nothing(conn):
    with pytest.raises(items.NotFound):
        csv_import.apply(conn, [
            {"action": "create", "title": "A", "kind": "movie"},
            {"action": "update", "item_id": 999, "title": "B"},
        ])
    assert conn.execute("SELECT COUNT(*) FROM media_item").fetchone()[0] == 0
