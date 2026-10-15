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
    assert names[0] == "shelf" and "shelf supports" in names
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
