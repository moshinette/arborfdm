"""« Open in OrcaSlicer »: user settings (arbor/core/settings.py), detection, export to the
dedicated folder and launch (arbor/io/slicer_launch.py). Launches use fake executables
that write their arguments to a file; nothing here needs OrcaSlicer."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

from arbor.core import settings as st
from arbor.core.mesh_io import read_3mf
from arbor.io import slicer_launch as sl
from arbor.io.export import export_3mf, validate_export

POSIX = not sys.platform.startswith("win")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(st.ENV_DIR, str(tmp_path / "cfg"))
    monkeypatch.setenv(sl.ENV_OUT_DIR, str(tmp_path / "to_slicer"))


def _fake_exe(path: Path, body: str = "", record: Path | None = None, code: int = 0) -> Path:
    """Executable Python script; writes its argv[1:] as JSON to ``record``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"#!{sys.executable}", "import json, sys"]
    if record is not None:
        lines.append(f"open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))")
    if body:
        lines.append(body)
    lines.append(f"sys.exit({code})")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _wait_for(p: Path, timeout: float = 10.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if p.exists() and p.stat().st_size:
            return
        time.sleep(0.05)
    raise AssertionError(f"{p} not written within {timeout} s")


def _scene():
    model = trimesh.creation.box((10, 10, 8))
    model.apply_translation((0, 0, 4 + 2))                 # z 2..10, rests on supports
    col = trimesh.creation.box((2, 2, 2.2))
    col.apply_translation((0, 0, 1.1))                     # z 0..2.2, touches the model
    raft = trimesh.creation.box((14, 14, 0.6))
    raft.apply_translation((0, 0, 0.3))
    sup = trimesh.util.concatenate([col])
    # the model must reach the plate for 2 objects: add a foot under it
    foot = trimesh.creation.box((1, 1, 2.0))
    foot.apply_translation((4, 4, 1.0))
    model = trimesh.util.concatenate([model, foot])
    return model, sup, None


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def test_settings_defaults_when_missing():
    s = st.load_settings()
    assert s == {"orca_path": None}
    assert not st.settings_path().exists()


def test_settings_roundtrip_atomic(tmp_path):
    p = st.save_settings({"orca_path": "C:/Program Files/OrcaSlicer/orca-slicer.exe", "future": [1]})
    assert p == tmp_path / "cfg" / "settings.json"
    s = st.load_settings()
    assert s["orca_path"] == "C:/Program Files/OrcaSlicer/orca-slicer.exe"
    assert s["future"] == [1]                                   # unknown keys kept
    assert json.loads(p.read_text())["format"] == "arborfdm-settings"
    assert [f.name for f in p.parent.iterdir()] == ["settings.json"]   # no temp file left
    st.update_settings(orca_path=None)
    assert st.load_settings()["orca_path"] is None and st.load_settings()["future"] == [1]


@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]", b"\xff\xfe\x00garbage", b""])
def test_settings_corrupted_gives_defaults_and_warning(content):
    p = st.settings_path()
    p.parent.mkdir(parents=True)
    p.write_bytes(content)
    with pytest.warns(st.SettingsWarning):
        s = st.load_settings()
    assert s == {"orca_path": None}
    assert p.read_bytes() == content                            # left untouched


def test_settings_invalid_orca_path_dropped():
    p = st.settings_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"orca_path": 42, "x": 1}))
    with pytest.warns(st.SettingsWarning, match="orca_path"):
        s = st.load_settings()
    assert s["orca_path"] is None and s["x"] == 1


def test_settings_save_errors_are_readable(tmp_path, monkeypatch):
    blocker = tmp_path / "file_not_dir"
    blocker.write_text("x")
    monkeypatch.setenv(st.ENV_DIR, str(blocker / "sub"))
    with pytest.raises(st.SettingsError, match="cannot write"):
        st.save_settings({"orca_path": None})
    with pytest.raises(st.SettingsError):
        st.save_settings({"orca_path": object()})


def test_settings_default_dirs(monkeypatch):
    monkeypatch.delenv(st.ENV_DIR)
    monkeypatch.setattr(st.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "/x/Roaming")
    assert st.user_config_dir() == Path("/x/Roaming") / "ArborFDM"
    monkeypatch.setattr(st.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/x/cfg")
    assert st.settings_path() == Path("/x/cfg/arborfdm/settings.json")


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not POSIX, reason="POSIX executable bits")
def test_check_orca_path_reasons(tmp_path):
    assert "does not exist" in sl.check_orca_path(tmp_path / "nope")
    assert "folder" in sl.check_orca_path(tmp_path)
    f = tmp_path / "OrcaSlicer.AppImage"
    f.write_text("x")
    assert "chmod +x" in sl.check_orca_path(f)
    f.chmod(0o755)
    assert sl.check_orca_path(f) is None
    assert sl.check_orca_path(f, platform="win32").endswith("is not a .exe program")
    exe = tmp_path / "orca-slicer.exe"
    exe.write_text("x")
    assert sl.check_orca_path(exe, platform="win32") is None
    app = tmp_path / "OrcaSlicer.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    assert sl.check_orca_path(app, platform="darwin") is None
    assert sl.check_orca_path("", platform="darwin") == "no path given"


@pytest.mark.skipif(not POSIX, reason="POSIX executable bits")
def test_find_on_path_and_configured_priority(tmp_path):
    bindir = tmp_path / "bin"
    on_path = _fake_exe(bindir / "orca-slicer")
    env = {"PATH": str(bindir), "HOME": str(tmp_path / "home")}
    assert sl.find_orcaslicer(None, platform="linux", env=env) == on_path
    assert sl.candidate_paths("linux", env)[0] == on_path
    configured = _fake_exe(tmp_path / "custom" / "my-orca")
    assert sl.find_orcaslicer(str(configured), platform="linux", env=env) == configured
    # an invalid configured path is ignored: automatic detection takes over
    assert sl.find_orcaslicer(str(tmp_path / "gone"), platform="linux", env=env) == on_path
    assert sl.find_orcaslicer(str(tmp_path / "gone"), platform="linux",
                              env={"PATH": "", "HOME": str(tmp_path / "home")}) is None


@pytest.mark.skipif(not POSIX, reason="POSIX executable bits")
def test_find_linux_appimage_and_flatpak(tmp_path):
    home = tmp_path / "home"
    env = {"PATH": "", "HOME": str(home), "XDG_DATA_HOME": str(home / ".local" / "share")}
    cands = sl.candidate_paths("linux", env)
    assert home / ".local/share/flatpak/exports/bin/com.orcaslicer.OrcaSlicer" in cands
    assert home / "Downloads" / "*Orca*.AppImage" in cands      # pattern shown when nothing found
    img = home / "Downloads" / "OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage"
    img.parent.mkdir(parents=True)
    img.write_text("x")                                         # not executable yet
    assert sl.find_orcaslicer(None, platform="linux", env=env) is None
    img.chmod(0o755)
    assert sl.find_orcaslicer(None, platform="linux", env=env) == img
    fp = _fake_exe(home / ".local/share/flatpak/exports/bin/com.orcaslicer.OrcaSlicer")
    assert sl.find_orcaslicer(None, platform="linux", env=env) == fp   # flatpak before AppImage


def test_find_windows_locations(tmp_path):
    pf, local, home = tmp_path / "PF", tmp_path / "Local", tmp_path / "User"
    env = {"PATH": "", "ProgramFiles": str(pf), "LOCALAPPDATA": str(local),
           "USERPROFILE": str(home)}
    cands = sl.candidate_paths("win32", env)
    assert pf / "OrcaSlicer" / "orca-slicer.exe" in cands
    assert home / "Documents" / "OrcaSlicer" / "orca-slicer.exe" in cands   # NSIS "Just me"
    assert sl.find_orcaslicer(None, platform="win32", env=env) is None
    exe = local / "Programs" / "OrcaSlicer" / "orca-slicer.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert sl.find_orcaslicer(None, platform="win32", env=env) == exe
    exe2 = pf / "OrcaSlicer" / "orca-slicer.exe"
    exe2.parent.mkdir(parents=True)
    exe2.write_bytes(b"MZ")
    assert sl.find_orcaslicer(None, platform="win32", env=env) == exe2      # Program Files first


def test_mac_candidates_and_open_command(tmp_path):
    env = {"PATH": "", "HOME": str(tmp_path)}
    cands = sl.candidate_paths("darwin", env)
    assert Path("/Applications/OrcaSlicer.app") in cands and tmp_path / "Applications/OrcaSlicer.app" in cands
    app = Path("/Applications/OrcaSlicer.app")
    f = tmp_path / "a b.3mf"
    assert sl.launch_command(f, app, "darwin") == ["open", "-a", str(app), str(f)]
    assert sl.launch_command(f, app / "Contents/MacOS/OrcaSlicer", "darwin")[:3] == ["open", "-a", str(app)]
    assert sl.launch_command(f, Path("/opt/orca"), "linux") == [str(Path("/opt/orca")), str(f)]


def test_to_slicer_dir_defaults(monkeypatch):
    assert sl.to_slicer_dir("win32", {"LOCALAPPDATA": "/L"}) == Path("/L/ArborFDM/to_slicer")
    assert sl.to_slicer_dir("linux", {"HOME": "/h"}) == Path("/h/.local/share/arborfdm/to_slicer")
    assert sl.to_slicer_dir("darwin", {"HOME": "/h"}) == Path(
        "/h/Library/Application Support/ArborFDM/to_slicer")
    assert sl.to_slicer_dir("linux", {sl.ENV_OUT_DIR: "/o", "HOME": "/h"}) == Path("/o")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def test_export_for_slicer_dedicated_folder_and_source_untouched(tmp_path):
    model, sup, raft = _scene()
    src_dir = tmp_path / "models"
    src_dir.mkdir()
    src = src_dir / "figurine.stl"
    model.export(src)
    before = (src.read_bytes(), src.stat().st_mtime_ns, sorted(os.listdir(src_dir)))
    v0, s0 = model.vertices.copy(), sup.vertices.copy()
    notes: list = []
    out = sl.export_for_slicer(model, sup, raft, {"profile": "x"}, "figurine", notes=notes)
    assert out.parent == tmp_path / "to_slicer"
    assert out.name.startswith("figurine_") and out.suffix == ".3mf"
    assert (src.read_bytes(), src.stat().st_mtime_ns, sorted(os.listdir(src_dir))) == before
    assert np.array_equal(model.vertices, v0) and np.array_equal(sup.vertices, s0)
    assert validate_export(out) == []
    with zipfile.ZipFile(out) as z:
        cfg = z.read("Metadata/model_settings.config").decode()
    assert cfg.count('key="enable_support" value="0"') == 2
    assert any("enable_support = 0" in n for n in notes)
    parts = {p.name: p.mesh for p in read_3mf(out).parts}
    assert set(parts) == {"figurine", "figurine supports"}
    # positions and scale kept: every object moved by the same vector, sizes unchanged
    d_model = parts["figurine"].bounds - model.bounds
    d_sup = parts["figurine supports"].bounds - sup.bounds
    assert np.allclose(d_model, d_model[0]) and np.allclose(d_model, d_sup, atol=1e-6)


def test_export_for_slicer_unique_names_and_options(tmp_path):
    model, sup, _ = _scene()
    d = tmp_path / "custom"
    a = sl.export_for_slicer(model, sup, None, None, "fig<>:|?*", d)
    b = sl.export_for_slicer(model, sup, None, None, "fig<>:|?*", d, disable_slicer_supports=False,
                             layer_heights={"model": 0.06, "supports": 0.12})
    assert a != b and a.exists() and b.exists() and a.parent == d
    assert not set('<>:|?*') & set(a.name)
    with zipfile.ZipFile(b) as z:
        cfg = z.read("Metadata/model_settings.config").decode()
    assert "enable_support" not in cfg and 'value="0.06"' in cfg
    with pytest.raises(ValueError):
        sl.export_for_slicer(trimesh.Trimesh(), None, None, None, "empty", d)
    assert sorted(p.name for p in d.iterdir()) == sorted([a.name, b.name])   # failed export removed


def test_cleanup_keeps_recent_and_foreign_files(tmp_path):
    model, sup, _ = _scene()
    d = tmp_path / "to_slicer"
    d.mkdir()
    old = []
    for i in range(5):
        p = d / f"old_{i}.3mf"
        export_3mf(p, model, sup, None, None, model_name="old", layout="separate")
        os.utime(p, (1000 + i, 1000 + i))
        old.append(p)
    foreign = d / "user_project.3mf"                     # e.g. re-saved by OrcaSlicer
    with zipfile.ZipFile(foreign, "w") as z:
        z.writestr("3D/3dmodel.model", '<model><metadata name="Application">OrcaSlicer-2.4.2</metadata></model>')
    os.utime(foreign, (1, 1))
    other = d / "notes.txt"
    other.write_text("x")
    new = sl.export_for_slicer(model, sup, None, None, "new", keep=3)
    left = sorted(p.name for p in d.iterdir())
    assert new.name in left and "old_4.3mf" in left and "old_3.3mf" in left
    assert "old_0.3mf" not in left and "old_1.3mf" not in left and "old_2.3mf" not in left
    assert "user_project.3mf" in left and "notes.txt" in left


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not POSIX, reason="fake executable is a POSIX script")
def test_launch_passes_the_file_path(tmp_path):
    rec = tmp_path / "args.json"
    exe = _fake_exe(tmp_path / "Orca Slicer dir" / "orca-slicer", record=rec)
    model, sup, _ = _scene()
    f = sl.export_for_slicer(model, sup, None, None, "fig ure")
    t0 = time.time()
    sl.launch_orcaslicer(f, exe, check_seconds=0)
    assert time.time() - t0 < 2.0                        # does not wait for the slicer
    _wait_for(rec)
    assert json.loads(rec.read_text()) == [str(f.resolve())]


@pytest.mark.skipif(not POSIX, reason="fake executable is a POSIX script")
def test_launch_non_blocking_with_long_running_slicer(tmp_path):
    rec = tmp_path / "args.json"
    exe = _fake_exe(tmp_path / "orca", body="import time; time.sleep(5)", record=rec)
    f = tmp_path / "x.3mf"
    f.write_bytes(b"PK")
    t0 = time.time()
    sl.launch_orcaslicer(f, exe, check_seconds=0.3)
    assert time.time() - t0 < 2.0
    _wait_for(rec)


@pytest.mark.skipif(not POSIX, reason="fake executable is a POSIX script")
def test_launch_errors(tmp_path):
    f = tmp_path / "x.3mf"
    f.write_bytes(b"PK")
    with pytest.raises(sl.SlicerNotFound, match="does not exist"):
        sl.launch_orcaslicer(f, tmp_path / "missing-orca")
    exe = _fake_exe(tmp_path / "orca")
    with pytest.raises(sl.SlicerLaunchError, match="does not exist"):
        sl.launch_orcaslicer(tmp_path / "missing.3mf", exe)
    bad = _fake_exe(tmp_path / "bad-orca", code=3)
    with pytest.raises(sl.SlicerLaunchError, match="code 3"):
        sl.launch_orcaslicer(f, bad, check_seconds=5)
    noexec = tmp_path / "noexec"
    noexec.write_text("x")
    with pytest.raises(sl.SlicerNotFound, match="not executable"):
        sl.launch_orcaslicer(f, noexec)
    broken = tmp_path / "broken"
    broken.write_bytes(b"\x00\x01binary")
    broken.chmod(0o755)
    with pytest.raises(sl.SlicerLaunchError, match="cannot start"):
        sl.launch_orcaslicer(f, broken)


def test_child_env_restores_frozen_library_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEI123", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    monkeypatch.setenv("PATH", os.pathsep.join(["/tmp/_MEI123", "/usr/bin"]))
    monkeypatch.setenv("QT_PLUGIN_PATH", "/tmp/_MEI123/PySide6/plugins")
    env = sl._child_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib" and "LD_LIBRARY_PATH_ORIG" not in env
    assert env["PATH"] == "/usr/bin" and "QT_PLUGIN_PATH" not in env
