from fastapi.testclient import TestClient

from mediamanager import app as app_module
from mediamanager import config, hotlink, items
from mediamanager.app import create_app
from mediamanager.config import Settings, load_settings, save_settings


def add_movie(conn, title="Heat", year=1995, jellyfin_id=None):
    item_id = items.create_manual(conn, "movie", title, year, None, "tmdb")
    if jellyfin_id:
        conn.execute(
            "UPDATE media_item SET jellyfin_id = ?, on_jellyfin = 1, source = 'jellyfin' WHERE id = ?",
            (jellyfin_id, item_id),
        )
    conn.commit()
    return item_id


# ---- Local-only protection

def test_foreign_host_is_rejected():
    client = TestClient(create_app(port=8787), base_url="http://evil.example:8787")
    assert client.get("/api/items").status_code == 403


def test_foreign_origin_cannot_trigger_actions(client, conn):
    item_id = add_movie(conn)
    response = client.post(f"/api/items/{item_id}/open", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


# ---- Settings

def test_settings_never_return_keys(client):
    save_settings(Settings(jellyfin_url="http://jf", jellyfin_api_key="secret", tmdb_api_key="s2"))
    body = client.get("/api/settings").json()
    assert "secret" not in str(body) and "s2" not in str(body)
    assert body["jellyfin_api_key_set"] and body["tmdb_api_key_set"]


def test_blank_key_on_save_keeps_existing(client):
    save_settings(Settings(jellyfin_api_key="secret"))
    client.put("/api/settings", json={"jellyfin_api_key": "", "path_mappings": [{"from": "/a", "to": "/b"}]})
    settings = load_settings()
    assert settings.jellyfin_api_key == "secret"
    assert settings.path_mappings == [{"from": "/a", "to": "/b"}]


def test_env_file_supplies_settings_and_isnt_copied_into_config(client):
    (config.PROJECT_ROOT / ".env").write_text(
        "# comment\nJELLYFIN_SERVER=http://jf:8096\nJELLYFIN_API='k123'\n", encoding="utf-8"
    )
    assert load_settings().jellyfin_url == "http://jf:8096"
    assert load_settings().jellyfin_api_key == "k123"
    client.put("/api/settings", json={"path_mappings": []})
    assert "k123" not in (config.data_dir() / "config.json").read_text(encoding="utf-8")
    assert load_settings().jellyfin_api_key == "k123"


# ---- Browser

def test_browse_filters_search_and_paging(client, conn):
    add_movie(conn, "Heat", 1995, jellyfin_id="a" * 32)
    other = add_movie(conn, "Alien", 1979)
    conn.execute(
        "INSERT INTO credit (item_id, name, ord) VALUES (?, 'Sigourney Weaver', 0)", (other,)
    )
    conn.commit()
    titles = lambda **params: [i["title"] for i in client.get("/api/items", params=params).json()["items"]]  # noqa: E731
    assert titles() == ["Alien", "Heat"]
    assert titles(q="hea") == ["Heat"]
    assert titles(q="Weaver") == ["Alien"]
    assert titles(library="not_jellyfin") == ["Alien"]
    assert titles(library="jellyfin") == ["Heat"]
    assert titles(sort="release") == ["Heat", "Alien"]
    assert client.get("/api/items", params={"size": 1, "page": 2}).json()["items"][0]["title"] == "Heat"


def test_detail_marks_unreachable_jellyfin_file(client, conn):
    item_id = add_movie(conn, jellyfin_id="a" * 32)
    conn.execute(
        "INSERT INTO location (item_id, origin, path) VALUES (?, 'jellyfin', '/nonexistent/x.mkv')",
        (item_id,),
    )
    conn.commit()
    location = client.get(f"/api/items/{item_id}").json()["locations"][0]
    assert location["available"] is False and "path mapping" in location["reason"]


def test_patch_rejects_blank_title(client, conn):
    item_id = add_movie(conn)
    assert client.patch(f"/api/items/{item_id}", json={"title": "  "}).status_code == 409


# ---- Hotlink

def test_hotlink_opens_mapped_path(client, conn, tmp_path, monkeypatch):
    (tmp_path / "Movies").mkdir()
    (tmp_path / "Movies" / "Heat.mkv").touch()
    save_settings(Settings(path_mappings=[{"from": "/media", "to": str(tmp_path)}]))
    item_id = add_movie(conn, jellyfin_id="a" * 32)
    conn.execute(
        "INSERT INTO location (item_id, origin, path) VALUES (?, 'jellyfin', '/media/Movies/Heat.mkv')",
        (item_id,),
    )
    conn.commit()
    opened = []
    monkeypatch.setattr(hotlink, "open_in_file_manager", lambda path, platform=None: opened.append(path))
    assert client.post(f"/api/items/{item_id}/open").status_code == 200
    assert opened == [str(tmp_path / "Movies" / "Heat.mkv")]


def test_hotlink_without_file_explains_why(client, conn, monkeypatch):
    item_id = add_movie(conn)
    monkeypatch.setattr(hotlink, "open_in_file_manager", lambda *_: pytest_fail())
    response = client.post(f"/api/items/{item_id}/open")
    assert response.status_code == 409 and "No file" in response.json()["detail"]


def pytest_fail():
    raise AssertionError("must not open anything")


# ---- Verify before Delete

def test_preview_changes_nothing_and_reports_consequences(client, conn):
    item_id = add_movie(conn, jellyfin_id="a" * 32)
    preview = client.get(f"/api/items/{item_id}/delete-preview").json()
    assert preview["will_exclude"] is True and preview["token"]
    assert client.get(f"/api/items/{item_id}").status_code == 200


def test_delete_needs_matching_title_and_token(client, conn):
    item_id = add_movie(conn)
    token = client.get(f"/api/items/{item_id}/delete-preview").json()["token"]
    url = f"/api/items/{item_id}/delete"
    assert client.post(url, json={"token": token, "confirm_title": "Wrong"}).status_code == 409
    assert client.post(url, json={"token": "bad", "confirm_title": "Heat"}).status_code == 409
    assert client.get(f"/api/items/{item_id}").status_code == 200
    assert client.post(url, json={"token": token, "confirm_title": "Heat"}).status_code == 200
    assert client.get(f"/api/items/{item_id}").status_code == 404


def test_delete_rejected_if_record_changed_after_review(client, conn):
    item_id = add_movie(conn)
    token = client.get(f"/api/items/{item_id}/delete-preview").json()["token"]
    conn.execute("INSERT INTO credit (item_id, name, ord) VALUES (?, 'New', 0)", (item_id,))
    conn.commit()
    response = client.post(f"/api/items/{item_id}/delete", json={"token": token, "confirm_title": "Heat"})
    assert response.status_code == 409 and "changed" in response.json()["detail"]


def test_delete_leaves_file_and_writes_log(client, conn, tmp_path):
    media = tmp_path / "Heat.mkv"
    media.touch()
    item_id = add_movie(conn)
    conn.execute(
        "INSERT INTO location (item_id, origin, path) VALUES (?, 'jellyfin', ?)", (item_id, str(media))
    )
    conn.commit()
    token = client.get(f"/api/items/{item_id}/delete-preview").json()["token"]
    client.post(f"/api/items/{item_id}/delete", json={"token": token, "confirm_title": "Heat"})
    assert media.exists()
    assert conn.execute("SELECT title FROM delete_log").fetchone()[0] == "Heat"


# ---- Discovery

def test_discover_results_show_library_status(client, conn, monkeypatch):
    add_movie(conn, "Heat", 1995)
    results = [
        {"tmdb_id": 1, "kind": "movie", "title": "Heat", "year": 1995},  # title + year match
        {"tmdb_id": 2, "kind": "movie", "title": "New", "year": 2020},
    ]

    class FakeTmdb:
        def trending(self, page):
            return {"results": results, "page": page, "total_pages": 1}

    monkeypatch.setattr(app_module, "tmdb_client", lambda: FakeTmdb())
    body = client.get("/api/discover/trending").json()["results"]
    assert body[0]["status"] == "saved" and body[1]["status"] is None


def test_discovery_without_key_gives_clear_error(client):
    response = client.get("/api/discover/trending")
    assert response.status_code == 502 and "TMDB" in response.json()["detail"]


# ---- Sorting and filtering

def seed_library(conn):
    from mediamanager.db import sort_title

    rows = [
        # title, kind, year, release_date, rating, runtime, added_at, genres
        ("The Matrix", "movie", 1999, "1999-03-31", 8.2, 136, "2024-01-03", ["Action", "Sci-Fi"]),
        ("10 Things I Hate About You", "movie", 1999, "1999-03-30", 7.3, 97, "2024-01-01", ["Comedy"]),
        ("2 Guns", "movie", 2013, "2013-08-02", 6.6, None, "2024-01-05", ["Action"]),
        ("A Knight's Tale", "movie", 2001, None, None, 132, "2024-01-02", []),
        ("Élite", "series", 2018, "2018-10-05", 8.0, 50, "2024-01-04", ["Drama"]),
    ]
    for title, kind, year, release, rating, runtime, added, genres in rows:
        item_id = items.create_manual(conn, kind, title, year, None, "tmdb")
        conn.execute(
            "UPDATE media_item SET release_date = ?, community_rating = ?, runtime_minutes = ?,"
            " added_at = ? WHERE id = ?",
            (release, rating, runtime, added, item_id),
        )
        conn.executemany(
            "INSERT INTO item_genre (item_id, name) VALUES (?, ?)", [(item_id, g) for g in genres]
        )
    conn.commit()


def browse(client, **params):
    return [i["title"] for i in client.get("/api/items", params=params).json()["items"]]


def test_title_sort_ignores_articles_and_orders_numbers(client, conn):
    seed_library(conn)
    expected = ["2 Guns", "10 Things I Hate About You", "Élite", "A Knight's Tale", "The Matrix"]
    assert browse(client) == expected
    assert browse(client, order="desc") == expected[::-1]


def test_letter_filter_uses_sort_title(client, conn):
    seed_library(conn)
    assert browse(client, letter="M") == ["The Matrix"]
    assert browse(client, letter="k") == ["A Knight's Tale"]
    assert browse(client, letter="E") == ["Élite"]  # accents file under the base letter
    assert browse(client, letter="T") == []  # "The Matrix" isn't under T
    assert browse(client, letter="#") == ["2 Guns", "10 Things I Hate About You"]


def test_release_date_sort_both_ways_uses_year_when_date_missing(client, conn):
    seed_library(conn)
    newest = ["Élite", "2 Guns", "A Knight's Tale", "The Matrix", "10 Things I Hate About You"]
    assert browse(client, sort="release") == newest
    assert browse(client, sort="release", order="asc") == newest[::-1]


def test_date_added_sort_both_ways(client, conn):
    seed_library(conn)
    assert browse(client, sort="added")[0] == "2 Guns"
    assert browse(client, sort="added", order="asc")[0] == "10 Things I Hate About You"


def test_blank_values_sort_last_in_both_directions(client, conn):
    seed_library(conn)
    assert browse(client, sort="rating")[0] == "The Matrix"
    assert browse(client, sort="rating")[-1] == "A Knight's Tale"
    assert browse(client, sort="rating", order="asc")[-1] == "A Knight's Tale"
    assert browse(client, sort="runtime")[0] == "The Matrix"
    assert browse(client, sort="runtime", order="asc")[-1] == "2 Guns"


def test_genre_and_year_filters_combine_with_everything(client, conn):
    seed_library(conn)
    assert browse(client, genre="Action") == ["2 Guns", "The Matrix"]
    assert browse(client, year=1999) == ["10 Things I Hate About You", "The Matrix"]
    assert browse(client, genre="Action", year=1999, letter="M", kind="movie") == ["The Matrix"]
    assert browse(client, kind="series", genre="Action") == []


def test_filter_options_list_whats_in_the_library(client, conn):
    seed_library(conn)
    options = client.get("/api/items/filter-options").json()
    assert options["genres"] == ["Action", "Comedy", "Drama", "Sci-Fi"]
    assert options["years"] == [2018, 2013, 2001, 1999]


def test_unknown_sort_and_order_fall_back_safely(client, conn):
    seed_library(conn)
    assert browse(client, sort="title; DROP TABLE media_item", order="sideways")[0] == "2 Guns"


def test_paging_never_repeats_titles_when_sort_values_tie(client, conn):
    for i in range(30):
        items.create_manual(conn, "movie", "Same", 2000 + i, None, "tmdb")
    conn.commit()
    seen = []
    for page in (1, 2, 3):
        body = client.get("/api/items", params={"sort": "rating", "size": 10, "page": page}).json()
        seen += [i["id"] for i in body["items"]]
    assert len(set(seen)) == 30


def test_title_edit_updates_where_it_files(client, conn):
    seed_library(conn)
    item_id = client.get("/api/items", params={"letter": "M"}).json()["items"][0]["id"]
    client.patch(f"/api/items/{item_id}", json={"title": "Zion"})
    assert browse(client, letter="Z") == ["Zion"]


def test_old_database_is_upgraded_in_place(tmp_path, monkeypatch):
    import sqlite3

    from mediamanager import db

    monkeypatch.setenv("MEDIA_MANAGER_DATA", str(tmp_path / "old"))
    old = sqlite3.connect(config.data_dir() / "media.db")
    old.executescript(
        """CREATE TABLE media_item (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
             norm_title TEXT NOT NULL, year INTEGER, country TEXT, summary TEXT, poster_url TEXT,
             trailer_url TEXT, source TEXT NOT NULL, on_jellyfin INTEGER NOT NULL DEFAULT 0,
             jellyfin_id TEXT UNIQUE, tmdb_id INTEGER, imdb_id TEXT,
             locked_fields TEXT NOT NULL DEFAULT '[]', added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
           INSERT INTO media_item (kind, title, norm_title, source, added_at, updated_at)
             VALUES ('movie', 'The Matrix', 'the matrix', 'jellyfin', 'x', 'x');"""
    )
    old.close()
    db.init_db()
    conn = db.connect()
    assert items.list_items(conn, letter="M")["total"] == 1
    conn.close()
