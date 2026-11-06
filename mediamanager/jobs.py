"""Run long tasks (Jellyfin sync, drive scan) one at a time on a background thread."""

from __future__ import annotations

import threading
from typing import Callable

from . import db


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = {"running": False, "name": None, "message": "", "result": None, "error": None}

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def start(self, name: str, work: Callable) -> bool:
        """`work(conn, progress)` runs in the background; False if a job is already running."""
        with self._lock:
            if self._state["running"]:
                return False
            self._state = {"running": True, "name": name, "message": "Starting", "result": None, "error": None}
        threading.Thread(target=self._run, args=(work,), daemon=True).start()
        return True

    def _progress(self, message: str) -> None:
        with self._lock:
            self._state["message"] = message

    def _run(self, work: Callable) -> None:
        result, error = None, None
        conn = db.connect()
        try:
            result = work(conn, self._progress)
        except Exception as exc:  # reported in the UI; the job thread must not die silently
            error = str(exc) or type(exc).__name__
        finally:
            conn.close()
        with self._lock:
            self._state.update(running=False, message="", result=result, error=error)
