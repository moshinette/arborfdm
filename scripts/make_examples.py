"""Write STL files of synthetic models to examples/ (binary STL, mm, Z up).

Usage: python scripts/make_examples.py [--out examples]
Total size is kept below 15 MB (checked at the end).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.models import synthetic as S  # noqa: E402

EXAMPLES = {
    "cube": lambda: S.cube(),
    "shelf": lambda: S.shelf(),
    "bridge": lambda: S.bridge(),
    "suspended_arm": lambda: S.suspended_arm(),
    "floating_cube": lambda: S.floating_cube(),
    "sphere_on_plate": lambda: S.sphere_on_plate(),
    "inclined_60deg": lambda: S.inclined_surface(60),
    "cavity": lambda: S.cavity(),
    "crossing_case": lambda: S.crossing_case(),
    "impossible_case": lambda: S.impossible_case(),
    "figurine": lambda: S.figurine(40_000),
    "broken_hole": lambda: S.broken_hole(),
    "broken_nonmanifold": lambda: S.broken_nonmanifold(),
}
LIMIT = 15 * 1024 * 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "examples"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, gen in EXAMPLES.items():
        m = gen()
        p = out / f"{name}.stl"
        m.export(p, file_type="stl")
        size = p.stat().st_size
        total += size
        print(f"{p.name:26s} {len(m.faces):7d} faces  {size / 1024:8.1f} KiB  watertight={m.is_watertight}")
    print(f"total {total / 1024 / 1024:.2f} MiB")
    if total > LIMIT:
        print("ERROR: examples exceed 15 MB", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
