"""Show a file or folder in the operating system's file manager.

Nothing here may launch a media file. Files are only ever "revealed"; the generic
opener (xdg-open) is only given directories, since it would play a file.
"""

from __future__ import annotations

import ntpath
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


class HotlinkError(Exception):
    pass


def windows_command(path: str, is_dir: bool) -> str:
    # Passed as one string because explorer needs the literal form /select,"C:\a b\c";
    # list-style quoting would wrap the /select switch too, and Explorer then opens the
    # default folder. Windows paths cannot contain double quotes.
    native = ntpath.normpath(path)
    return f'explorer "{native}"' if is_dir else f'explorer /select,"{native}"'


def mac_command(path: str, is_dir: bool) -> list[str]:
    return ["open", path] if is_dir else ["open", "-R", path]


def linux_reveal_command(path: str) -> list[str]:
    # The freedesktop FileManager1 interface selects the file in Dolphin, Nautilus,
    # Nemo and others. dbus-send splits arrays on commas, so they must stay encoded.
    uri = "file://" + quote(path, safe="/")
    return [
        "dbus-send", "--session", "--print-reply",
        "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
        "org.freedesktop.FileManager1.ShowItems", f"array:string:{uri}", "string:",
    ]


def _spawn(command: list[str] | str) -> None:
    # Detached, so closing the app leaves the file manager open.
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)  # noqa: S603 - argument list / fixed explorer form


def _open_linux(path: str, is_dir: bool) -> None:
    if not is_dir:
        try:
            done = subprocess.run(
                linux_reveal_command(path), capture_output=True, timeout=5, check=False
            )
            if done.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass  # no D-Bus file manager; open the containing folder instead
    _spawn(["xdg-open", path if is_dir else str(Path(path).parent)])


def open_in_file_manager(path: str, platform: str | None = None) -> None:
    """Reveal `path`, an absolute path that must exist."""
    platform = platform or sys.platform
    if not os.path.isabs(path):
        raise HotlinkError(f"Not an absolute path: {path}")
    if not os.path.exists(path):
        raise HotlinkError(f"Path not found: {path}")
    is_dir = os.path.isdir(path)
    try:
        if platform.startswith("win"):
            # Explorer's exit code is unreliable, so it isn't checked.
            _spawn(windows_command(path, is_dir))
        elif platform == "darwin":
            _spawn(mac_command(path, is_dir))
        else:
            _open_linux(path, is_dir)
    except OSError as exc:
        raise HotlinkError(f"Couldn't start the file manager: {exc}") from exc
