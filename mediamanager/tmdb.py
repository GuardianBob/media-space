"""TMDB client: the data source behind discovery, as in Seerr."""

from __future__ import annotations

import httpx

IMAGE_BASE = "https://image.tmdb.org/t/p"
# TMDB returns only English videos unless asked, which hides trailers for many foreign titles.
VIDEO_LANGUAGES = "en,ja,ko,zh,fr,es,de,it,pt,ru,hi,null"


class TmdbError(Exception):
    pass


def poster(path: str | None, size: str = "w500") -> str | None:
    return f"{IMAGE_BASE}/{size}{path}" if path else None


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def normalize_result(raw: dict, kind: str | None = None) -> dict | None:
    """One TMDB list entry as a card. People and other non-title results give None."""
    media_type = kind or raw.get("media_type")
    if media_type == "movie":
        title, date = raw.get("title"), raw.get("release_date")
    elif media_type == "tv":
        title, date = raw.get("name"), raw.get("first_air_date")
    else:
        return None
    return {
        "tmdb_id": raw["id"],
        "kind": "movie" if media_type == "movie" else "series",
        "title": title or "",
        "year": _year(date),
        "release_date": date or None,
        "summary": raw.get("overview") or "",
        "poster_url": poster(raw.get("poster_path")),
        "score": raw.get("vote_average"),
    }


def _trailer(videos: list[dict]) -> str | None:
    youtube = [v for v in videos if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")]
    # A trailer beats a teaser, then English beats other languages, then official beats not.
    youtube.sort(key=lambda v: (
        v["type"] != "Trailer", v.get("iso_639_1") not in ("en", None), not v.get("official"),
    ))
    return f"https://www.youtube.com/watch?v={youtube[0]['key']}" if youtube else None


class TmdbClient:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise TmdbError("The TMDB API key isn't set. Add it in Settings.")
        # v4 read tokens are JWTs and go in a header; v3 keys go in the query string.
        self._params = {} if api_key.startswith("eyJ") else {"api_key": api_key}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key.startswith("eyJ") else {}
        self._http = httpx.Client(
            base_url="https://api.themoviedb.org/3",
            headers=headers,
            timeout=20,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params) -> dict:
        try:
            response = self._http.get(path, params={**self._params, **params})
        except httpx.HTTPError as exc:
            # Never include the exception's URL: it can contain the v3 key.
            raise TmdbError(f"Couldn't reach TMDB ({type(exc).__name__}).") from None
        if response.status_code == 401:
            raise TmdbError("TMDB rejected the API key.")
        if response.status_code >= 400:
            raise TmdbError(f"TMDB returned HTTP {response.status_code} for {path}")
        return response.json()

    def _page(self, data: dict, kind: str | None = None) -> dict:
        results = [normalize_result(r, kind) for r in data.get("results", [])]
        return {
            "results": [r for r in results if r],
            "page": data.get("page", 1),
            "total_pages": data.get("total_pages", 1),
        }

    def verify(self) -> None:
        self._get("/configuration")

    def search(self, query: str, page: int = 1) -> dict:
        return self._page(self._get("/search/multi", query=query, page=page))

    def search_kind(self, kind: str, title: str, year: int | None = None) -> list[dict]:
        tmdb_kind = "movie" if kind == "movie" else "tv"
        params: dict = {"query": title}
        if year:
            params["year" if tmdb_kind == "movie" else "first_air_date_year"] = year
        return self._page(self._get(f"/search/{tmdb_kind}", **params), tmdb_kind)["results"]

    def trending(self, page: int = 1) -> dict:
        return self._page(self._get("/trending/all/week", page=page))

    def discover(self, kind: str, page: int = 1, genre: int | None = None) -> dict:
        tmdb_kind = "movie" if kind == "movie" else "tv"
        params: dict = {"page": page, "sort_by": "popularity.desc"}
        if genre:
            params["with_genres"] = genre
        return self._page(self._get(f"/discover/{tmdb_kind}", **params), tmdb_kind)

    def upcoming(self, page: int = 1) -> dict:
        return self._page(self._get("/movie/upcoming", page=page), "movie")

    def genres(self, kind: str) -> list[dict]:
        tmdb_kind = "movie" if kind == "movie" else "tv"
        return self._get(f"/genre/{tmdb_kind}/list").get("genres", [])

    def details(self, kind: str, tmdb_id: int) -> dict:
        """Full record in the shape the database stores."""
        tmdb_kind = "movie" if kind == "movie" else "tv"
        raw = self._get(
            f"/{tmdb_kind}/{tmdb_id}", append_to_response="credits,videos,external_ids",
            include_video_language=VIDEO_LANGUAGES,
        )
        card = normalize_result(raw, tmdb_kind)
        countries = [c["name"] for c in raw.get("production_countries") or [] if c.get("name")]
        cast = (raw.get("credits") or {}).get("cast") or []
        return {
            **card,
            "country": ", ".join(countries) or None,
            "original_language": raw.get("original_language") or None,
            "community_rating": raw.get("vote_average") or None,
            "runtime_minutes": raw.get("runtime") or next(iter(raw.get("episode_run_time") or []), None),
            "genres": [g["name"] for g in raw.get("genres") or [] if g.get("name")],
            "trailer_url": _trailer((raw.get("videos") or {}).get("results") or []),
            "imdb_id": (raw.get("external_ids") or {}).get("imdb_id") or raw.get("imdb_id"),
            "credits": [
                {
                    "name": person["name"],
                    "character": person.get("character"),
                    "image_url": poster(person.get("profile_path"), "w185"),
                }
                for person in cast[:20]
            ],
        }

    def season_episodes(self, tmdb_id: int, season: int) -> list[dict]:
        data = self._get(f"/tv/{tmdb_id}/season/{season}")
        return [
            {
                "season_number": season,
                "episode_number": ep.get("episode_number"),
                "title": ep.get("name"),
                "summary": ep.get("overview"),
                "air_date": ep.get("air_date"),
            }
            for ep in data.get("episodes") or []
        ]
