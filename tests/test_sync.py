import json

import httpx
import pytest

from mediamanager import items
from mediamanager.jellyfin import JellyfinClient, JellyfinError
from mediamanager.sync import run_sync

MOVIE_ID = "a" * 32
SERIES_ID = "b" * 32
BOXSET_ID = "c" * 32


def movie(**overrides):
    return {
        "Id": MOVIE_ID, "Type": "Movie", "Name": "Heat", "ProductionYear": 1995,
        "Overview": "Cops and robbers.", "ProviderIds": {"Tmdb": "949", "Imdb": "tt0113277"},
        "ImageTags": {"Primary": "tag1"}, "ProductionLocations": ["United States of America"],
        "RemoteTrailers": [{"Url": "https://youtu.be/x"}],
        "People": [{"Id": "d" * 32, "Name": "Al Pacino", "Role": "Hanna", "Type": "Actor",
                    "PrimaryImageTag": "p1"},
                   {"Id": "e" * 32, "Name": "Michael Mann", "Type": "Director"}],
        "MediaSources": [{"Path": "/media/Movies/Heat.mkv", "Size": 100}],
        **overrides,
    }


def series():
    return {"Id": SERIES_ID, "Type": "Series", "Name": "Farscape", "ProductionYear": 1999,
            "Path": "/media/TV Shows/Farscape", "ProviderIds": {"Tmdb": "1"}}


def episode(index=1, **overrides):
    return {"Id": f"{index:032x}", "Type": "Episode", "SeriesId": SERIES_ID, "ParentIndexNumber": 1,
            "IndexNumber": index, "Name": f"Ep {index}",
            "Path": f"/media/TV Shows/Farscape/S01E{index:02}.mkv", **overrides}


def fake_server(titles, episodes=(), page_log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if "Token=" not in request.headers["authorization"]:
            return httpx.Response(401)
        params = request.url.params
        rows = list(episodes) if params["IncludeItemTypes"] == "Episode" else list(titles)
        start, limit = int(params["StartIndex"]), int(params["Limit"])
        if page_log is not None:
            page_log.append(start)
        return httpx.Response(200, json={
            "Items": rows[start:start + limit], "TotalRecordCount": len(rows), "StartIndex": start,
        })
    return JellyfinClient("http://jf:8096/", "key", transport=httpx.MockTransport(handler))


def sync(conn, titles, episodes=()):
    return run_sync(conn, fake_server(titles, episodes))


def only_item(conn):
    return conn.execute("SELECT * FROM media_item").fetchone()


def test_sync_stores_all_required_fields(conn):
    stats = sync(conn, [movie()])
    row = only_item(conn)
    assert stats["added"] == 1
    assert (row["title"], row["year"], row["summary"]) == ("Heat", 1995, "Cops and robbers.")
    assert row["poster_url"] == f"/api/jellyfin/image/{MOVIE_ID}?tag=tag1"
    assert row["trailer_url"] == "https://youtu.be/x"
    assert row["country"] == "United States of America"
    assert row["on_jellyfin"] == 1 and row["tmdb_id"] == 949
    assert [c["name"] for c in conn.execute("SELECT name FROM credit")] == ["Al Pacino"]
    location = conn.execute("SELECT * FROM location").fetchone()
    assert location["path"] == "/media/Movies/Heat.mkv" and location["size_bytes"] == 100


def test_boxsets_returned_by_server_are_filtered_out(conn):
    # Observed on a real Jellyfin 12.4 server despite IncludeItemTypes=Movie,Series.
    sync(conn, [movie(), {"Id": BOXSET_ID, "Type": "BoxSet", "Name": "Collection"}])
    assert conn.execute("SELECT COUNT(*) FROM media_item").fetchone()[0] == 1


def test_all_pages_are_fetched(conn):
    titles = [movie(Id=f"{i:032x}", Name=f"M{i}") for i in range(450)]
    pages = []
    run_sync(conn, fake_server(titles, page_log=pages))
    assert conn.execute("SELECT COUNT(*) FROM media_item").fetchone()[0] == 450
    assert pages[:3] == [0, 200, 400]


def test_resync_is_idempotent(conn):
    sync(conn, [movie()])
    stats = sync(conn, [movie()])
    assert stats["added"] == stats["updated"] == 0
    assert conn.execute("SELECT COUNT(*) FROM location").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM credit").fetchone()[0] == 1


def test_edits_survive_sync_and_other_fields_still_update(conn):
    sync(conn, [movie()])
    item_id = only_item(conn)["id"]
    items.update_fields(conn, item_id, {"country": "France"})
    conn.commit()
    sync(conn, [movie(Overview="New summary")])
    row = only_item(conn)
    assert row["country"] == "France"
    assert row["summary"] == "New summary"
    assert json.loads(row["locked_fields"]) == ["country"]


def test_unlocking_returns_field_to_jellyfin(conn):
    sync(conn, [movie()])
    item_id = only_item(conn)["id"]
    items.update_fields(conn, item_id, {"country": "France"})
    items.unlock_field(conn, item_id, "country")
    conn.commit()
    sync(conn, [movie()])
    assert only_item(conn)["country"] == "United States of America"


def test_deleted_item_stays_excluded_until_restored(conn):
    sync(conn, [movie()])
    item_id = only_item(conn)["id"]
    preview = items.delete_preview(conn, item_id, [])
    items.delete_confirm(conn, item_id, preview["token"], "Heat")
    conn.commit()

    stats = sync(conn, [movie()])
    assert stats["excluded"] == 1 and only_item(conn) is None

    # Jellyfin can assign a new ID after a rescan; the TMDB ID still identifies it.
    sync(conn, [movie(Id="f" * 32)])
    assert only_item(conn) is None

    items.restore_excluded(conn, items.list_excluded(conn)[0]["id"])
    conn.commit()
    sync(conn, [movie()])
    assert only_item(conn)["title"] == "Heat"


def test_tmdb_saved_title_merges_when_it_arrives_on_jellyfin(conn):
    item_id = items.create_manual(conn, "movie", "Heat", 1995, None, "tmdb")
    conn.execute("UPDATE media_item SET tmdb_id = 949 WHERE id = ?", (item_id,))
    conn.commit()
    sync(conn, [movie()])
    rows = conn.execute("SELECT * FROM media_item").fetchall()
    assert len(rows) == 1 and rows[0]["id"] == item_id
    assert rows[0]["on_jellyfin"] == 1 and rows[0]["source"] == "tmdb"


def test_item_gone_from_jellyfin_is_flagged_not_deleted(conn):
    sync(conn, [movie()])
    stats = sync(conn, [])
    row = only_item(conn)
    assert stats["removed"] == 1 and row["on_jellyfin"] == 0
    assert conn.execute("SELECT COUNT(*) FROM location").fetchone()[0] == 0
    sync(conn, [movie()])
    assert only_item(conn)["on_jellyfin"] == 1


def test_episodes_stored_including_unnumbered_and_orphaned(conn):
    # All three shapes occur on a real server.
    stats = sync(conn, [series()], [
        episode(1),
        episode(2, ParentIndexNumber=None, IndexNumber=None),
        episode(3, SeriesId=None),
    ])
    assert stats["episodes"] == 2
    assert conn.execute("SELECT COUNT(*) FROM episode").fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM location WHERE episode_id IS NOT NULL"
    ).fetchone()[0] == 2


def test_episode_gone_from_jellyfin_is_removed_and_resync_doesnt_duplicate(conn):
    sync(conn, [series()], [episode(1), episode(2)])
    sync(conn, [series()], [episode(1)])
    assert conn.execute("SELECT COUNT(*) FROM episode").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM location WHERE episode_id IS NOT NULL"
    ).fetchone()[0] == 1


def test_failed_fetch_changes_nothing(conn):
    sync(conn, [movie()])

    def broken(_request):
        return httpx.Response(500)

    client = JellyfinClient("http://jf", "key", transport=httpx.MockTransport(broken))
    with pytest.raises(JellyfinError):
        run_sync(conn, client)
    assert only_item(conn)["on_jellyfin"] == 1


def test_bad_key_gives_a_clear_error():
    client = JellyfinClient(
        "http://jf", "key", transport=httpx.MockTransport(lambda _r: httpx.Response(401))
    )
    with pytest.raises(JellyfinError, match="rejected"):
        client.server_info()


def test_sync_stores_sort_and_filter_fields(conn):
    sync(conn, [movie(
        PremiereDate="1995-12-15T00:00:00.0000000Z", CommunityRating=8.3,
        RunTimeTicks=170 * 600_000_000, Genres=["Crime", "Drama"],
    )])
    row = only_item(conn)
    assert (row["release_date"], row["community_rating"], row["runtime_minutes"]) == ("1995-12-15", 8.3, 170)
    assert row["sort_title"] == "heat"
    genres = [r["name"] for r in conn.execute("SELECT name FROM item_genre ORDER BY name")]
    assert genres == ["Crime", "Drama"]


def test_resync_replaces_genres_and_empty_list_keeps_them(conn):
    sync(conn, [movie(Genres=["Crime"])])
    sync(conn, [movie(Genres=["Thriller"])])
    sync(conn, [movie()])
    assert [r["name"] for r in conn.execute("SELECT name FROM item_genre")] == ["Thriller"]


def test_library_scan_posts_refresh_and_explains_non_admin_key():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(204 if len(seen) == 1 else 403)

    client = JellyfinClient("http://jf", "key", transport=httpx.MockTransport(handler))
    client.refresh_library()
    assert seen == [("POST", "/Library/Refresh")]
    with pytest.raises(JellyfinError, match="administrator"):
        client.refresh_library()
