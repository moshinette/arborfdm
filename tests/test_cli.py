"""Command line interface (arbor/cli.py), run for real: main(argv) in-process and
``python -m arbor`` in a subprocess."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

from arbor.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, apply_overrides, main
from arbor.core.mesh_io import read_3mf
from arbor.core.repair import check_mesh
from arbor.engine.params import SupportParams
from arbor.io.export import validate_export

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"


def _ex(name: str) -> str:
    p = EX / f"{name}.stl"
    if not p.exists():
        pytest.skip(f"example {p} missing")
    return str(p)


# ---------------------------------------------------------------------------
# profiles / parameters
# ---------------------------------------------------------------------------
def test_profiles_lists_keys(capsys):
    assert main(["profiles"]) == EXIT_OK
    out = capsys.readouterr().out
    for key in ("nozzle_04_normal", "nozzle_02_light", "nozzle_04_dense"):
        assert key in out
    assert "_nozzle_04" not in out.split()          # abstract base profiles hidden


def test_profiles_json(capsys):
    assert main(["profiles", "--json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["nozzle_04_normal"]["params"]["nozzle_diameter"] == pytest.approx(0.4)
    assert data["nozzle_02_normal"]["params"]["nozzle_diameter"] == pytest.approx(0.2)


def test_apply_overrides_types():
    p = apply_overrides(SupportParams(), ["tip_diameter=0.5", "circle_segments=16",
                                          "raft_enabled=yes", "tip_shape=ball"])
    assert p.tip_diameter == 0.5 and isinstance(p.tip_diameter, float)
    assert p.circle_segments == 16 and isinstance(p.circle_segments, int)
    assert p.raft_enabled is True and p.tip_shape == "ball"


@pytest.mark.parametrize("bad", ["nope=1", "tip_diameter", "tip_diameter=abc",
                                 "raft_enabled=maybe", "tip_diameter=0.01"])
def test_apply_overrides_rejects(bad):
    from arbor.cli import CliError
    with pytest.raises(CliError):
        apply_overrides(SupportParams(), [bad])


def test_generate_bad_set_exit_code(tmp_path, capsys):
    rc = main(["generate", _ex("shelf"), "-o", str(tmp_path / "x.3mf"), "--set", "no_such=1"])
    assert rc == EXIT_USAGE
    assert "unknown parameter" in capsys.readouterr().err
    assert not (tmp_path / "x.3mf").exists()


def test_generate_unknown_profile(tmp_path):
    assert main(["generate", _ex("shelf"), "-o", str(tmp_path / "x.3mf"),
                 "--profile", "nozzle_99"]) == EXIT_USAGE


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def test_check_valid_and_broken(tmp_path, capsys):
    assert main(["check", _ex("cube")]) == EXIT_OK
    assert "Mesh OK" in capsys.readouterr().out
    rep = tmp_path / "c.json"
    assert main(["check", _ex("broken_hole"), "--json", str(rep)]) == EXIT_PROBLEMS
    err = capsys.readouterr().err
    assert "hole" in err
    data = json.loads(rep.read_text())
    assert data["check"]["holes"] == 1 and data["check"]["valid"] is False


def test_check_missing_file(tmp_path, capsys):
    assert main(["check", str(tmp_path / "nothing.stl")]) == EXIT_USAGE
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------
def test_generate_shelf_full(tmp_path, capsys):
    out = tmp_path / "shelf.3mf"
    stl = tmp_path / "sup.stl"
    rep = tmp_path / "r.json"
    rc = main(["generate", _ex("shelf"), "-o", str(out), "--profile", "nozzle_04_normal",
               "--set", "tip_diameter=0.5", "--stl-supports", str(stl), "--json", str(rep)])
    io = capsys.readouterr()
    assert rc == EXIT_OK, io.err
    assert "%]" in io.err                       # progress lines
    assert "disable the slicer's own supports" in io.out

    assert validate_export(out) == []
    content = read_3mf(out)
    names = [p.name for p in content.parts]
    # v0.2 default raft under the model (with a release gap): the model does not touch
    # the plate, so the separate layout writes one object to keep the alignment
    assert names == ["shelf with supports"] or (names[0] == "shelf" and "shelf supports" in names)
    params = json.loads(content.metadata["arbor:support_params"])
    assert params["tip_diameter"] == 0.5
    for p in content.parts:                     # our reader (bit-identical merge)
        r = check_mesh(p.mesh)
        assert r.watertight and r.winding_consistent, (p.name, r.summary())
    sc = trimesh.load(str(out))                 # trimesh merges vertices by position
    for g in sc.geometry.values():
        assert g.is_watertight
    zmin = min(p.mesh.bounds[0, 2] for p in content.parts)
    assert zmin == pytest.approx(0.0, abs=1e-6)

    s = trimesh.load(str(stl))
    assert len(s.faces) > 0 and s.is_watertight

    data = json.loads(rep.read_text())
    assert data["exit_code"] == EXIT_OK
    assert data["params"]["tip_diameter"] == 0.5
    assert data["tree"]["tips"] == len([c for c in data["contacts"] if c["enabled"]]) > 0
    assert data["export"]["validation_problems"] == []
    assert Path(data["files"]["output"]) == out


def test_generate_stl_output(tmp_path):
    out = tmp_path / "all.stl"
    assert main(["-q", "generate", _ex("shelf"), "-o", str(out)]) == EXIT_OK
    m = trimesh.load(str(out))
    shelf = trimesh.load(_ex("shelf"))
    assert m.volume > shelf.volume              # model + supports


def test_generate_invalid_mesh_needs_repair(tmp_path, capsys):
    out = tmp_path / "bh.3mf"
    assert main(["generate", _ex("broken_hole"), "-o", str(out)]) == EXIT_USAGE
    assert "--repair" in capsys.readouterr().err
    assert not out.exists()
    rep = tmp_path / "bh.json"
    assert main(["generate", _ex("broken_hole"), "-o", str(out), "--repair",
                 "--json", str(rep)]) == EXIT_OK
    data = json.loads(rep.read_text())
    assert data["repair"]["success"] is True
    assert any("Filled" in a for a in data["repair"]["actions"])
    part = read_3mf(out).parts[0]
    assert check_mesh(part.mesh).watertight
    # the input file itself is untouched
    from arbor.core.mesh_io import load_mesh
    assert check_mesh(load_mesh(_ex("broken_hole")).mesh).n_holes == 1


def test_generate_errors_give_exit_1(tmp_path, capsys):
    """manual_only with no manual contact leaves the shelf unsupported: verification
    error, file still written, exit code 1."""
    out = tmp_path / "m.3mf"
    rc = main(["generate", _ex("shelf"), "-o", str(out), "--set", "manual_only=true"])
    assert rc == EXIT_PROBLEMS
    assert "verify:" in capsys.readouterr().err
    assert out.exists() and validate_export(out) == []


def test_generate_auto_orient(tmp_path):
    """The shelf is generated as given; auto-orient must pick a pose with less overhang
    and report it."""
    rep = tmp_path / "o.json"
    out = tmp_path / "o.3mf"
    assert main(["-q", "generate", _ex("shelf"), "-o", str(out), "--auto-orient",
                 "--json", str(rep)]) in (EXIT_OK, EXIT_PROBLEMS)
    data = json.loads(rep.read_text())
    assert "orientation" in data and "label" in data["orientation"]
    m = read_3mf(out).parts[0].mesh
    assert m.bounds[0, 2] >= -1e-6
    # plate transform reported = rotation then placement
    t = np.asarray(data["plate_transform"])
    assert t.shape == (4, 4) and abs(abs(np.linalg.det(t[:3, :3])) - 1) < 1e-6


def test_generate_components_layout(tmp_path):
    out = tmp_path / "c.3mf"
    assert main(["-q", "generate", _ex("shelf"), "-o", str(out), "--layout", "components"]) == EXIT_OK
    with zipfile.ZipFile(out) as z:
        xml = z.read("3D/3dmodel.model").decode()
    assert xml.count("<item ") == 1 and "<components>" in xml


def test_generate_separate_layout_with_layer_heights(tmp_path, capsys):
    out, js = tmp_path / "sep.3mf", tmp_path / "r.json"
    # two objects on the plate: raft under the feet only (with the v0.2 default raft
    # under the model, the separate layout writes one object)
    rc = main(["generate", _ex("shelf"), "-o", str(out), "--set", "raft_enabled=true",
               "--set", "raft_coverage=feet",
               "--layout", "separate", "--model-layer-height", "0.12",
               "--support-layer-height", "0.24", "--json", str(js)])
    assert rc == EXIT_OK
    assert "Separate objects, each resting on the plate" in capsys.readouterr().out
    assert validate_export(out) == []
    rep = json.loads(js.read_text())
    assert rep["export"]["layout"] == "separate"
    assert rep["export"]["layer_heights"] == {"model": 0.12, "supports": 0.24}
    with zipfile.ZipFile(out) as z:
        assert "Metadata/model_settings.config" in z.namelist()
        cfg = z.read("Metadata/model_settings.config").decode()
    assert 'key="layer_height" value="0.12"' in cfg and 'key="layer_height" value="0.24"' in cfg
    for part in read_3mf(out).parts:
        assert part.mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-6), part.name


def test_layer_height_options_need_separate_layout(tmp_path):
    out = tmp_path / "x.3mf"
    assert main(["generate", _ex("shelf"), "-o", str(out), "--model-layer-height", "0.1"]) == EXIT_USAGE
    assert main(["generate", _ex("shelf"), "-o", str(tmp_path / "x.stl"), "--layout", "separate",
                 "--support-layer-height", "0.2"]) == EXIT_USAGE
    assert main(["generate", _ex("shelf"), "-o", str(out), "--layout", "separate",
                 "--support-layer-height", "-1"]) == EXIT_USAGE
    assert not out.exists()


def test_bad_output_extension(tmp_path):
    assert main(["generate", _ex("shelf"), "-o", str(tmp_path / "x.obj")]) == EXIT_USAGE


# ---------------------------------------------------------------------------
# subprocess: python -m arbor
# ---------------------------------------------------------------------------
def _run(*args, cwd=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "arbor", *args], capture_output=True, text=True,
                          env=env, cwd=cwd or ROOT, timeout=600)


def test_module_entry_point(tmp_path):
    r = _run("--version")
    assert r.returncode == 0 and "arborfdm-cli" in r.stdout
    r = _run("profiles")
    assert r.returncode == 0 and "nozzle_04_normal" in r.stdout
    r = _run("bogus")
    assert r.returncode == 2


def test_module_generate_subprocess(tmp_path):
    out = tmp_path / "s.3mf"
    r = _run("generate", _ex("shelf"), "-o", str(out), "-q", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert validate_export(out) == []


# ---------------------------------------------------------------------------
# OrcaSlicer: --open-in-orca, orca --find / --set-path
# ---------------------------------------------------------------------------
def _fake_orca(path: Path, record: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport json, sys\n"
                    f"open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def orca_env(tmp_path, monkeypatch):
    """Isolated settings / to_slicer folders, no OrcaSlicer reachable by detection."""
    import arbor.io.slicer_launch as sl
    monkeypatch.setenv("ARBOR_USER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("ARBOR_TO_SLICER_DIR", str(tmp_path / "to_slicer"))
    monkeypatch.setattr(sl, "candidate_paths", lambda *a, **k: [tmp_path / "nowhere" / "orca-slicer"])
    return tmp_path


def _wait(p: Path) -> None:
    import time
    for _ in range(200):
        if p.exists() and p.stat().st_size:
            return
        time.sleep(0.05)
    raise AssertionError(f"{p} not written")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fake executable is a POSIX script")
def test_generate_open_in_orca_without_output(orca_env, capsys):
    rec = orca_env / "args.json"
    exe = _fake_orca(orca_env / "bin" / "orca-slicer", rec)
    js = orca_env / "r.json"
    src = Path(_ex("shelf"))
    before = src.read_bytes()
    # raft under the feet only: model and supports are two objects (one with the
    # v0.2 default raft under the model)
    rc = main(["generate", str(src), "--open-in-orca", "--orca-path", str(exe),
               "--set", "raft_coverage=feet", "--model-layer-height", "0.12", "--support-layer-height", "0.24", "--json", str(js)])
    assert rc == EXIT_OK
    _wait(rec)
    files = list((orca_env / "to_slicer").glob("*.3mf"))
    assert len(files) == 1 and files[0].name.startswith("shelf_")
    assert json.loads(rec.read_text()) == [str(files[0].resolve())]
    assert src.read_bytes() == before
    rep = json.loads(js.read_text())
    assert rep["orca"]["opened"] is True and rep["orca"]["file"] == str(files[0])
    assert validate_export(files[0]) == []
    with zipfile.ZipFile(files[0]) as z:
        cfg = z.read("Metadata/model_settings.config").decode()
    assert cfg.count('key="enable_support" value="0"') == 2 and 'value="0.24"' in cfg
    assert "Opening in OrcaSlicer" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fake executable is a POSIX script")
def test_generate_open_in_orca_uses_saved_path_and_output(orca_env):
    rec = orca_env / "args.json"
    exe = _fake_orca(orca_env / "saved" / "orca", rec)
    assert main(["orca", "--set-path", str(exe)]) == EXIT_OK
    out = orca_env / "out.3mf"
    assert main(["-q", "generate", _ex("shelf"), "-o", str(out), "--open-in-orca"]) == EXIT_OK
    _wait(rec)
    sent = Path(json.loads(rec.read_text())[0])
    assert out.exists() and sent.parent == (orca_env / "to_slicer").resolve() and sent != out


def test_generate_open_in_orca_not_found_still_exports(orca_env, capsys):
    rc = main(["generate", _ex("shelf"), "--open-in-orca"])
    assert rc == EXIT_PROBLEMS
    err = capsys.readouterr().err
    assert "OrcaSlicer not found" in err
    assert len(list((orca_env / "to_slicer").glob("*.3mf"))) == 1


def test_open_in_orca_usage_errors(orca_env):
    assert main(["generate", _ex("shelf")]) == EXIT_USAGE                       # no -o
    assert main(["generate", _ex("shelf"), "-o", str(orca_env / "x.3mf"),
                 "--orca-path", "/x"]) == EXIT_USAGE                              # needs --open-in-orca
    assert main(["generate", _ex("shelf"), "--open-in-orca",
                 "--orca-path", str(orca_env / "missing")]) == EXIT_USAGE
    assert not (orca_env / "to_slicer").exists()                                 # failed before work


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fake executable is a POSIX script")
def test_orca_find_set_clear(orca_env, capsys):
    assert main(["orca", "--find"]) == EXIT_PROBLEMS
    out = capsys.readouterr().out
    assert "not found" in out and str(orca_env / "nowhere" / "orca-slicer") in out
    exe = _fake_orca(orca_env / "o" / "orca-slicer", orca_env / "unused.json")
    assert main(["orca", "--set-path", str(orca_env / "missing")]) == EXIT_USAGE
    assert main(["orca", "--set-path", str(exe)]) == EXIT_OK
    capsys.readouterr()
    assert main(["orca", "--find"]) == EXIT_OK
    assert f"OrcaSlicer: {exe}" in capsys.readouterr().out
    assert main(["orca", "--clear-path"]) == EXIT_OK
    assert main(["orca", "--find"]) == EXIT_PROBLEMS


def test_default_layout_is_separate_with_raft(tmp_path):
    """With a raft, the default 3MF keeps model and supports aligned in slicers that drop
    each object to the bed (2nd print test: supports lowered by the raft thickness)."""
    import zipfile
    from arbor.cli import main
    out = tmp_path / "raft.3mf"
    # raft under the feet only (v0.1 raft): the model stays on the plate; with the v0.2
    # default raft under the model the scene is one object (tests/test_raft_lift.py)
    rc = main(["generate", str(ROOT / "examples" / "shelf.stl"), "-o", str(out), "-q",
               "--set", "raft_enabled=true", "--set", "raft_coverage=feet"])
    assert rc in (0, 1)
    with zipfile.ZipFile(out) as z:
        xml = z.read("3D/3dmodel.model").decode()
    assert xml.count("<item ") == 2          # model + (supports + raft), both on the plate
