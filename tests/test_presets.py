"""User presets (arbor/core/presets.py): storage, validation, import / export. No Qt."""
from __future__ import annotations

import json
import os
import sys

import pytest

from arbor.core import presets as pr
from arbor.core.presets import PresetError, PresetStore
from arbor.core.project import Project
from arbor.engine.params import PROFILE_DIR, load_profiles


@pytest.fixture(scope="module")
def builtin():
    return load_profiles()


@pytest.fixture
def store(builtin, tmp_path):
    return PresetStore(builtin, tmp_path / "presets")


def _snapshot(directory):
    return {p.name: p.read_bytes() for p in sorted(directory.glob("*.json"))}


def test_user_dir_env_override_and_platform_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv(pr.ENV_DIR, str(tmp_path / "x"))
    assert pr.user_preset_dir() == tmp_path / "x"
    monkeypatch.delenv(pr.ENV_DIR)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert pr.user_preset_dir() == tmp_path / "Roaming" / "ArborFDM" / "presets"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert pr.user_preset_dir() == tmp_path / "cfg" / "arborfdm" / "presets"


def test_missing_folder_is_empty_not_an_error(store):
    assert store.presets == {} and store.errors == []
    assert set(store.profiles()) == set(store.builtin)


def test_save_stores_inheritance_and_only_changed_values(store, builtin):
    base = builtin["nozzle_04_normal"].params
    params = base.updated(overhang_angle_deg=48.0, raft_enabled=True)
    p = store.save("Mini 28mm", params, "nozzle_04_normal")
    data = json.loads(p.path.read_text(encoding="utf-8"))
    assert data["format"] == "arborfdm-preset"
    assert data["inherits"] == "nozzle_04_normal"
    assert data["values"] == {"overhang_angle_deg": 48.0, "raft_enabled": True}
    assert p.key == "user:mini_28mm" and p.params == params
    # reload from disk gives the same parameters
    again = PresetStore(builtin, store.directory)
    assert again.presets[p.key].params == params
    assert again.presets[p.key].label == "Mini 28mm"


def test_saving_from_a_user_preset_rebases_on_its_builtin(store, builtin):
    base = builtin["nozzle_02_light"].params
    a = store.save("A", base.updated(contact_density=1.2), "nozzle_02_light")
    b = store.save("B", a.params.updated(z_gap=0.05), a.key)
    assert b.inherits == "nozzle_02_light"
    assert b.values == {"contact_density": 1.2, "z_gap": 0.05}


def test_duplicate_name_needs_overwrite(store, builtin):
    params = builtin["nozzle_04_normal"].params
    store.save("Same", params, "nozzle_04_normal")
    with pytest.raises(PresetError, match="already exists"):
        store.save("  same ", params.updated(walls=4), "nozzle_04_normal")
    p = store.save("same", params.updated(walls=4), "nozzle_04_normal", overwrite=True)
    assert len(store.presets) == 1 and p.values == {"walls": 4}


def test_save_rejects_invalid_parameters_and_bad_names(store, builtin):
    bad = builtin["nozzle_04_normal"].params.updated(overhang_angle_deg=95.0)
    with pytest.raises(PresetError, match="overhang_angle_deg"):
        store.save("Bad", bad, "nozzle_04_normal")
    for name in ("", "   ", "x" * 200):
        with pytest.raises(PresetError):
            store.save(name, builtin["nozzle_04_normal"].params, "nozzle_04_normal")
    assert not store.directory.exists() or not list(store.directory.glob("*.json"))


def test_rename_and_delete(store, builtin):
    p = store.save("Old name", builtin["nozzle_04_dense"].params, "nozzle_04_dense")
    new = store.rename(p.key, "New name")
    assert new.key == "user:new_name" and p.key not in store.presets
    assert not p.path.exists() and new.path.exists()
    store.save("Other", builtin["nozzle_04_dense"].params, "nozzle_04_dense")
    with pytest.raises(PresetError, match="already exists"):
        store.rename(new.key, "other")
    store.delete(new.key)
    assert not new.path.exists() and new.key not in store.presets


def test_export_import_roundtrip(store, builtin, tmp_path):
    params = builtin["nozzle_02_dense"].params.updated(tip_shape="ball", circle_segments=16)
    p = store.save("Share me", params, "nozzle_02_dense")
    out = store.export(p.key, tmp_path / "shared")          # suffix added
    assert out.suffix == ".json" and out.exists()
    other = PresetStore(builtin, tmp_path / "other_user")
    got = other.import_file(out)
    assert got.params == params and got.label == "Share me"
    again = other.import_file(out)                           # name taken: suffixed
    assert again.label == "Share me (2)" and len(other.presets) == 2


@pytest.mark.parametrize("content, needle", [
    ("{not json", "invalid JSON"),
    ("[1, 2]", "not a preset"),
    (json.dumps({"format": "something-else", "values": {}}), "not an ArborFDM preset"),
    (json.dumps({"label": "x"}), "no 'values'"),
    (json.dumps({"label": "x", "values": {"nope": 1}}), "Unknown parameter"),
    (json.dumps({"label": "x", "values": {"overhang_angle_deg": "steep"}}), "Wrong value type"),
    (json.dumps({"label": "x", "values": {"raft_enabled": 1}}), "Wrong value type"),
    (json.dumps({"label": "x", "values": {"overhang_angle_deg": 120}}), "overhang_angle_deg"),
    (json.dumps({"label": "x", "inherits": "nozzle_9_magic", "values": {}}), "not a built-in"),
    (json.dumps({"label": "x", "inherits": "user:other", "values": {}}), "not a built-in"),
    (json.dumps({"label": "x", "version": 99, "values": {}}), "newer"),
    (json.dumps({"label": "", "values": {}}).replace('""', "42"), "name must be text"),
])
def test_invalid_files_give_clear_errors(store, tmp_path, content, needle):
    f = tmp_path / "bad.json"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(PresetError) as ei:
        store.import_file(f)
    assert needle in str(ei.value)
    assert "bad.json" in str(ei.value)
    assert store.presets == {}


def test_binary_and_missing_files(store, tmp_path):
    f = tmp_path / "bin.json"
    f.write_bytes(b"\xff\xfe\x00\x81garbage")
    with pytest.raises(PresetError, match="UTF-8|invalid JSON"):
        store.import_file(f)
    with pytest.raises(PresetError, match="cannot read"):
        store.import_file(tmp_path / "missing.json")


def test_invalid_files_in_folder_are_skipped_and_reported(builtin, tmp_path):
    d = tmp_path / "presets"
    d.mkdir()
    (d / "good.json").write_text(json.dumps({"label": "Good", "inherits": "nozzle_04_normal",
                                             "values": {"walls": 2}}), encoding="utf-8")
    (d / "broken.json").write_text("{", encoding="utf-8")
    (d / "zz_dup.json").write_text(json.dumps({"label": "good", "values": {}}), encoding="utf-8")
    profiles, errors = pr.load_profiles_from_dir(d, builtin)
    assert list(profiles) == ["user:good"]
    assert profiles["user:good"].params.walls == 2
    assert {p.name for p, _ in errors} == {"broken.json", "zz_dup.json"}


def test_ints_accepted_for_float_fields(store, tmp_path):
    f = tmp_path / "ints.json"
    f.write_text(json.dumps({"label": "Ints", "inherits": "nozzle_04_normal",
                             "values": {"overhang_angle_deg": 50, "walls": 3.0}}), "utf-8")
    p = store.import_file(f)
    assert p.params.overhang_angle_deg == 50.0 and isinstance(p.params.walls, int)


def test_builtin_profiles_are_never_written(store, builtin):
    before = _snapshot(PROFILE_DIR)
    p = store.save("X", builtin["nozzle_04_normal"].params.updated(walls=5), "nozzle_04_normal")
    store.rename(p.key, "Y")
    store.delete("user:y")
    assert _snapshot(PROFILE_DIR) == before
    assert store.directory != PROFILE_DIR


def test_project_with_user_preset_saves_and_reloads(store, builtin, tmp_path):
    import trimesh
    params = builtin["nozzle_04_normal"].params.updated(overhang_angle_deg=47.0)
    p = store.save("Proj preset", params, "nozzle_04_normal")
    profiles = store.profiles()
    proj = Project(profiles)
    proj.set_model(trimesh.creation.box((5, 5, 5)), name="box")
    proj.apply_profile(p.key)
    proj.set_param("walls", 4)
    path = proj.save(tmp_path / "a.arbor")
    q = Project.load(path, profiles)
    assert q.profile_key == p.key
    assert q.params == params.updated(walls=4)
    assert q.changed_from_profile() == {"walls": (3, 4)}
    # without the preset (other computer): parameters are kept, default profile is used
    r = Project.load(path, dict(builtin))
    assert r.params == params.updated(walls=4)
    assert r.profile_key in builtin


def test_rebind_profile_updates_undo_history(builtin, store):
    p = store.save("Tmp", builtin["nozzle_04_normal"].params.updated(walls=6), "nozzle_04_normal")
    proj = Project(store.profiles())
    proj.apply_profile(p.key)
    proj.set_param("walls", 7)
    proj.rebind_profile(p.key, "nozzle_04_normal")
    assert proj.profile_key == "nozzle_04_normal" and proj.params.walls == 7
    proj.undo()
    proj.undo()
    proj.redo()
    assert proj.profile_key == "nozzle_04_normal"


@pytest.mark.skipif(os.name == "nt", reason="chmod based")
def test_unwritable_folder_gives_clear_error(builtin, tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        if os.access(d, os.W_OK):
            pytest.skip("running as a user that ignores permissions")
        s = PresetStore(builtin, d)
        with pytest.raises(PresetError, match="Cannot write"):
            s.save("X", builtin["nozzle_04_normal"].params, "nozzle_04_normal")
    finally:
        d.chmod(0o700)
