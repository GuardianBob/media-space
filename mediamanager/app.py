from __future__ import annotations

from dataclasses import asdict, replace
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import csv_import, db, fill, hotlink, items, scanner, volumes
from .config import EXTRA_ALLOWED_HOSTS, HOST, PROJECT_ROOT, load_settings, save_settings
from .jellyfin import JellyfinClient, JellyfinError
from .jobs import JobRunner
from .sync import run_sync
from .tmdb import TmdbClient, TmdbError

DIST = PROJECT_ROOT / "frontend" / "dist"


def get_conn():
    conn = db.connect()
    try:
        with conn:  # commit on success, roll back if the endpoint raises
            yield conn
    finally:
        conn.close()


Conn = Annotated[object, Depends(get_conn)]


@lru_cache(maxsize=1)
def _jellyfin_client(url: str, key: str) -> JellyfinClient:
    return JellyfinClient(url, key)


def jellyfin_client() -> JellyfinClient:
    settings = load_settings()
    return _jellyfin_client(settings.jellyfin_url, settings.jellyfin_api_key)


@lru_cache(maxsize=1)
def _tmdb_client(key: str) -> TmdbClient:
    return TmdbClient(key)


def tmdb_client() -> TmdbClient:
    return _tmdb_client(load_settings().tmdb_api_key)


def optional_tmdb() -> TmdbClient | None:
    try:
        return tmdb_client()
    except TmdbError:
        return None


class SettingsIn(BaseModel):
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    tmdb_api_key: str | None = None
    path_mappings: list[dict[str, str]] | None = None


class SaveTitle(BaseModel):
    tmdb_id: int
    kind: str


class ItemPatch(BaseModel):
    title: str | None = None
    year: int | None = None
    country: str | None = None
    summary: str | None = None
    poster_url: str | None = None
    trailer_url: str | None = None


class DeleteConfirm(BaseModel):
    token: str
    confirm_title: str


class WatchRequest(BaseModel):
    count: Literal[10, 25, 50] = 10
    kind: str = ""
    availability: str = ""
    year_min: int | None = None
    year_max: int | None = None
    genres: list[str] = []
    resolutions: list[str] = []
    languages: list[str] = []
    min_rating: float | None = None
    max_runtime: int | None = None


class RootIn(BaseModel):
    path: str
    label: str = ""


class AcceptIn(BaseModel):
    tmdb_id: int | None = None
    kind: str | None = None
    title: str | None = None
    year: int | None = None


def settings_view() -> dict:
    s = load_settings()
    # Keys never leave the server.
    return {
        "jellyfin_url": s.jellyfin_url,
        "jellyfin_api_key_set": bool(s.jellyfin_api_key),
        "tmdb_api_key_set": bool(s.tmdb_api_key),
        "path_mappings": s.path_mappings,
    }


def _open(resolved) -> dict:
    if not resolved.available:
        raise items.Conflict(resolved.reason or "File not available.")
    hotlink.open_in_file_manager(resolved.path)
    return {"opened": resolved.path}


def create_app(port: int = 8787) -> FastAPI:
    db.init_db()
    app = FastAPI(title="Media Manager")
    jobs = JobRunner()
    allowed_hosts = {f"{HOST}:{port}", f"localhost:{port}", f"127.0.0.1:{port}"} | EXTRA_ALLOWED_HOSTS
    allowed_origins = {f"http://{host}" for host in allowed_hosts}

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Any website can send requests to localhost. Rejecting foreign Host headers stops
        # DNS rebinding; rejecting foreign Origins stops pages from triggering actions
        # such as Hotlink or delete.
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin and origin not in allowed_origins:
            return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
        return await call_next(request)

    def error(status: int):
        async def handler(_request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=status)
        return handler

    app.add_exception_handler(items.NotFound, error(404))
    app.add_exception_handler(items.Conflict, error(409))
    app.add_exception_handler(hotlink.HotlinkError, error(409))
    app.add_exception_handler(csv_import.CsvError, error(400))
    app.add_exception_handler(JellyfinError, error(502))
    app.add_exception_handler(TmdbError, error(502))

    # ---- Library ---------------------------------------------------------------------

    @app.get("/api/items")
    def list_items(conn: Conn, q: str = "", kind: str = "", library: str = "", sort: str = "title",
                   page: Annotated[int, Query(ge=1)] = 1,
                   size: Annotated[int, Query(ge=1, le=100)] = 40,
                   order: str = "", letter: str = "", genre: str = "", year: int | None = None,
                   resolution: str = "", language: str = ""):
        return items.list_items(
            conn, q, kind, library, sort, page, size, order, letter, genre, year, resolution, language
        )

    # Declared before /api/items/{item_id}, which would otherwise capture this path.
    @app.get("/api/items/filter-options")
    def filter_options(conn: Conn):
        return items.filter_options(conn)

    @app.get("/api/items/{item_id}")
    def get_item(conn: Conn, item_id: int):
        return items.item_detail(conn, item_id, load_settings().path_mappings)

    @app.patch("/api/items/{item_id}")
    def patch_item(conn: Conn, item_id: int, patch: ItemPatch):
        items.update_fields(conn, item_id, patch.model_dump(exclude_unset=True))
        return {"ok": True}

    @app.post("/api/items/{item_id}/unlock/{field}")
    def unlock(conn: Conn, item_id: int, field: str):
        items.unlock_field(conn, item_id, field)
        return {"ok": True}

    @app.post("/api/what-to-watch")
    def what_to_watch(conn: Conn, body: WatchRequest):
        return items.random_selection(conn, **body.model_dump())

    # Hotlink. Only stored locations can be opened; the browser never supplies a path.
    @app.post("/api/items/{item_id}/open")
    def open_item(conn: Conn, item_id: int):
        return _open(items.best_location(conn, item_id, load_settings().path_mappings))

    @app.post("/api/locations/{location_id}/open")
    def open_location(conn: Conn, location_id: int):
        return _open(items.location_path(conn, location_id, load_settings().path_mappings))

    # Verify before Delete
    @app.get("/api/items/{item_id}/delete-preview")
    def delete_preview(conn: Conn, item_id: int):
        return items.delete_preview(conn, item_id, load_settings().path_mappings)

    @app.post("/api/items/{item_id}/delete")
    def delete_item(conn: Conn, item_id: int, body: DeleteConfirm):
        items.delete_confirm(conn, item_id, body.token, body.confirm_title)
        return {"ok": True}

    @app.get("/api/excluded")
    def excluded(conn: Conn):
        return items.list_excluded(conn)

    @app.delete("/api/excluded/{excluded_id}")
    def restore(conn: Conn, excluded_id: int):
        items.restore_excluded(conn, excluded_id)
        return {"ok": True}

    # ---- Jellyfin --------------------------------------------------------------------

    @app.get("/api/jellyfin/image/{item_id}")
    def jellyfin_image(
        item_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{32,36}$")],
        tag: Annotated[str | None, Query(pattern=r"^[\w-]+$")] = None,
    ):
        upstream = jellyfin_client().image(item_id, tag)
        return Response(
            upstream.content,
            media_type=upstream.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "private, max-age=604800"},
        )

    @app.post("/api/sync/jellyfin", status_code=202)
    def start_sync():
        client, tmdb = jellyfin_client(), optional_tmdb()

        def work(conn, progress):
            stats = run_sync(conn, client, progress)
            if tmdb:
                # The sync is already saved, so a TMDB problem here doesn't fail it.
                try:
                    stats["filled"] = fill.fill_gaps(conn, tmdb, progress)["improved"]
                except TmdbError as exc:
                    stats["fill_error"] = str(exc)
            return stats

        if not jobs.start("Jellyfin sync", work):
            raise items.Conflict("Another job is running.")
        return {"started": True}

    @app.post("/api/library/fill-missing", status_code=202)
    def fill_missing():
        client = tmdb_client()

        def work(conn, progress):
            return fill.fill_gaps(conn, client, progress, recheck=True)

        if not jobs.start("Fill missing data", work):
            raise items.Conflict("Another job is running.")
        return {"started": True}

    @app.get("/api/library/missing")
    def missing(conn: Conn):
        return fill.still_missing(conn)

    @app.get("/api/jobs")
    def job_status():
        return jobs.status()

    @app.get("/api/sync/last")
    def last_sync(conn: Conn):
        row = conn.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # ---- Discovery -------------------------------------------------------------------

    def annotated(conn, page: dict) -> dict:
        page["results"] = items.annotate_results(conn, page["results"])
        return page

    @app.get("/api/discover/trending")
    def trending(conn: Conn, page: int = 1):
        return annotated(conn, tmdb_client().trending(page))

    @app.get("/api/discover/upcoming")
    def upcoming(conn: Conn, page: int = 1):
        return annotated(conn, tmdb_client().upcoming(page))

    @app.get("/api/discover/search")
    def discover_search(conn: Conn, q: str, page: int = 1):
        return annotated(conn, tmdb_client().search(q, page))

    @app.get("/api/discover/genres/{kind}")
    def genres(kind: str):
        return tmdb_client().genres(kind)

    @app.get("/api/discover/{kind}")
    def discover(conn: Conn, kind: str, page: int = 1, genre: int | None = None):
        return annotated(conn, tmdb_client().discover(kind, page, genre))

    @app.get("/api/tmdb/{kind}/{tmdb_id}")
    def tmdb_details(conn: Conn, kind: str, tmdb_id: int):
        details = tmdb_client().details(kind, tmdb_id)
        return items.annotate_results(conn, [details])[0]

    @app.post("/api/discover/save")
    def save_title(conn: Conn, body: SaveTitle):
        if body.kind not in ("movie", "series"):
            raise HTTPException(422, "kind must be movie or series")
        return {"item_id": items.ensure_tmdb_item(conn, tmdb_client(), body.kind, body.tmdb_id, "tmdb")}

    # ---- Drive scanner ---------------------------------------------------------------

    @app.get("/api/volumes")
    def list_volumes():
        return [asdict(mount) for mount in volumes.list_mounts()]

    @app.get("/api/scan/roots")
    def roots(conn: Conn):
        return scanner.list_roots(conn)

    @app.post("/api/scan/roots")
    def add_root(conn: Conn, body: RootIn):
        return {"id": scanner.add_root(conn, body.path, body.label)}

    @app.delete("/api/scan/roots/{root_id}")
    def remove_root(conn: Conn, root_id: int):
        scanner.remove_root(conn, root_id)
        return {"ok": True}

    @app.post("/api/scan/roots/{root_id}/run", status_code=202)
    def run_scan(root_id: int):
        settings, client = load_settings(), optional_tmdb()

        def work(conn, progress):
            return scanner.run_scan(conn, root_id, settings, client, progress)

        if not jobs.start("Drive scan", work):
            raise items.Conflict("Another job is running.")
        return {"started": True}

    @app.get("/api/scan/candidates")
    def candidates(conn: Conn, status: str = "pending"):
        return scanner.list_candidates(conn, status)

    @app.post("/api/scan/candidates/accept-high-confidence")
    def accept_high(conn: Conn):
        return scanner.accept_high_confidence(conn, tmdb_client())

    @app.post("/api/scan/candidates/{candidate_id}/accept")
    def accept(conn: Conn, candidate_id: int, body: AcceptIn):
        item_id = scanner.accept_candidate(
            conn, candidate_id, optional_tmdb(), tmdb_id=body.tmdb_id, kind=body.kind,
            title=body.title, year=body.year,
        )
        return {"item_id": item_id}

    @app.post("/api/scan/candidates/{candidate_id}/ignore")
    def ignore(conn: Conn, candidate_id: int):
        scanner.set_ignored(conn, candidate_id, True)
        return {"ok": True}

    @app.post("/api/scan/candidates/{candidate_id}/unignore")
    def unignore(conn: Conn, candidate_id: int):
        scanner.set_ignored(conn, candidate_id, False)
        return {"ok": True}

    # ---- CSV -------------------------------------------------------------------------

    @app.post("/api/csv/preview")
    async def csv_preview(request: Request):
        # Raw body, so the bytes reach our own decoding (Excel often writes cp1252).
        data = await request.body()
        conn = db.connect()
        try:
            return csv_import.preview(conn, data)
        finally:
            conn.close()

    @app.post("/api/csv/apply")
    def csv_apply(conn: Conn, decisions: Annotated[list[dict], Body()]):
        return csv_import.apply(conn, decisions)

    # ---- Settings --------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings():
        return settings_view()

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        current = load_settings()
        # Blank keys mean "keep the current one"; the form never receives saved keys.
        changes = {k: v for k, v in body.model_dump(exclude_none=True).items()
                   if k == "path_mappings" or k == "jellyfin_url" or v}
        if "path_mappings" in changes:
            changes["path_mappings"] = [
                m for m in changes["path_mappings"] if m.get("from") and m.get("to")
            ]
        save_settings(replace(current, **changes))
        return settings_view()

    @app.post("/api/settings/test/jellyfin")
    def test_jellyfin():
        info = jellyfin_client().server_info()
        return {"message": f"Connected to {info.get('ServerName')} (Jellyfin {info.get('Version')})"}

    @app.post("/api/settings/jellyfin/scan", status_code=202)
    def scan_jellyfin_library():
        # Only starts Jellyfin's own scan; it runs on the server, and a Media Manager
        # sync afterwards picks up whatever it found.
        client = jellyfin_client()
        try:
            client.refresh_library()
        finally:
            client.close()
        return {"message": "Jellyfin is scanning its libraries. Sync here once it finishes."}

    @app.post("/api/settings/test/tmdb")
    def test_tmdb():
        tmdb_client().verify()
        return {"message": "TMDB key works."}

    # ---- Frontend --------------------------------------------------------------------

    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        index = DIST / "index.html"
        if not index.exists():
            return Response("Frontend not built. Run: cd frontend && npm install && npm run build",
                            media_type="text/plain")
        return FileResponse(index)

    return app
