"""Identify mounted volumes so a drive keeps its identity across mount points.

Each platform exposes one stable ID: filesystem UUID (Linux, macOS), volume serial
(Windows), or the share address for network mounts.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NETWORK_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "9p"}
# System mounts that are never where someone keeps media.
SYSTEM_TARGETS = ("/boot", "/efi", "/var", "/usr", "/snap")


@dataclass(frozen=True)
class Mount:
    volume_id: str
    path: str
    label: str
    network: bool = False


def list_mounts(platform: str | None = None) -> list[Mount]:
    platform = platform or sys.platform
    if platform.startswith("win"):
        return _windows_mounts()
    if platform == "darwin":
        return _mac_mounts()
    return _linux_mounts()


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=10, check=True).stdout


def _linux_mounts() -> list[Mount]:
    try:
        output = _run(["findmnt", "-J", "--real", "-o", "TARGET,SOURCE,UUID,FSTYPE"])
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_findmnt(output)


def parse_findmnt(output: str) -> list[Mount]:
    mounts: list[Mount] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            target = node.get("target") or ""
            source = node.get("source") or ""
            fstype = node.get("fstype") or ""
            uuid = node.get("uuid")
            is_system = target.startswith(SYSTEM_TARGETS)
            if fstype in NETWORK_FS:
                mounts.append(Mount(f"net:{source}", target, target, network=True))
            elif uuid and not is_system:
                mounts.append(Mount(f"uuid:{uuid}", target, target))
            walk(node.get("children") or [])

    walk(json.loads(output or "{}").get("filesystems", []))
    return mounts


def _mac_mounts() -> list[Mount]:
    volumes = Path("/Volumes")
    mounts: list[Mount] = []
    if not volumes.is_dir():
        return mounts
    for entry in volumes.iterdir():
        try:
            info = plistlib.loads(
                subprocess.run(
                    ["diskutil", "info", "-plist", str(entry)],
                    capture_output=True, timeout=10, check=True,
                ).stdout
            )
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
            info = {}
        uuid = info.get("VolumeUUID")
        if uuid:
            mounts.append(Mount(f"uuid:{uuid}", str(entry), entry.name))
    return mounts


def _windows_mounts() -> list[Mount]:
    import ctypes  # Windows-only API calls below

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mounts: list[Mount] = []
    bitmask = kernel32.GetLogicalDrives()
    for index in range(26):
        if not bitmask & (1 << index):
            continue
        root = f"{chr(65 + index)}:\\"
        label = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong()
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), label, 261, ctypes.byref(serial), None, None, None, 0
        )
        if ok:
            name = f"{root} {label.value}".strip()
            mounts.append(Mount(f"serial:{serial.value:08X}", root, name))
    return mounts


def _contains(mount_path: str, path: str) -> bool:
    mount = os.path.normcase(mount_path).rstrip("/\\")
    target = os.path.normcase(path)
    return target == mount or target.startswith(mount + os.sep)


def identify(path: str, mounts: list[Mount] | None = None) -> tuple[str, str]:
    """Return (volume_id, path relative to that volume) for an absolute path."""
    mounts = list_mounts() if mounts is None else mounts
    owners = [m for m in mounts if _contains(m.path, path)]
    if not owners:
        # An unidentified volume is tracked by its absolute path.
        return f"path:{path}", ""
    mount = max(owners, key=lambda m: len(m.path))
    rel = os.path.relpath(path, mount.path)
    return mount.volume_id, "" if rel == "." else Path(rel).as_posix()


def resolve(volume_id: str, rel_path: str, mounts: list[Mount] | None = None) -> str | None:
    """Current absolute path for a volume-relative path; None if not connected.

    An existing path is preferred because one volume can be mounted more than once
    (e.g. btrfs subvolumes share a UUID).
    """
    if volume_id.startswith("path:"):
        base = volume_id[len("path:"):]
        full = os.path.join(base, *rel_path.split("/")) if rel_path else base
        return full if os.path.exists(full) else None
    mounts = list_mounts() if mounts is None else mounts
    for mount in mounts:
        if mount.volume_id == volume_id:
            full = os.path.join(mount.path, *rel_path.split("/")) if rel_path else mount.path
            if os.path.exists(full):
                return full
    return None
