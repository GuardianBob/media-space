"""Jellyfin API client. Auth header and item queries follow Seerr's server/api/jellyfin.ts."""

from __future__ import annotations

from typing import Iterator

import httpx

PAGE_SIZE = 200
ITEM_FIELDS = (
    "Overview,People,ProviderIds,Path,MediaSources,RemoteTrailers,"
    "ProductionLocations,DateCreated,Genres"
)
EPISODE_FIELDS = "Overview,Path,MediaSources,PremiereDate"


class JellyfinError(Exception):
    pass


class JellyfinClient:
    def __init__(self, url: str, api_key: str, transport: httpx.BaseTransport | None = None):
        if not url or not api_key:
            raise JellyfinError("Jellyfin URL and API key aren't set. Add them in Settings.")
        auth = (
            'MediaBrowser Client="Media Manager", Device="Media Manager", '
            f'DeviceId="media-space", Version="0.1.0", Token="{api_key}"'
        )
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": auth, "Accept": "application/json"},
            timeout=60,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params) -> httpx.Response:
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Couldn't reach Jellyfin: {exc}") from exc
        if response.status_code in (401, 403):
            raise JellyfinError("Jellyfin rejected the API key.")
        if response.status_code >= 400:
            raise JellyfinError(f"Jellyfin returned HTTP {response.status_code} for {path}")
        return response

    def refresh_library(self) -> None:
        """Ask Jellyfin to scan its libraries for new and changed files (needs an admin key)."""
        try:
            response = self._http.post("/Library/Refresh")
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Couldn't reach Jellyfin: {exc}") from exc
        if response.status_code == 401:
            raise JellyfinError("Jellyfin rejected the API key.")
        if response.status_code == 403:
            raise JellyfinError("Jellyfin refused: starting a library scan needs an administrator's API key.")
        if response.status_code >= 400:
            raise JellyfinError(f"Jellyfin returned HTTP {response.status_code} for /Library/Refresh")

    def server_info(self) -> dict:
        return self._get("/System/Info").json()

    def _paged(self, **params) -> Iterator[dict]:
        start = 0
        while True:
            data = self._get(
                "/Items", Recursive="true", StartIndex=start, Limit=PAGE_SIZE, **params
            ).json()
            items = data.get("Items") or []
            yield from items
            start += len(items)
            if not items or start >= data.get("TotalRecordCount", 0):
                return

    def titles(self) -> list[dict]:
        # Filtered again here: servers have been seen returning BoxSets despite the query.
        rows = self._paged(IncludeItemTypes="Movie,Series", Fields=ITEM_FIELDS)
        return [row for row in rows if row.get("Type") in ("Movie", "Series")]

    def episodes(self) -> list[dict]:
        return list(self._paged(IncludeItemTypes="Episode", Fields=EPISODE_FIELDS))

    def image(self, item_id: str, tag: str | None) -> httpx.Response:
        params = {"maxHeight": 750, "quality": 90}
        if tag:
            params["tag"] = tag
        return self._get(f"/Items/{item_id}/Images/Primary", **params)
