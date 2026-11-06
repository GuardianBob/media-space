import json
import os
import subprocess

import pytest

from mediamanager import hotlink, paths, volumes


# ---- Path mapping

def test_mapping_uses_longest_prefix():
    maps = [{"from": "/media", "to": "/mnt/a"}, {"from": "/media/4K Movies", "to": "/mnt/b"}]
    assert paths.apply_mappings("/media/4K Movies/x.mkv", maps) == "/mnt/b/x.mkv"
    assert paths.apply_mappings("/media/Movies/x.mkv", maps) == "/mnt/a/Movies/x.mkv"


def test_mapping_matches_whole_path_components_only():
    maps = [{"from": "/media/Movies", "to": "/mnt/a"}]
    assert paths.apply_mappings("/media/Movies2/x.mkv", maps) == "/media/Movies2/x.mkv"


def test_mapping_handles_windows_server_paths():
    maps = [{"from": "D:\\Media", "to": "/mnt/nas"}]
    assert paths.apply_mappings("D:\\Media\\Movies\\x.mkv", maps) == "/mnt/nas/Movies/x.mkv"


def test_mapping_to_a_windows_drive_root_stays_absolute(monkeypatch):
    monkeypatch.setattr(paths.os, "sep", "\\")
    maps = [{"from": "/media", "to": "Z:\\"}]
    assert paths.apply_mappings("/media/Movies/x.mkv", maps) == "Z:\\Movies\\x.mkv"
    assert paths.apply_mappings("/media", maps) == "Z:\\"


def test_unmapped_jellyfin_path_explains_itself():
    result = paths.resolve_location(
        {"origin": "jellyfin", "path": "/nonexistent/x.mkv"}, [], []
    )
    assert not result.available
    assert "path mapping" in result.reason


# ---- Volumes

FINDMNT = json.dumps({"filesystems": [{
    "target": "/", "source": "/dev/a", "uuid": "root-uuid", "fstype": "btrfs",
    "children": [
        {"target": "/boot", "source": "/dev/b", "uuid": "boot-uuid", "fstype": "ext4"},
        {"target": "/run/media/me/Drive", "source": "/dev/c", "uuid": "drive-uuid", "fstype": "exfat"},
        {"target": "/mnt/nas", "source": "//nas/media", "uuid": None, "fstype": "cifs"},
        {"target": "/run/user/1000/kio", "source": "kio-fuse", "uuid": None, "fstype": "fuse.kio-fuse"},
    ],
}]})


def test_findmnt_keeps_real_data_volumes():
    ids = {m.volume_id for m in volumes.parse_findmnt(FINDMNT)}
    assert ids == {"uuid:root-uuid", "uuid:drive-uuid", "net://nas/media"}


def test_identify_picks_innermost_mount():
    mounts = volumes.parse_findmnt(FINDMNT)
    assert volumes.identify("/run/media/me/Drive/Movies/x.mkv", mounts) == (
        "uuid:drive-uuid", "Movies/x.mkv",
    )
    assert volumes.identify("/home/me/x.mkv", mounts) == ("uuid:root-uuid", "home/me/x.mkv")


def test_drive_keeps_identity_when_mount_point_changes(tmp_path):
    (tmp_path / "second" / "Movies").mkdir(parents=True)
    (tmp_path / "second" / "Movies" / "x.mkv").touch()
    first = [volumes.Mount("uuid:d", str(tmp_path / "first"), "d")]
    second = [volumes.Mount("uuid:d", str(tmp_path / "second"), "d")]
    assert volumes.resolve("uuid:d", "Movies/x.mkv", first) is None
    assert volumes.resolve("uuid:d", "Movies/x.mkv", second) == str(
        tmp_path / "second" / "Movies" / "x.mkv"
    )


def test_real_linux_mounts_identify_a_temp_folder(tmp_path):
    volume_id, rel = volumes.identify(str(tmp_path))
    assert volumes.resolve(volume_id, rel) == str(tmp_path)


# ---- Hotlink commands per OS

def test_windows_select_quotes_only_the_path():
    command = hotlink.windows_command("C:\\My Movies\\a, b.mkv", is_dir=False)
    assert command == 'explorer /select,"C:\\My Movies\\a, b.mkv"'


def test_windows_folder_and_unc_paths():
    assert hotlink.windows_command("D:\\TV Shows", True) == 'explorer "D:\\TV Shows"'
    assert hotlink.windows_command("\\\\nas\\media\\x.mkv", False) == (
        'explorer /select,"\\\\nas\\media\\x.mkv"'
    )


def test_mac_reveals_files_and_opens_folders():
    assert hotlink.mac_command("/Volumes/D/x y.mkv", False) == ["open", "-R", "/Volumes/D/x y.mkv"]
    assert hotlink.mac_command("/Volumes/D", True) == ["open", "/Volumes/D"]


def test_linux_uri_escapes_commas_and_spaces():
    command = hotlink.linux_reveal_command("/m/a, b#1.mkv")
    assert "array:string:file:///m/a%2C%20b%231.mkv" in command


@pytest.fixture
def record_processes(monkeypatch):
    calls = {"run": [], "spawn": []}

    def fake_run(command, **_):
        calls["run"].append(command)
        return subprocess.CompletedProcess(command, calls.get("run_code", 0))

    monkeypatch.setattr(hotlink.subprocess, "run", fake_run)
    monkeypatch.setattr(hotlink, "_spawn", calls["spawn"].append)
    return calls


def test_linux_file_is_revealed_never_handed_to_xdg_open(tmp_path, record_processes):
    media = tmp_path / "x.mkv"
    media.touch()
    hotlink.open_in_file_manager(str(media), "linux")
    assert record_processes["run"][0][0] == "dbus-send"
    assert record_processes["spawn"] == []


def test_linux_fallback_opens_parent_folder_not_the_file(tmp_path, record_processes):
    record_processes["run_code"] = 1
    media = tmp_path / "x.mkv"
    media.touch()
    hotlink.open_in_file_manager(str(media), "linux")
    assert record_processes["spawn"] == [["xdg-open", str(tmp_path)]]


def test_each_platform_dispatches_its_opener(tmp_path, record_processes):
    media = tmp_path / "x.mkv"
    media.touch()
    hotlink.open_in_file_manager(str(media), "darwin")
    assert record_processes["spawn"][-1] == ["open", "-R", str(media)]
    hotlink.open_in_file_manager(str(media), "win32")
    assert record_processes["spawn"][-1].startswith("explorer /select,")


def test_hotlink_rejects_missing_and_relative_paths(tmp_path):
    with pytest.raises(hotlink.HotlinkError):
        hotlink.open_in_file_manager(str(tmp_path / "missing.mkv"))
    with pytest.raises(hotlink.HotlinkError):
        hotlink.open_in_file_manager(os.path.join("relative", "x.mkv"))
