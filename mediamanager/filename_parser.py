"""Work out title, year, season and episode from a media file's path."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_YEAR = r"(?:19|20)\d{2}"
_EPISODE_PATTERNS = (
    re.compile(r"\bS(?P<s>\d{1,2})[ ._-]*E(?P<e>\d{1,3})", re.I),
    re.compile(r"\b(?P<s>\d{1,2})x(?P<e>\d{2,3})\b", re.I),
    re.compile(r"\bSeason[ ._-]*(?P<s>\d{1,2})[ ._-]*Episode[ ._-]*(?P<e>\d{1,3})", re.I),
)
_SEASON_DIR = re.compile(r"^(?:season|series|s)[ ._-]*(?P<s>\d{1,2})$", re.I)
_BARE_EPISODE = re.compile(r"^(?:e|ep|episode)?[ ._-]*(?P<e>\d{1,3})\b", re.I)
_JUNK = re.compile(
    r"\b(?:480p|576p|720p|1080p|1080i|2160p|4k|uhd|hdr|hdr10|dv|sdr|bluray|blu ray|brrip|bdrip|"
    r"remux|webrip|web dl|webdl|web|hdtv|dvdrip|dvd|x264|x265|h264|h265|hevc|avc|aac|ac3|dts|"
    r"ddp?5 1|atmos|proper|repack|extended|unrated|remastered|imax|multi|dubbed|subbed)\b.*$",
    re.I,
)


@dataclass
class Parsed:
    kind: str
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None


def _clean(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", " ", text)
    if " " not in text.strip():
        text = text.replace(".", " ").replace("_", " ")
    text = _JUNK.sub(" ", text)
    text = re.sub(r"[()]", " ", text)
    return " ".join(text.split()).strip(" -._")


def _title_and_year(text: str) -> tuple[str, int | None]:
    # The last year wins, and never one at the very start: "2001 A Space Odyssey 1968".
    years = [m for m in re.finditer(rf"(?<![\d]){_YEAR}(?!\d)", text) if m.start() > 0]
    if years:
        last = years[-1]
        return _clean(text[: last.start()]), int(last.group())
    return _clean(text), None


def parse_path(rel_path: str) -> Parsed:
    path = PurePosixPath(rel_path)
    stem, parents = path.stem, list(path.parent.parts)

    for pattern in _EPISODE_PATTERNS:
        match = pattern.search(stem)
        if match:
            before = stem[: match.start()]
            title, year = _title_and_year(before)
            if not title:
                title, year = _series_from_dirs(parents)
            return Parsed(
                "series", title or stem, year, int(match.group("s")), int(match.group("e"))
            )

    # Show/Season 2/05 - Name.mkv
    if parents and (season_dir := _SEASON_DIR.match(parents[-1])):
        episode = _BARE_EPISODE.match(stem)
        title, year = _series_from_dirs(parents)
        return Parsed(
            "series", title or stem, year, int(season_dir.group("s")),
            int(episode.group("e")) if episode else None,
        )

    title, year = _title_and_year(stem)
    if not year and parents:
        # Movie Name (2019)/movie.mkv: the folder is often better named than the file.
        folder_title, folder_year = _title_and_year(parents[-1])
        if folder_year:
            return Parsed("movie", folder_title, folder_year)
    return Parsed("movie", title or stem, year)


def _series_from_dirs(parents: list[str]) -> tuple[str, int | None]:
    for part in reversed(parents):
        if not _SEASON_DIR.match(part):
            return _title_and_year(part)
    return "", None
