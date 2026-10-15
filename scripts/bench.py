"""Reproducible benchmark of the ArborFDM engine stages.

Models: the procedural figurine refined to ~10k, 100k, 500k, 1M faces (deterministic,
tests/models/synthetic.py; no random numbers are used, the seed is fixed anyway for any
stage that might use numpy's global RNG).

Each stage is timed separately, in the same order as arbor.engine.pipeline.generate:
analysis, contacts, tree, mesh (build.mesh_tree), verify. Every size is run --repeat times
and the median is reported. Writes docs/BENCHMARKS.md with machine information and ONLY
measured numbers.

Usage: python scripts/bench.py [--sizes 10000 100000 500000 1000000] [--repeat 3]
                               [--profile nozzle_04_normal] [--no-write]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.models import synthetic as S  # noqa: E402

SEED = 12345
STAGES = ["analysis", "contacts", "tree", "mesh", "verify"]


def machine_info() -> dict:
    info = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "processor": platform.processor() or "",
    }
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["processor"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                info["memory"] = f"{int(line.split()[1]) / 1024 / 1024:.1f} GiB"
                break
    except OSError:
        pass
    import manifold3d
    import scipy
    import shapely
    import trimesh
    info["numpy"] = np.__version__
    info["scipy"] = scipy.__version__
    info["trimesh"] = trimesh.__version__
    info["shapely"] = shapely.__version__
    try:
        from importlib.metadata import version
        info["manifold3d"] = version("manifold3d")
    except Exception:
        info["manifold3d"] = getattr(manifold3d, "__version__", "?")
    try:
        info["commit"] = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                                 text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "arbor"], cwd=ROOT, text=True)
        if dirty.strip():
            info["commit"] += " (+ uncommitted changes in arbor/)"
    except Exception:
        info["commit"] = "?"
    return info


def available_stages() -> list[str]:
    import importlib
    mods = {"analysis": "analysis", "contacts": "contacts", "tree": "tree", "mesh": "build", "verify": "verify"}
    out = []
    for st in STAGES:
        try:
            importlib.import_module(f"arbor.engine.{mods[st]}")
        except ImportError:
            break  # later stages depend on earlier ones
        out.append(st)
    return out


def run_once(mesh, params, stages) -> tuple[dict, dict]:
    import importlib
    from arbor.engine.types import Progress
    E = lambda m: importlib.import_module(f"arbor.engine.{m}")  # noqa: E731

    np.random.seed(SEED)
    pr = Progress()
    t: dict = {}
    out: dict = {}
    t0 = time.perf_counter()
    an = E("analysis").analyze(mesh, params, pr)
    t["analysis"] = time.perf_counter() - t0
    if "contacts" in stages:
        t0 = time.perf_counter()
        cs = E("contacts").place_contacts(mesh, an, params, [], pr)
        t["contacts"] = time.perf_counter() - t0
        active = [c for c in cs if c.enabled]
        out["n_contacts"] = len(active)
    if "tree" in stages:
        t0 = time.perf_counter()
        tr = E("tree").build_tree(mesh, active, params, pr)
        t["tree"] = time.perf_counter() - t0
        out.update(tr.stats())
    if "mesh" in stages:
        t0 = time.perf_counter()
        sm = E("build").mesh_tree(tr, params, pr)
        t["mesh"] = time.perf_counter() - t0
        out["support_faces"] = len(sm.faces) if sm is not None else 0
        out["watertight"] = bool(sm is not None and len(sm.faces) and sm.is_watertight)
    if "verify" in stages:
        t0 = time.perf_counter()
        issues = E("verify").verify(mesh, an, cs, tr, sm, params, pr)
        t["verify"] = time.perf_counter() - t0
        out["errors"] = sum(1 for i in issues if i.severity == "error")
    t["total"] = sum(t.values())
    return t, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="*", default=[10_000, 100_000, 500_000, 1_000_000])
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--profile", default="nozzle_04_normal")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    from arbor.engine.params import load_profiles
    params = load_profiles()[a.profile].params
    info = machine_info()
    stages = available_stages()
    missing = [st for st in STAGES if st not in stages]
    if missing:
        print(f"WARNING: stages not available, not measured: {missing}", flush=True)
    rows = []
    for size in a.sizes:
        t0 = time.perf_counter()
        mesh = S.large_mesh(size)
        t_model = time.perf_counter() - t0
        reps = []
        out = {}
        for r in range(a.repeat if size <= 100_000 else max(1, a.repeat - 2)):
            t, out = run_once(mesh.copy(), params, stages)
            reps.append(t)
            print(f"{size:>9d} target, {len(mesh.faces):>8d} faces, run {r + 1}: "
                  + ", ".join(f"{k} {v:.2f}s" for k, v in t.items()), flush=True)
        med = {k: statistics.median(x[k] for x in reps) for k in reps[0]}
        rows.append({"target": size, "faces": len(mesh.faces), "runs": len(reps),
                     "model_gen": t_model, **med, **out})
        del mesh

    md = render(info, a, rows, missing)
    print(md)
    if not a.no_write:
        (ROOT / "docs" / "BENCHMARKS.md").write_text(md, encoding="utf-8")
        print("written docs/BENCHMARKS.md")
    return 0


def render(info, a, rows, missing) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Benchmarks",
        "",
        f"Mesure du {now}, produite par `python scripts/bench.py` (aucune valeur saisie à la main).",
        "",
    ]
    if missing:
        lines += [f"**Mesure partielle** : étapes absentes du moteur au moment de la mesure, non mesurées : "
                  f"{', '.join(missing)}. Le total ne couvre que les étapes mesurées.", ""]
    lines += ["## Machine", "", "| Élément | Valeur |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in info.items()]
    lines += [
        "",
        "## Protocole",
        "",
        f"- Modèle : figurine procédurale (`tests/models/synthetic.py::large_mesh`), raffinée à la taille cible. "
        f"Déterministe, graine numpy {SEED}.",
        f"- Profil : `{a.profile}`.",
        "- Étapes chronométrées séparément, dans l'ordre de `pipeline.generate`. Médiane sur `runs` exécutions.",
        "- Temps en secondes, un seul processus. `n.m.` = non mesuré.",
        "",
        "## Résultats (s)",
        "",
        "| Faces | Runs | génération modèle | analysis | contacts | tree | mesh | verify | total | nb contacts "
        "| arbres | faces support | étanche | erreurs |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def f(r, k, fmt="{:.2f}"):
        v = r.get(k)
        if v is None:
            return "n.m."
        if isinstance(v, bool):
            return "oui" if v else "non"
        return fmt.format(v)

    for r in rows:
        cells = [f"{r['faces']:,}".replace(",", " "), str(r["runs"]), f(r, "model_gen")]
        cells += [f(r, k) for k in STAGES + ["total"]]
        cells += [f(r, "n_contacts", "{}"), f(r, "trees", "{}"), f(r, "support_faces", "{}"),
                  f(r, "watertight"), f(r, "errors", "{}")]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
