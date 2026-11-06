# Media Manager

A local web app for your media: sync from Jellyfin, discover titles on TMDB, scan external
drives, bulk-update from CSV, and jump straight to any file in your file manager.

Reference for the Jellyfin connection, discovery flow and browser layout:
[seerr-team/seerr](https://github.com/seerr-team/seerr) (no code copied).

## Setup

Needs Python 3.11+, and Node 20+ to build the frontend. `ffprobe` (part of FFmpeg) is optional, for measuring scanned files. Works on Linux, Windows and macOS.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'     # Windows: .venv\Scripts\pip
cd frontend && npm install && npm run build && cd ..
```

Create `.env` in the project folder (git-ignored), or enter the same values in Settings:

```sh
JELLYFIN_SERVER=http://your-server:8096
JELLYFIN_API=your-jellyfin-api-key
TMDB_API=your-tmdb-key
```

## Run

```sh
.venv/bin/python -m mediamanager      # Windows: .venv\Scripts\python
```

Open <http://127.0.0.1:8787>. Use `--port` to change the port. The app only listens on this
computer, because Open folder acts on the machine running the backend.

## Connecting Jellyfin's files

Jellyfin reports paths as its server sees them, such as `/media/Movies/x.mkv`. To open those
files from here:

1. Mount the share on this computer.
2. In **Settings → Path mappings**, add `/media` → the mount point, e.g. `/mnt/nas/media` or `Z:\`.

## How it behaves

| Area | Behaviour |
| --- | --- |
| **Jellyfin sync** | One-way and started by you; nothing is written to Jellyfin. A failed sync changes nothing. Titles gone from Jellyfin are flagged, not removed. |
| **Media browser** | Sort by title, release date, date added, rating or runtime, in either direction. Sort or filter by resolution too. Filter by first letter, type, availability, genre and year. As in Jellyfin, leading articles are ignored ("The Matrix" is under M) and numbers sort numerically. |
| **Resolution** | Shown on cards, title pages and each file, e.g. `1080p · 1920×800`. Cropped widescreen counts by width, so 1920×800 is 1080p. A movie shows its best version; a series shows its most common quality and the full mix. Scanned files are measured with `ffprobe` if installed, otherwise labelled from the filename. |
| **Missing data** | Each sync fills blank fields on new titles from TMDB, and Settings can recheck everything. Only blanks are written. A title with no TMDB ID is linked only on an exact, unique title and year match. |
| **What to Watch** | Set any of type, availability, year range, genres, resolutions, minimum rating and maximum runtime, then get a random 10, 25 or 50 titles. Several genres or resolutions mean "any of these". A title with no value for a range or minimum, such as no rating, is left out. |
| **Your edits** | Fields changed by hand or by CSV are kept through syncs. The title page has a button to return a field to Jellyfin's value. |
| **Discovery** | Saving a TMDB title stores its metadata, marked **Not on Jellyfin**. If it later appears on Jellyfin, the records merge. Nothing is downloaded. |
| **Drive scan** | Read-only, started by you. New files wait in a review queue. Drives are tracked by volume ID, so a new mount point or drive letter doesn't break their paths. |
| **CSV import** | Matches on Title + Year and previews every change. You choose whether unmatched rows become new records. Blank cells never erase a value. |
| **Open folder** | Reveals the file in Dolphin, Nautilus and others (D-Bus, then `xdg-open` on the parent folder), Explorer or Finder. It never plays the file. |
| **Delete** | Removes only the database record, **never a media file**. You review what goes and type the title to confirm. Deleted Jellyfin titles go to **Excluded**, where they can be restored. Deletes apply to whole titles, not single episodes. |

## Data

`data/media.db` (SQLite) and `data/config.json` hold everything and are git-ignored. Set
`MEDIA_MANAGER_DATA` to keep them elsewhere.

## Development

```sh
.venv/bin/python -m pytest               # backend tests
.venv/bin/python -m mediamanager         # backend, then in another terminal:
cd frontend && npm run dev               # frontend with hot reload
```
