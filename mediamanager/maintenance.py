"""Library cleanup helpers. Read-only: finding a duplicate never changes anything."""

from __future__ import annotations

from .items import Conflict

# What makes two records "the same title".
MODES = {
    "title_year": "Same title and year",
    "title": "Same title, any year",
    "tmdb": "Same TMDB ID",
}


def find_duplicates(conn, mode: str = "title_year", kind: str = "", source: str = "") -> dict:
    """Groups of records that look like the same title, largest groups first.

    `source` narrows to groups with at least one record from that origin ("jellyfin" or
    "other"), which finds duplicates made by a scan, CSV import or TMDB save landing next
    to a Jellyfin record.
    """
    if mode not in MODES:
        raise Conflict(f"Unknown duplicate mode: {mode}")
    where = ["1 = 1"]
    params: list = []
    if kind in ("movie", "series"):
        where.append("m.kind = ?")
        params.append(kind)
    if mode == "tmdb":
        where.append("m.tmdb_id IS NOT NULL")
    rows = conn.execute(
        f"""SELECT m.id, m.kind, m.title, m.norm_title, m.year, m.tmdb_id, m.source,
                   m.on_jellyfin, m.jellyfin_id, m.poster_url, m.resolution,
                   (SELECT COUNT(*) FROM location l WHERE l.item_id = m.id) AS file_count,
                   (SELECT COALESCE(SUM(size_bytes), 0) FROM location l WHERE l.item_id = m.id)
                       AS total_bytes
            FROM media_item m WHERE {' AND '.join(where)}
            ORDER BY m.sort_title, m.id""",
        params,
    ).fetchall()

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if mode == "title_year":
            key = (row["kind"], row["norm_title"], row["year"])
        elif mode == "title":
            key = (row["kind"], row["norm_title"])
        else:
            key = (row["kind"], row["tmdb_id"])
        member = dict(row)
        member["on_jellyfin"] = bool(member["on_jellyfin"])
        member["removed_from_jellyfin"] = bool(member["jellyfin_id"]) and not member["on_jellyfin"]
        del member["jellyfin_id"], member["norm_title"]
        groups.setdefault(key, []).append(member)

    result = []
    for members in groups.values():
        if len(members) < 2:
            continue
        if source == "jellyfin" and not any(m["on_jellyfin"] for m in members):
            continue
        if source == "other" and all(m["on_jellyfin"] for m in members):
            continue
        result.append({"title": members[0]["title"], "kind": members[0]["kind"], "items": members})
    result.sort(key=lambda g: (-len(g["items"]), g["title"].casefold()))
    return {"groups": result, "total_groups": len(result),
            "total_items": sum(len(g["items"]) for g in result)}
