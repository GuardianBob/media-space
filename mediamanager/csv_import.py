"""CSV bulk updates (Title, Year, Country): preview first, then apply chosen rows."""

from __future__ import annotations

import csv
import io
import json

from . import items
from .db import norm_title

MAX_BYTES = 10 * 1024 * 1024
ALIASES = {"name": "title", "release year": "year", "type": "kind"}


class CsvError(Exception):
    pass


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")  # Excel writes a BOM
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_rows(data: bytes) -> list[dict]:
    if len(data) > MAX_BYTES:
        raise CsvError("That CSV is larger than the 10 MB limit.")
    text = _decode(data)
    if not text.strip():
        raise CsvError("That CSV is empty.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = {
        name: ALIASES.get(name.strip().casefold(), name.strip().casefold())
        for name in reader.fieldnames or []
    }
    if "title" not in headers.values():
        raise CsvError("The CSV needs a Title column.")

    rows = []
    for line, raw in enumerate(reader, start=2):
        row = {headers[k]: (v or "").strip() for k, v in raw.items() if k in headers}
        year_text = row.get("year", "")
        kind_text = row.get("kind", "").casefold()
        rows.append({
            "line": line,
            "title": row.get("title", ""),
            "year": int(year_text) if year_text.isdigit() else None,
            "year_invalid": bool(year_text) and not (year_text.isdigit() and len(year_text) == 4),
            "country": row.get("country") or None,
            "kind": "series" if kind_text in ("series", "tv", "show") else "movie",
        })
    return rows


def _changes(row: dict, item) -> dict:
    """Blank CSV cells leave a value alone; they never clear it."""
    changes = {}
    if row["title"] != item["title"]:
        changes["title"] = row["title"]
    if row["year"] is not None and row["year"] != item["year"]:
        changes["year"] = row["year"]
    if row["country"] and row["country"] != item["country"]:
        changes["country"] = row["country"]
    return changes


def preview(conn, data: bytes) -> dict:
    rows = parse_rows(data)
    by_title: dict[str, list] = {}
    for item in conn.execute("SELECT id, kind, title, norm_title, year, country FROM media_item"):
        by_title.setdefault(item["norm_title"], []).append(item)

    out = []
    for row in rows:
        entry = {**row, "status": "", "item_id": None, "changes": {}, "candidates": []}
        if not row["title"]:
            entry.update(status="invalid", message="No title")
        elif row["year_invalid"]:
            entry.update(status="invalid", message="Year isn't a four-digit number")
        else:
            same_title = by_title.get(norm_title(row["title"]), [])
            exact = [i for i in same_title if row["year"] is not None and i["year"] == row["year"]]
            matches = exact or (same_title if row["year"] is None else [])
            if len(matches) == 1:
                changes = _changes(row, matches[0])
                entry.update(
                    status="update" if changes else "unchanged",
                    item_id=matches[0]["id"], changes=changes,
                )
            elif matches or same_title:
                # Several records fit, or the same title has another year: your call.
                entry["status"] = "ambiguous"
                entry["candidates"] = [
                    {
                        "id": i["id"], "kind": i["kind"], "title": i["title"], "year": i["year"],
                        "changes": _changes(row, i),
                    }
                    for i in (matches or same_title)
                ]
            else:
                entry["status"] = "unmatched"
        out.append(entry)

    summary: dict[str, int] = {}
    for entry in out:
        summary[entry["status"]] = summary.get(entry["status"], 0) + 1
    return {"rows": out, "summary": summary}


def apply(conn, decisions: list[dict]) -> dict:
    """Decisions: {action: 'update', item_id, title, year, country} or {action: 'create', ...}.

    Updates are recalculated against the current record, and go through the normal edit
    path, so they lock those fields against Jellyfin sync.
    """
    result = {"updated": 0, "created": 0}
    with conn:
        for decision in decisions:
            title = (decision.get("title") or "").strip()
            if not title:
                raise CsvError("A row has no title.")
            if decision["action"] == "update":
                item = items.get_row(conn, decision["item_id"])
                changes = _changes(
                    {"title": title, "year": decision.get("year"), "country": decision.get("country")},
                    item,
                )
                if changes:
                    items.update_fields(conn, item["id"], changes)
                    result["updated"] += 1
            elif decision["action"] == "create":
                items.create_manual(
                    conn, decision.get("kind") or "movie", title, decision.get("year"),
                    decision.get("country"), "csv",
                )
                result["created"] += 1
            else:
                raise CsvError(f"Unknown action: {json.dumps(decision['action'])}")
    return result
