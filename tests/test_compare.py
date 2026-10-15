"""Comparison of support configurations (arbor/core/compare.py and 'arborfdm-cli compare'),
run for real through the engine on synthetic models and examples/*.stl."""
from __future__ import annotations

import csv
import dataclasses
import io
import json
import math
import threading
from functools import lru_cache
from pathlib import Path

import pytest

from arbor.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, main
from arbor.core.compare import (CRITERIA, PLA_DENSITY_G_CM3, CompareReport, ConfigStats,
                                compare_configs, measure)
from arbor.engine import pipeline
from arbor.engine.params import load_profiles
from arbor.engine.types import Cancelled, Progress
from tests.models import synthetic as S

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
DENSITIES = ("light", "normal", "dense")


@lru_cache(maxsize=None)
def profiles():
    return load_profiles()


@lru_cache(maxsize=None)
def figurine_report() -> CompareReport:
    mesh = S.figurine()
    cfg = [(f"nozzle_02_{d}", profiles()[f"nozzle_02_{d}"].params) for d in DENSITIES]
    return compare_configs(mesh, cfg, model_info={"name": "figurine"})


@lru_cache(maxsize=None)
def bridge_report() -> CompareReport:
    mesh = S.bridge()
    cfg = [(f"nozzle_04_{d}", profiles()[f"nozzle_04_{d}"].params, f"nozzle_04_{d}")
           for d in DENSITIES]
    return compare_configs(mesh, cfg)


def _ex(name: str) -> str:
    p = EX / f"{name}.stl"
    if not p.exists():
        pytest.skip(f"example {p} missing")
    return str(p)


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------
def test_figurine_density_monotonic():
    rep = figurine_report()
    assert [c.label for c in rep.configs] == [f"nozzle_02_{d}" for d in DENSITIES]
    assert all(c.ok for c in rep.configs)
    n = [c.contacts for c in rep.configs]
    assert n[0] <= n[1] <= n[2] and n[0] < n[2], n
    area = [c.contact_area_mm2 for c in rep.configs]
    assert area[0] < area[2], area
    for c in rep.configs:
        assert 0 <= c.model_contacts <= c.contacts
        assert 1 <= c.trees <= c.contacts
        assert c.support_volume_mm3 > 0
        assert c.support_mass_g == pytest.approx(c.support_volume_mm3 * PLA_DENSITY_G_CM3 / 1000)
        assert c.total_mass_g >= c.support_mass_g
        assert 0 < c.max_tree_height_mm <= 60.0 + 1.0          # figurine is 60 mm tall
        assert c.time_s > 0
        assert c.errors == sum(c.errors_by_code.values())
        assert c.warnings == sum(c.warnings_by_code.values())


def test_contact_area_matches_tip_diameters():
    rep = bridge_report()
    for c in rep.configs:
        p = profiles()[c.source].params
        assert c.tip_area_mm2 == pytest.approx(c.contacts * math.pi * (p.tip_diameter / 2) ** 2)
        if c.stats_source == "computed":
            assert c.contact_area_mm2 == pytest.approx(c.tip_area_mm2 + c.model_foot_area_mm2)
        else:   # engine value (tip node radii): same order of magnitude
            assert 0.5 * c.tip_area_mm2 <= c.contact_area_mm2 <= 2 * (c.tip_area_mm2 + c.model_foot_area_mm2)


def test_measure_matches_engine_result_and_per_contact_override():
    mesh = S.bridge()
    params = profiles()["nozzle_04_normal"].params
    res = pipeline.generate(mesh.copy(), params)
    res.stats = {}                                      # measure everything here
    active = [c for c in res.contacts if c.enabled]
    s = measure(res, params, "x")
    assert s.contacts == len(active)
    assert s.support_volume_mm3 == pytest.approx(abs(res.support_mesh.volume))
    # a per-contact tip diameter overrides the profile value
    active[0].tip_diameter = 1.0
    s2 = measure(res, params, "x")
    extra = math.pi * 0.25 - math.pi * (params.tip_diameter / 2) ** 2
    assert s2.tip_area_mm2 == pytest.approx(s.tip_area_mm2 + extra)


def test_engine_stats_used_when_present():
    mesh = S.bridge()
    params = profiles()["nozzle_04_normal"].params
    res = pipeline.generate(mesh.copy(), params)
    res.stats = {"support_volume_mm3": 1000.0, "support_mass_g": 1.24, "contact_area_mm2": 3.5,
                 "model_contacts": 99, "max_tree_height_mm": 12.5, "max_slenderness": 7.0}
    s = measure(res, params, "x")
    assert s.stats_source == "engine"
    assert (s.support_volume_mm3, s.support_mass_g, s.contact_area_mm2,
            s.max_tree_height_mm, s.max_slenderness) == (1000.0, 1.24, 3.5, 12.5, 7.0)
    # engine model_contacts counts tips + pads: the report keeps its own count
    assert s.model_contacts == sum(1 for c in res.contacts if c.enabled and c.rests_on_model)
    res.stats = {}
    s = measure(res, params, "x")
    assert s.stats_source == "computed" and s.max_slenderness is None
    assert s.support_volume_mm3 == pytest.approx(abs(res.support_mesh.volume))


def test_best_rows_and_ties():
    rep = bridge_report()
    labels = {c.label for c in rep.configs}
    for key, _, fn in CRITERIA:
        assert rep.best[key] and set(rep.best[key]) <= labels
        low = min(fn(c) for c in rep.configs)
        assert all(fn(c) == low for c in rep.configs if c.label in rep.best[key])
    # hand made report: ties are all listed, failed configs never win
    a = ConfigStats("a", contact_area_mm2=1.0, total_mass_g=2.0, errors=0, warnings=1)
    b = ConfigStats("b", contact_area_mm2=1.0, total_mass_g=1.0, errors=0, warnings=0)
    f = ConfigStats("f", ok=False, failure="bad")
    r = CompareReport([a, b, f])
    r.compute_best()
    assert r.best == {"fewest_marks": ["a", "b"], "least_material": ["b"], "fewest_issues": ["b"]}


def test_failed_config_reported_others_run():
    p = profiles()["nozzle_04_normal"].params
    bad = dataclasses.replace(p, tip_diameter=0.01)        # below half the nozzle: rejected
    assert bad.validate()
    rep = compare_configs(S.cube(), [("good", p), ("bad", bad), ("good", p)])
    assert [c.label for c in rep.configs] == ["good", "bad", "good (2)"]
    assert rep.configs[0].ok and rep.configs[2].ok
    assert not rep.configs[1].ok and "tip_diameter" in rep.configs[1].failure
    assert "bad" not in sum(rep.best.values(), [])
    assert "failed" in rep.to_text() and "generation failed" in rep.to_markdown()


def test_empty_and_cancel():
    with pytest.raises(ValueError):
        compare_configs(S.cube(), [])
    prog = Progress(cancel_event=threading.Event())
    prog.cancel()
    with pytest.raises(Cancelled):
        compare_configs(S.cube(), [("a", profiles()["nozzle_04_normal"].params)], progress=prog)


def test_progress_is_monotonic():
    seen = []
    p = profiles()["nozzle_04_light"].params
    compare_configs(S.bridge(), [("a", p), ("b", p)], progress=Progress(lambda f, m: seen.append(f)))
    assert seen and seen[-1] == pytest.approx(1.0)
    assert all(b >= a - 1e-9 for a, b in zip(seen, seen[1:]))


# ---------------------------------------------------------------------------
# output formats
# ---------------------------------------------------------------------------
def test_json_format():
    rep = figurine_report()
    d = json.loads(rep.to_json())
    assert d["format"] == "arborfdm-compare"
    assert [c["label"] for c in d["configs"]] == [c.label for c in rep.configs]
    assert d["configs"][2]["contacts"] == rep.configs[2].contacts
    assert set(d["best"]) == {k for k, _, _ in CRITERIA}
    assert "does not mean" in d["note"]


def test_markdown_format():
    rep = figurine_report()
    md = rep.to_markdown()
    rows = [line for line in md.splitlines() if line.startswith("|")]
    assert len(rows) == 2 + len(rep.configs)
    ncol = rows[0].count("|")
    assert all(r.count("|") == ncol for r in rows)
    assert set(rows[1].replace("|", "").replace(":", "")) == {"-"}
    assert md.count("**Best,") == len(CRITERIA)
    assert "prints better" in md


def test_csv_format():
    rep = figurine_report()
    rows = list(csv.DictReader(io.StringIO(rep.to_csv())))
    assert [r["label"] for r in rows] == [c.label for c in rep.configs]
    for r, c in zip(rows, rep.configs):
        assert int(r["contacts"]) == c.contacts
        assert float(r["support_volume_mm3"]) == pytest.approx(c.support_volume_mm3, rel=1e-5)
        for key, _, _ in CRITERIA:
            assert r[f"best_{key}"] == ("1" if c.label in rep.best[key] else "0")


def test_text_table_aligned():
    rep = bridge_report()
    lines = rep.to_text().splitlines()
    assert lines[0].startswith("config") and set(lines[1].replace(" ", "")) == {"-"}
    table = lines[:2 + len(rep.configs)]
    assert len({len(line) for line in table[1:]}) == 1      # numbers right aligned on one width


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def test_cli_compare_writes_all_formats(tmp_path, capsys):
    j, m, c = tmp_path / "r.json", tmp_path / "sub" / "r.md", tmp_path / "r.csv"
    code = main(["compare", _ex("bridge"), "--profile", "nozzle_04_light",
                 "--profile", "nozzle_04_dense", "--json", str(j), "--md", str(m), "--csv", str(c)])
    out = capsys.readouterr().out
    d = json.loads(j.read_text(encoding="utf-8"))
    assert code == (EXIT_PROBLEMS if any(x["errors"] for x in d["configs"]) else EXIT_OK)
    assert [x["label"] for x in d["configs"]] == ["nozzle_04_light", "nozzle_04_dense"]
    assert d["configs"][0]["contacts"] < d["configs"][1]["contacts"]
    assert d["model"]["name"] and d["model"]["prepare"]["check"]["valid"] is True
    assert "nozzle_04_light" in out and "Best, fewest marks" in out
    assert m.read_text(encoding="utf-8").count("| nozzle_04_") == 2
    assert len(list(csv.DictReader(c.open(encoding="utf-8")))) == 2


def test_cli_compare_figurine_three_02_profiles(tmp_path):
    j = tmp_path / "f.json"
    code = main(["compare", _ex("figurine"), "-q", "--json", str(j)]
                + sum((["--profile", f"nozzle_02_{d}"] for d in DENSITIES), []))
    d = json.loads(j.read_text(encoding="utf-8"))
    assert code in (EXIT_OK, EXIT_PROBLEMS)
    assert code == (EXIT_PROBLEMS if any(x["errors"] or not x["ok"] for x in d["configs"]) else EXIT_OK)
    n = [x["contacts"] for x in d["configs"]]
    assert n[0] <= n[1] <= n[2] and n[0] < n[2], n


def test_cli_compare_set_applies_to_every_config(tmp_path):
    j = tmp_path / "r.json"
    assert main(["compare", _ex("bridge"), "-q", "--profile", "nozzle_04_light",
                 "--profile", "nozzle_04_normal", "--set", "tip_diameter=0.6",
                 "--json", str(j)]) in (EXIT_OK, EXIT_PROBLEMS)
    for x in json.loads(j.read_text(encoding="utf-8"))["configs"]:
        assert x["tip_area_mm2"] == pytest.approx(x["contacts"] * math.pi * 0.3 ** 2)


def test_cli_compare_user_preset_in_order(tmp_path, monkeypatch):
    from arbor.core.presets import PresetStore
    d = tmp_path / "presets"
    monkeypatch.setenv("ARBOR_USER_PRESET_DIR", str(d))
    base = profiles()["nozzle_04_normal"].params
    PresetStore(profiles(), d).save("My Fine Tips", base.updated(tip_diameter=0.35), "nozzle_04_normal")
    j = tmp_path / "r.json"
    code = main(["compare", _ex("bridge"), "-q", "--preset", "my fine tips",
                 "--profile", "nozzle_04_normal", "--preset", "user:my_fine_tips", "--json", str(j)])
    assert code in (EXIT_OK, EXIT_PROBLEMS)
    cfg = json.loads(j.read_text(encoding="utf-8"))["configs"]
    assert [x["label"] for x in cfg] == ["My Fine Tips", "nozzle_04_normal", "My Fine Tips (2)"]
    assert cfg[0]["source"] == "user:my_fine_tips"
    assert cfg[0]["tip_area_mm2"] == pytest.approx(cfg[0]["contacts"] * math.pi * 0.175 ** 2)


@pytest.mark.parametrize("argv", [
    ["--profile", "no_such_profile"],
    ["--preset", "no such preset"],
    [],                                              # nothing to compare
    ["--profile", "nozzle_04_normal", "--set", "nope=1"],
])
def test_cli_compare_usage_errors(argv, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARBOR_USER_PRESET_DIR", str(tmp_path / "empty"))
    assert main(["compare", _ex("cube")] + argv) == EXIT_USAGE
    assert "error:" in capsys.readouterr().err


def test_cli_compare_broken_mesh_needs_repair(tmp_path):
    src = _ex("broken_hole")
    assert main(["compare", src, "-q", "--profile", "nozzle_04_light"]) == EXIT_USAGE
    j = tmp_path / "r.json"
    code = main(["compare", src, "-q", "--repair", "--profile", "nozzle_04_light", "--json", str(j)])
    assert code in (EXIT_OK, EXIT_PROBLEMS)
    d = json.loads(j.read_text(encoding="utf-8"))
    assert d["model"]["prepare"]["repair"]["success"] is True


def test_cli_compare_auto_orient(tmp_path):
    j = tmp_path / "r.json"
    code = main(["compare", _ex("suspended_arm"), "-q", "--auto-orient",
                 "--profile", "nozzle_04_light", "--json", str(j)])
    assert code in (EXIT_OK, EXIT_PROBLEMS)
    assert "orientation" in json.loads(j.read_text(encoding="utf-8"))["model"]["prepare"]
