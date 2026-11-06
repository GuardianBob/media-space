"""Settings live in <data dir>/config.json and .env, both git-ignored."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Loopback by default. Containers set MEDIA_MANAGER_HOST=0.0.0.0 so the port can be published.
HOST = os.environ.get("MEDIA_MANAGER_HOST", "127.0.0.1")
# Extra Host headers to accept, comma separated, e.g. "nas.local:8787,192.168.1.5:8787".
EXTRA_ALLOWED_HOSTS = {
    h.strip() for h in os.environ.get("MEDIA_MANAGER_ALLOWED_HOSTS", "").split(",") if h.strip()
}
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_lock = threading.Lock()


def data_dir() -> Path:
    override = os.environ.get("MEDIA_MANAGER_DATA")
    path = Path(override) if override else PROJECT_ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""
    tmdb_api_key: str = ""
    # [{"from": "/media/movies", "to": "/mnt/nas/movies"}]
    path_mappings: list[dict[str, str]] = field(default_factory=list)


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or PROJECT_ROOT / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        values[name.strip()] = value.strip().strip("'\"")
    return values


def load_settings() -> Settings:
    """Values saved from the Settings page win; .env fills in anything left blank."""
    path = data_dir() / "config.json"
    settings = Settings()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = Settings.__dataclass_fields__
        settings = Settings(**{k: v for k, v in raw.items() if k in known})
    env = {**{k: os.environ[k] for k in ("JELLYFIN_SERVER", "JELLYFIN_API", "TMDB_API") if k in os.environ},
           **read_env_file()}
    settings.jellyfin_url = settings.jellyfin_url or env.get("JELLYFIN_SERVER", "")
    settings.jellyfin_api_key = settings.jellyfin_api_key or env.get("JELLYFIN_API", "")
    settings.tmdb_api_key = settings.tmdb_api_key or env.get("TMDB_API", "")
    return settings


def save_settings(settings: Settings) -> None:
    path = data_dir() / "config.json"
    tmp = path.with_suffix(".tmp")
    values = asdict(settings)
    # Values that came from .env stay there, so later edits to .env still take effect.
    env = {**{k: os.environ[k] for k in ("JELLYFIN_SERVER", "JELLYFIN_API", "TMDB_API") if k in os.environ},
           **read_env_file()}
    for field_name, env_name in (
        ("jellyfin_url", "JELLYFIN_SERVER"),
        ("jellyfin_api_key", "JELLYFIN_API"),
        ("tmdb_api_key", "TMDB_API"),
    ):
        if values[field_name] == env.get(env_name):
            values[field_name] = ""
    with _lock:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=2)
        os.replace(tmp, path)
