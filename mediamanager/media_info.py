"""Video dimensions and the resolution labels derived from them."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter

RESOLUTIONS = ("240p", "360p", "480p", "576p", "720p", "1080p", "1440p", "4K", "8K")
# (minimum width, minimum height, label), highest first. Either dimension qualifies, because
# widescreen films are cropped: 1920x800 is a 1080p encode, and 1440x1080 is too.
_THRESHOLDS = (
    (6000, 3000, "8K"),
    (3000, 1600, "4K"),
    (2200, 1300, "1440p"),
    (1700, 900, "1080p"),
    (1100, 620, "720p"),
    (1000, 520, "576p"),
    (700, 400, "480p"),
    (480, 300, "360p"),
)
_NAME_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:(240|360|480|576|720|1080|1440)[pi]|(2160p|4k|uhd)|(4320p|8k))(?![a-z0-9])", re.I
)


def resolution_label(width: int | None, height: int | None) -> str | None:
    if not width and not height:
        return None
    width, height = width or 0, height or 0
    for min_width, min_height, label in _THRESHOLDS:
        if width >= min_width or height >= min_height:
            return label
    return "240p"


def label_from_filename(path: str) -> str | None:
    """A label from a name like Movie.2019.1080p.mkv. Claimed, not measured."""
    match = _NAME_PATTERN.search(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    if not match:
        return None
    if match.group(1):
        return f"{match.group(1)}p"
    return "4K" if match.group(2) else "8K"


def probe_file(path: str) -> tuple[int | None, int | None, str | None]:
    """(width, height, label) from ffprobe if installed, else a label from the filename."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            done = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v",
                 "-show_entries", "stream=width,height:stream_disposition=attached_pic",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=30, check=False,
            )
            for stream in json.loads(done.stdout or "{}").get("streams", []):
                # Skips cover art, which MKV and MP4 files store as a video stream.
                if not stream.get("disposition", {}).get("attached_pic"):
                    width, height = stream.get("width"), stream.get("height")
                    if width and height:
                        return width, height, resolution_label(width, height)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass  # an unreadable file still gets the filename's label
    return None, None, label_from_filename(path)


def rank(label: str | None) -> int:
    return RESOLUTIONS.index(label) if label in RESOLUTIONS else -1


def summarize(labels: list[str], kind: str) -> str | None:
    """One label for a title: a movie's best version, or a series' most common quality."""
    if not labels:
        return None
    if kind == "movie":
        return max(labels, key=rank)
    return max(Counter(labels).items(), key=lambda pair: (pair[1], rank(pair[0])))[0]
