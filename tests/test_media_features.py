import json
import shutil
import subprocess

import httpx
import pytest

from mediamanager import fill, items, media_info, scanner, volumes
from mediamanager.config import Settings
from mediamanager.jellyfin import JellyfinClient
from mediamanager.sync import run_sync
from mediamanager.tmdb import TmdbClient


# ---- Resolution labels

@pytest.mark.parametrize("width, height, label", [
    (1920, 1080, "1080p"), (1920, 800, "1080p"), (1440, 1080, "1080p"), (1920, 1040, "1080p"),
    (1280, 720, "720p"), (1280, 534, "720p"), (960, 720, "720p"),
    (1024, 576, "576p"), (720, 576, "576p"),
    (720, 480, "480p"), (640, 480, "480p"), (720, 300, "480p"), (720, 400, "480p"),
    (640, 360, "360p"), (640, 272, "360p"), (512, 384, "360p"),
    (320, 240, "240p"),
    (3840, 2160, "4K"), (3840, 1600, "4K"), (2560, 1440, "1440p"), (7680, 4320, "8K"),
    (None, None, None),
])
def test_resolution_label(width, height, label):
    assert media_info.resolution_label(width, height) == label


@pytest.mark.parametrize("path, label", [
    ("Heat.1995.1080p.BluRay.mkv", "1080p"),
    ("/m/Movie 720p/Film [480p].mkv", "480p"),
    ("Film.2160p.mkv", "4K"), ("Film.4K.mkv", "4K"), ("Film_UHD.mkv", "4K"),
    ("D:\\x\\Show.S01E01.720i.mkv", "720p"),
    ("1080 Nights.mkv", None), ("Film.1995.mkv", None), ("Mark4K2.mkv", None),
])
def test_label_from_filename(path, label):
    assert media_info.label_from_filename(path) == label


def test_movie_uses_best_version_and_series_most_common():
    labels = ["720p", "720p", "1080p"]
    assert media_info.summarize(labels, "movie") == "1080p"
    assert media_info.summarize(labels, "series") == "720p"
    assert media_info.summarize(["720p", "1080p"], "series") == "1080p"  # ties go higher
    assert media_info.summarize([], "movie") is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_probe_measures_a_real_video(tmp_path):
    video = tmp_path / "wrongly.named.720p.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1920x800:d=0.1", str(video)],
        check=True,
    )
    assert media_info.probe_file(str(video)) == (1920, 800, "1080p")


def test_probe_skips_cover_art(monkeypatch):
    streams = {"streams": [
        {"width": 600, "height": 900, "disposition": {"attached_pic": 1}},
        {"width": 1280, "height": 720, "disposition": {"attached_pic": 0}},
    ]}
    monkeypatch.setattr(media_info.shutil, "which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        media_info.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=json.dumps(streams)),
    )
    assert media_info.probe_file("/x.mkv") == (1280, 720, "720p")


def test_without_ffprobe_or_on_unreadable_file_filename_is_used(tmp_path, monkeypatch):
    broken = tmp_path / "Film.1080p.mkv"
    broken.touch()
    assert media_info.probe_file(str(broken)) == (None, None, "1080p")
    monkeypatch.setattr(media_info.shutil, "which", lambda _: None)
    assert media_info.probe_file(str(broken)) == (None, None, "1080p")


# ---- Jellyfin

def source(path, width, height):
    return {"Path": path, "Size": 1, "MediaStreams": [
        {"Type": "Audio"}, {"Type": "Video", "Width": width, "Height": height},
    ]}


def server(titles, episodes=()):
    def handler(request):
        rows = list(episodes) if request.url.params["IncludeItemTypes"] == "Episode" else list(titles)
        return httpx.Response(200, json={"Items": rows, "TotalRecordCount": len(rows)})
    return JellyfinClient("http://jf", "k", transport=httpx.MockTransport(handler))


MOVIE = {"Id": "a" * 32, "Type": "Movie", "Name": "Heat", "ProductionYear": 1995,
         "MediaSources": [source("/m/heat-dvd.mkv", 720, 480), source("/m/heat-bd.mkv", 1920, 800)]}
SERIES = {"Id": "b" * 32, "Type": "Series", "Name": "Show", "Path": "/tv/Show"}


def episode(index, width, height):
    return {"Id": f"{index:032x}", "Type": "Episode", "SeriesId": "b" * 32, "ParentIndexNumber": 1,
            "IndexNumber": index, "Name": f"E{index}",
            "MediaSources": [source(f"/tv/Show/{index}.mkv", width, height)]}


def test_sync_stores_dimensions_per_file_and_label_per_title(conn):
    run_sync(conn, server([MOVIE]))
    rows = conn.execute("SELECT width, height, resolution FROM location ORDER BY width").fetchall()
    assert [tuple(r) for r in rows] == [(720, 480, "480p"), (1920, 800, "1080p")]
    assert conn.execute("SELECT resolution FROM media_item").fetchone()[0] == "1080p"


def test_series_gets_most_common_episode_quality(conn, client):
    run_sync(conn, server([SERIES], [episode(1, 1280, 720), episode(2, 1280, 720), episode(3, 1920, 1080)]))
    row = conn.execute("SELECT id, resolution FROM media_item").fetchone()
    assert row["resolution"] == "720p"
    detail = client.get(f"/api/items/{row['id']}").json()
    assert detail["resolution_breakdown"] == [
        {"resolution": "1080p", "count": 1}, {"resolution": "720p", "count": 2},
    ]
    file = detail["seasons"][0]["episodes"][2]["locations"][0]
    assert (file["width"], file["height"], file["resolution"]) == (1920, 1080, "1080p")


def test_filename_label_used_when_jellyfin_has_no_stream(conn):
    raw = {**MOVIE, "MediaSources": [{"Path": "/m/Heat.720p.mkv"}]}
    run_sync(conn, server([raw]))
    assert conn.execute("SELECT resolution FROM media_item").fetchone()[0] == "720p"


def test_resolution_follows_replaced_file_and_clears_when_gone(conn):
    run_sync(conn, server([MOVIE]))
    run_sync(conn, server([{**MOVIE, "MediaSources": [source("/m/heat.mkv", 3840, 2160)]}]))
    assert conn.execute("SELECT resolution FROM media_item").fetchone()[0] == "4K"
    run_sync(conn, server([]))
    assert conn.execute("SELECT resolution FROM media_item").fetchone()[0] is None


def test_browse_filter_and_sort_by_resolution(client, conn):
    for title, label in (("A", "720p"), ("B", "4K"), ("C", "1080p"), ("D", None)):
        item_id = items.create_manual(conn, "movie", title, 2000, None, "tmdb")
        conn.execute("UPDATE media_item SET resolution = ? WHERE id = ?", (label, item_id))
    conn.commit()
    titles = lambda **p: [i["title"] for i in client.get("/api/items", params=p).json()["items"]]  # noqa: E731
    assert titles(resolution="1080p") == ["C"]
    assert titles(sort="resolution") == ["B", "C", "A", "D"]  # 4K above 1080p, not alphabetical
    assert titles(sort="resolution", order="asc") == ["A", "C", "B", "D"]  # unknown stays last
    assert client.get("/api/items/filter-options").json()["resolutions"] == ["4K", "1080p", "720p"]
    assert client.get("/api/items", params={"resolution": "4K"}).json()["items"][0]["resolution"] == "4K"


def test_scan_measures_file_and_carries_it_to_the_record(conn, tmp_path, monkeypatch):
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "Heat.1995.mkv").touch()
    monkeypatch.setattr(volumes, "list_mounts", lambda platform=None: [volumes.Mount("uuid:d", str(drive), "D")])
    monkeypatch.setattr(media_info, "probe_file", lambda _: (1920, 800, "1080p"))
    root = scanner.add_root(conn, str(drive))
    scanner.run_scan(conn, root, Settings(), None)
    cand = scanner.list_candidates(conn)[0]
    assert (cand["width"], cand["resolution"]) == (1920, "1080p")
    item_id = scanner.accept_candidate(conn, cand["id"], None)
    assert items.get_row(conn, item_id)["resolution"] == "1080p"
    location = conn.execute("SELECT width, height FROM location").fetchone()
    assert tuple(location) == (1920, 800)


# ---- Filling missing data

DETAILS = {
    "id": 949, "title": "Heat", "release_date": "1995-12-15", "overview": "From TMDB.",
    "poster_path": "/p.jpg", "production_countries": [{"name": "United States"}],
    "vote_average": 8.3, "runtime": 170, "genres": [{"name": "Crime"}],
    "credits": {"cast": [{"name": "Al Pacino", "character": "Hanna"}]},
    "videos": {"results": [{"site": "YouTube", "type": "Trailer", "key": "abc"}]},
    "external_ids": {"imdb_id": "tt1"},
}


def tmdb(calls=None, search=(), status=200):
    def handler(request):
        if calls is not None:
            calls.append(request.url.path)
        if status != 200:
            return httpx.Response(status)
        if "/search/" in request.url.path:
            return httpx.Response(200, json={"results": list(search)})
        return httpx.Response(200, json=DETAILS)
    return TmdbClient("key", transport=httpx.MockTransport(handler))


def linked_item(conn, **values):
    item_id = items.create_manual(conn, "movie", "Heat", 1995, None, "jellyfin")
    conn.execute("UPDATE media_item SET tmdb_id = 949 WHERE id = ?", (item_id,))
    for name, value in values.items():
        conn.execute(f"UPDATE media_item SET {name} = ? WHERE id = ?", (value, item_id))
    conn.commit()
    return item_id


def test_fill_writes_blanks(conn):
    item_id = linked_item(conn)
    result = fill.fill_gaps(conn, tmdb())
    row = items.get_row(conn, item_id)
    assert result["improved"] == 1
    assert row["trailer_url"] == "https://www.youtube.com/watch?v=abc"
    assert (row["country"], row["runtime_minutes"], row["community_rating"]) == ("United States", 170, 8.3)
    assert (row["release_date"], row["imdb_id"]) == ("1995-12-15", "tt1")
    assert conn.execute("SELECT name FROM item_genre").fetchone()[0] == "Crime"
    assert conn.execute("SELECT name FROM credit").fetchone()[0] == "Al Pacino"


def test_fill_never_overwrites_existing_values(conn):
    item_id = linked_item(conn, summary="Jellyfin summary", country="France")
    conn.execute("INSERT INTO credit (item_id, name, ord) VALUES (?, 'Existing', 0)", (item_id,))
    conn.execute("INSERT INTO item_genre (item_id, name) VALUES (?, 'Existing')", (item_id,))
    conn.commit()
    fill.fill_gaps(conn, tmdb())
    row = items.get_row(conn, item_id)
    assert (row["summary"], row["country"]) == ("Jellyfin summary", "France")
    assert [r[0] for r in conn.execute("SELECT name FROM credit")] == ["Existing"]
    assert [r[0] for r in conn.execute("SELECT name FROM item_genre")] == ["Existing"]


def test_fill_respects_a_field_you_cleared(conn):
    item_id = linked_item(conn, trailer_url="http://x")
    items.update_fields(conn, item_id, {"trailer_url": None})
    conn.commit()
    fill.fill_gaps(conn, tmdb())
    assert items.get_row(conn, item_id)["trailer_url"] is None


def test_complete_title_makes_no_tmdb_call(conn):
    item_id = linked_item(
        conn, country="x", summary="x", poster_url="x", trailer_url="x", imdb_id="x",
        release_date="x", community_rating=1, runtime_minutes=1, original_language="en",
    )
    conn.execute("INSERT INTO credit (item_id, name, ord) VALUES (?, 'c', 0)", (item_id,))
    conn.execute("INSERT INTO item_genre (item_id, name) VALUES (?, 'g')", (item_id,))
    conn.commit()
    calls = []
    fill.fill_gaps(conn, tmdb(calls))
    assert calls == []


def test_routine_run_skips_titles_already_checked_but_recheck_asks_again(conn):
    linked_item(conn)
    no_data = {**DETAILS, "videos": {"results": []}}
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=no_data)

    client = TmdbClient("key", transport=httpx.MockTransport(handler))
    fill.fill_gaps(conn, client)
    assert len(calls) == 1
    fill.fill_gaps(conn, client)
    assert len(calls) == 1
    fill.fill_gaps(conn, client, recheck=True)
    assert len(calls) == 2


HEAT_RESULT = {"id": 949, "title": "Heat", "release_date": "1995-12-15"}


def test_title_without_id_links_on_exact_title_and_year(conn):
    item_id = items.create_manual(conn, "movie", "Heat", 1995, None, "csv")
    conn.commit()
    result = fill.fill_gaps(conn, tmdb(search=[HEAT_RESULT]))
    assert result["linked"] == 1
    assert items.get_row(conn, item_id)["tmdb_id"] == 949


@pytest.mark.parametrize("year, search", [
    (1995, [{"id": 1, "title": "Heat Wave", "release_date": "1995-01-01"}]),  # different title
    (1996, [HEAT_RESULT]),  # different year
    (None, [HEAT_RESULT]),  # no year to confirm with
    (1995, [HEAT_RESULT, {"id": 2, "title": "Heat", "release_date": "1995-06-01"}]),  # not unique
])
def test_uncertain_titles_stay_blank(conn, year, search):
    item_id = items.create_manual(conn, "movie", "Heat", year, None, "csv")
    conn.commit()
    result = fill.fill_gaps(conn, tmdb(search=search))
    assert result["no_match"] == 1
    row = items.get_row(conn, item_id)
    assert row["tmdb_id"] is None and row["summary"] is None


def test_no_two_titles_link_to_the_same_tmdb_entry(conn):
    linked_item(conn, country="x")
    other = items.create_manual(conn, "movie", "heat", 1995, None, "csv")
    conn.commit()
    fill.fill_gaps(conn, tmdb(search=[HEAT_RESULT]))
    assert items.get_row(conn, other)["tmdb_id"] is None


def test_single_failure_is_skipped_and_retried_later(conn):
    first, second = linked_item(conn), linked_item(conn)
    conn.execute("UPDATE media_item SET tmdb_id = 404 WHERE id = ?", (first,))
    conn.commit()

    def handler(request):
        return httpx.Response(404 if "/404" in request.url.path else 200, json=DETAILS)

    result = fill.fill_gaps(conn, TmdbClient("k", transport=httpx.MockTransport(handler)))
    assert (result["failed"], result["improved"]) == (1, 1)
    assert items.get_row(conn, second)["country"] == "United States"
    assert items.get_row(conn, first)["gaps_checked_at"] is None


def test_repeated_failures_stop_but_keep_earlier_work(conn):
    good = linked_item(conn)
    bad = [linked_item(conn) for _ in range(8)]
    for item_id in bad:
        conn.execute("UPDATE media_item SET tmdb_id = 404 WHERE id = ?", (item_id,))
    conn.commit()
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401 if "/404" in request.url.path else 200, json=DETAILS)

    with pytest.raises(Exception, match="repeated"):
        fill.fill_gaps(conn, TmdbClient("k", transport=httpx.MockTransport(handler)))
    assert len(calls) == 1 + fill.MAX_CONSECUTIVE_FAILURES
    assert items.get_row(conn, good)["country"] == "United States"


def test_filled_data_survives_a_jellyfin_sync(conn):
    raw = {"Id": "a" * 32, "Type": "Movie", "Name": "Heat", "ProductionYear": 1995,
           "ProviderIds": {"Tmdb": "949"}, "Path": "/m/heat.mkv"}
    run_sync(conn, server([raw]))
    fill.fill_gaps(conn, tmdb())
    run_sync(conn, server([raw]))
    row = conn.execute("SELECT * FROM media_item").fetchone()
    assert row["trailer_url"] and row["country"] == "United States"
    assert conn.execute("SELECT COUNT(*) FROM credit").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM item_genre").fetchone()[0] == 1


def test_missing_counts(client, conn):
    linked_item(conn)
    body = client.get("/api/library/missing").json()
    assert body["trailer_url"] == 1 and body["cast"] == 1 and body["genres"] == 1
    fill.fill_gaps(conn, tmdb())
    body = client.get("/api/library/missing").json()
    assert body["trailer_url"] == 0 and body["cast"] == 0


# ---- Better gap filling, found from real library data

def trailer_of(videos):
    from mediamanager.tmdb import _trailer
    return _trailer(videos)


def video(key, type="Trailer", lang="en", official=False, site="YouTube"):
    return {"key": key, "type": type, "iso_639_1": lang, "official": official, "site": site}


def test_trailer_preference_order():
    url = "https://www.youtube.com/watch?v="
    assert trailer_of([video("t", "Teaser"), video("j", lang="ja")]) == url + "j"  # trailer beats teaser
    assert trailer_of([video("j", lang="ja", official=True), video("e")]) == url + "e"  # English first
    assert trailer_of([video("u"), video("o", official=True)]) == url + "o"
    assert trailer_of([video("t", "Teaser")]) == url + "t"  # teaser beats nothing
    assert trailer_of([video("c", "Clip"), video("v", site="Vimeo")]) is None


def test_details_asks_for_videos_in_other_languages():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=DETAILS)

    TmdbClient("k", transport=httpx.MockTransport(handler)).details("movie", 949)
    assert "ja" in seen["include_video_language"]


@pytest.mark.parametrize("stored, year, query", [
    ("Heat EXTENDED (1995) [1080p]", None, "Heat"),
    ("Heat (Dub)", 1995, "Heat"),
    ("Heat.1080", 1995, "Heat"),
    ("Heat (Low)", 1995, "Heat"),
])
def test_filename_style_titles_link_after_cleaning(conn, stored, year, query):
    item_id = items.create_manual(conn, "movie", stored, year, None, "jellyfin")
    conn.commit()
    queries = []

    def handler(request):
        if "/search/" in request.url.path:
            queries.append(request.url.params["query"])
            found = request.url.params["query"] == query
            return httpx.Response(200, json={"results": [HEAT_RESULT] if found else []})
        return httpx.Response(200, json=DETAILS)

    fill.fill_gaps(conn, TmdbClient("k", transport=httpx.MockTransport(handler)))
    assert items.get_row(conn, item_id)["tmdb_id"] == 949
    assert queries[-1] == query
    assert items.get_row(conn, item_id)["title"] == stored  # the stored title isn't rewritten


def test_stored_title_is_tried_before_cleaning(conn):
    # The cleaner removes "web" as a release tag, but here it's the film's name.
    item_id = items.create_manual(conn, "movie", "Charlotte Web", 1995, None, "jellyfin")
    conn.commit()
    result = {"id": 5, "title": "Charlotte Web", "release_date": "1995-01-01"}
    fill.fill_gaps(conn, tmdb(search=[result]))
    assert items.get_row(conn, item_id)["tmdb_id"] == 5


def test_episode_filenames_and_slashes_never_link_by_a_fragment(conn):
    episode_like = items.create_manual(conn, "movie", "The Batman - 5x12 (2004)", 2004, None, "jellyfin")
    slashed = items.create_manual(conn, "movie", "Face/Off", 1997, None, "jellyfin")
    conn.commit()
    results = [
        {"id": 1, "title": "The Batman", "release_date": "2004-01-01"},
        {"id": 2, "title": "Off", "release_date": "1997-01-01"},
    ]
    fill.fill_gaps(conn, tmdb(search=results))
    assert items.get_row(conn, episode_like)["tmdb_id"] is None
    assert items.get_row(conn, slashed)["tmdb_id"] is None


# ---- What to Watch

def seed_watch(conn):
    rows = [
        ("Alpha", "movie", 1985, 8.5, 90, "1080p", 1, ["Action", "Comedy"]),
        ("Bravo", "movie", 1995, 6.0, 120, "720p", 1, ["Comedy"]),
        ("Charlie", "series", 2005, 9.0, 24, "1080p", 1, ["Drama"]),
        ("Delta", "movie", 2015, 7.0, 150, "4K", 0, ["Horror"]),
        ("Echo", "movie", None, None, None, None, 0, []),
    ]
    for title, kind, year, rating, runtime, resolution, on_jellyfin, genres in rows:
        item_id = items.create_manual(conn, kind, title, year, None, "tmdb")
        conn.execute(
            "UPDATE media_item SET community_rating = ?, runtime_minutes = ?, resolution = ?,"
            " on_jellyfin = ? WHERE id = ?",
            (rating, runtime, resolution, on_jellyfin, item_id),
        )
        conn.executemany(
            "INSERT INTO item_genre (item_id, name) VALUES (?, ?)", [(item_id, g) for g in genres]
        )
    conn.commit()


def watch(client, **body):
    response = client.post("/api/what-to-watch", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def titles_of(result):
    return sorted(i["title"] for i in result["items"])


@pytest.mark.parametrize("body, expected", [
    ({}, ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]),
    ({"kind": "series"}, ["Charlie"]),
    ({"kind": "movie"}, ["Alpha", "Bravo", "Delta", "Echo"]),
    ({"availability": "jellyfin"}, ["Alpha", "Bravo", "Charlie"]),
    ({"availability": "not_jellyfin"}, ["Delta", "Echo"]),
    ({"year_min": 1990, "year_max": 2010}, ["Bravo", "Charlie"]),
    ({"year_min": 2005}, ["Charlie", "Delta"]),
    ({"year_max": 1985}, ["Alpha"]),  # both ends of a range are included
    ({"genres": ["Comedy"]}, ["Alpha", "Bravo"]),
    ({"genres": ["Drama", "Horror"]}, ["Charlie", "Delta"]),  # any selected genre
    ({"resolutions": ["1080p", "4K"]}, ["Alpha", "Charlie", "Delta"]),
    ({"min_rating": 8}, ["Alpha", "Charlie"]),
    ({"max_runtime": 90}, ["Alpha", "Charlie"]),
    ({"kind": "movie", "genres": ["Comedy"], "year_min": 1990, "min_rating": 5}, ["Bravo"]),
    ({"kind": "series", "genres": ["Horror"]}, []),
])
def test_watch_filters(client, conn, body, expected):
    seed_watch(conn)
    result = watch(client, count=50, **body)
    assert titles_of(result) == expected
    assert result["matches"] == len(expected)


def test_title_in_two_selected_genres_appears_once(client, conn):
    seed_watch(conn)
    result = watch(client, count=50, genres=["Action", "Comedy"])
    assert titles_of(result) == ["Alpha", "Bravo"] and result["matches"] == 2


@pytest.mark.parametrize("count", [10, 25, 50])
def test_returns_requested_amount_with_no_repeats(client, conn, count):
    for i in range(60):
        items.create_manual(conn, "movie", f"Film {i}", 2000, None, "tmdb")
    conn.commit()
    result = watch(client, count=count)
    assert len(result["items"]) == count and result["matches"] == 60
    assert len({i["id"] for i in result["items"]}) == count


def test_lists_are_randomized(client, conn):
    for i in range(60):
        items.create_manual(conn, "movie", f"Film {i}", 2000, None, "tmdb")
    conn.commit()
    lists = {tuple(i["id"] for i in watch(client, count=10)["items"]) for _ in range(5)}
    assert len(lists) > 1
    assert len({i for picks in lists for i in picks}) > 10  # not one set in a new order


@pytest.mark.parametrize("count", [0, 5, 11, 100])
def test_other_amounts_are_rejected(client, count):
    assert client.post("/api/what-to-watch", json={"count": count}).status_code == 422


def test_backwards_year_range_gets_a_clear_error(client):
    response = client.post("/api/what-to-watch", json={"year_min": 2010, "year_max": 2000})
    assert response.status_code == 409 and "year range" in response.json()["detail"]


def test_filter_text_cant_inject_sql(client, conn):
    seed_watch(conn)
    result = watch(client, genres=["x') OR 1=1 --"], resolutions=["' OR ''='"])
    assert result["items"] == []
    assert conn.execute("SELECT COUNT(*) FROM media_item").fetchone()[0] == 5


def test_original_language_filters_library_and_what_to_watch(conn):
    en = linked_item(conn, original_language="en")
    ja = linked_item(conn, original_language="ja")
    conn.commit()
    assert [i["id"] for i in items.list_items(conn, language="ja")["items"]] == [ja]
    assert items.filter_options(conn)["languages"] == ["en", "ja"]
    picked = items.random_selection(conn, 10, languages=["en", "ja"])
    assert picked["matches"] == 2
    assert [i["id"] for i in items.random_selection(conn, 10, languages=["en"])["items"]] == [en]
