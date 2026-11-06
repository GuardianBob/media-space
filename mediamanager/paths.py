"""Translate stored locations into paths that work on this computer."""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import volumes


def _clean(path: str) -> str:
    # Jellyfin on Windows reports backslashes; comparison uses forward slashes only.
    return path.replace("\\", "/").rstrip("/")


def apply_mappings(raw_path: str, mappings: list[dict[str, str]]) -> str:
    """Replace the longest matching prefix, on whole path components only.

    A Jellyfin server path with no mapping is returned as it is.
    """
    cleaned = _clean(raw_path)
    best: dict[str, str] | None = None
    for mapping in mappings:
        prefix = _clean(mapping.get("from", ""))
        if prefix and (cleaned == prefix or cleaned.startswith(prefix + "/")):
            if best is None or len(prefix) > len(_clean(best["from"])):
                best = mapping
    if best is None:
        return raw_path
    remainder = cleaned[len(_clean(best["from"])):].lstrip("/")
    if not remainder:
        return best["to"]
    # Joined by hand: os.path.join("Z:", "x") means "x relative to Z:'s current folder".
    return best["to"].rstrip("/\\") + os.sep + os.sep.join(remainder.split("/"))


@dataclass
class Resolved:
    path: str | None
    available: bool
    reason: str | None = None


def resolve_location(
    location: dict, mappings: list[dict[str, str]], mounts: list[volumes.Mount] | None = None
) -> Resolved:
    if location["origin"] == "scan":
        path = volumes.resolve(location["volume_id"], location["rel_path"] or "", mounts)
        if path:
            return Resolved(path, True)
        return Resolved(
            location["path"], False,
            "The drive holding this file isn't connected, or the file has moved.",
        )

    local = apply_mappings(location["path"], mappings)
    if os.path.exists(local):
        return Resolved(local, True)
    if local == location["path"]:
        return Resolved(
            local, False,
            "This is Jellyfin's own path. Add a path mapping in Settings to reach it from here.",
        )
    return Resolved(local, False, "Mapped path not found. Check the share is mounted.")
