"""Printable calibration bench for support tips (see docs/PHYSICAL_TESTS.md).

Model: a 70 x 14 x 2 mm base with N pillars. Each pillar carries a flat cantilever plate
(10 x 10 x 1.2 mm) sticking out towards +y, at increasing heights (6, 9, 12, 15 mm by
default). Plate i is identified by i+1 small cubes on the base in front of its pillar.
Every plate underside is a flat horizontal overhang that the generator must support.

Usage:
  python scripts/make_tip_bench.py                      # writes the bare model STL
  python scripts/make_tip_bench.py --supports           # also one 3MF per tip diameter
  python scripts/make_tip_bench.py --supports --tips 0.3 0.4 0.5 0.6 --profile nozzle_04_normal
  python scripts/make_tip_bench.py --supports --densities  # one 3MF per light/normal/dense

Output: examples/test_bench/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "examples" / "test_bench"


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return mf.Manifold.cube(tuple(hi - lo)).translate(tuple(lo))


def make_bench(n: int = 4, z0: float = 6.0, dz: float = 3.0, plate: float = 10.0,
               plate_t: float = 1.2, pitch: float = 17.0) -> trimesh.Trimesh:
    width = pitch * n + 2
    parts = [_box((0, 0, 0), (width, 14, 2))]
    for i in range(n):
        x = 2 + i * pitch
        z = z0 + i * dz
        parts.append(_box((x, 0, 0), (x + 4, 4, z + plate_t)))                       # pillar
        parts.append(_box((x, 3.5, z), (x + plate, 3.5 + plate, z + plate_t)))      # plate
        for k in range(i + 1):                                                       # id marks
            parts.append(_box((x + 5 + 1.6 * k, 0.5, 2), (x + 6 + 1.6 * k, 1.5, 2.6)))
    m = mf.Manifold.batch_boolean(parts, mf.OpType.Add)
    mg = m.to_mesh()
    t = trimesh.Trimesh(np.asarray(mg.vert_properties, float)[:, :3], np.asarray(mg.tri_verts), process=True)
    t.metadata["plates"] = [((2 + i * pitch, 3.5, z0 + i * dz), (2 + i * pitch + plate, 3.5 + plate,
                                                                z0 + i * dz + plate_t)) for i in range(n)]
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--supports", action="store_true", help="generate supports with the engine")
    ap.add_argument("--tips", type=float, nargs="*", default=[0.3, 0.4, 0.5, 0.6])
    ap.add_argument("--profile", default="nozzle_04_normal")
    ap.add_argument("--densities", action="store_true", help="vary density instead of tip diameter")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    bench = make_bench()
    assert bench.is_watertight
    bench.export(out / "tip_bench.stl")
    print(f"tip_bench.stl  {len(bench.faces)} faces  bounds {bench.bounds.round(2).tolist()}")
    if not a.supports:
        return 0

    from arbor.engine import pipeline
    from arbor.engine.params import load_profiles
    from arbor.io.export import export_3mf

    profiles = load_profiles()
    if a.densities:
        nozzle = a.profile.split("_")[1]
        runs = [(f"density_{d}", profiles[f"nozzle_{nozzle}_{d}"].params) for d in ("light", "normal", "dense")]
    else:
        base = profiles[a.profile].params
        runs = []
        for tip in a.tips:
            over = {"tip_diameter": tip}
            if base.tip_base_diameter < tip:
                over["tip_base_diameter"] = tip
            runs.append((f"tip_{tip:.2f}".replace(".", "p"), base.updated(**over)))
    for name, p in runs:
        res = pipeline.generate(bench.copy(), p)
        path = out / f"tip_bench_{name}.3mf"
        export_3mf(path, bench, res.support_mesh, res.raft_mesh)
        print(f"{path.name}: {len([c for c in res.contacts if c.enabled])} contacts, "
              f"tip {p.tip_diameter} mm, errors={[i.code for i in res.errors]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
