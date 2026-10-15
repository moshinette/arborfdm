"""Compare several support configurations on the same model (no Qt).

    report = compare_configs(mesh, [("light", p1), ("normal", p2), ("dense", p3)])
    print(report.to_text()); report.to_json(); report.to_markdown(); report.to_csv()

For every configuration the full engine (arbor.engine.pipeline.generate) is run on a copy
of the same placed mesh, and measurable quantities are collected:

  contacts            active contacts (tips touching the model)
  model_contacts      of which the support stands on the model (rests_on_model)
  model_feet          feet (pads) standing on the model, one per support resting on it
  trees               number of trees (roots of the support graph)
  support_volume_mm3  volume of the support mesh (raft excluded, see raft_volume_mm3)
  support_mass_g      support_volume x PLA density (1.24 g/cm3)
  raft_volume_mm3     raft volume (0 without raft); total_mass_g = supports + raft in PLA
  contact_area_mm2    sum of the areas touching the model: pi (d/2)^2 per active contact
                      (d = contact.tip_diameter or params.tip_diameter) plus, for every
                      support standing on the model, its foot on the model (radius of the
                      model foot, ~ on_model_tip_diameter / 2). A proxy of the marks left on
                      the part, not a prediction of surface quality.
  max_tree_height_mm  highest tip above the foot of its tree
  max_slenderness     largest section slenderness (engine stats only, else None)
  errors / warnings   verification issues, also counted by code
  time_s              generation time

When the engine provides ``GenerationResult.stats`` (support_volume_mm3, support_mass_g,
contact_area_mm2, model_contacts, max_tree_height_mm, max_slenderness), those values are
used; otherwise they are computed here. ``model_contacts`` is always counted here (the
engine's ``model_contacts`` counts every place touching the model, tips + pads, which is
the ``contacts + model_feet`` of this report).

The "best" rows only say which configuration minimises one measurable criterion (fewest
marks, least material, fewest issues). None of them claims that a configuration prints
better: that has to be checked on a real print.
"""
from __future__ import annotations

import csv
import io
import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional, Sequence

import numpy as np

PLA_DENSITY_G_CM3 = 1.24

# column key, header, number of decimals (None = integer / text)
COLUMNS: list[tuple[str, str, Optional[int]]] = [
    ("label", "config", None),
    ("contacts", "contacts", None),
    ("model_contacts", "on model", None),
    ("trees", "trees", None),
    ("support_volume_mm3", "support mm3", 1),
    ("support_mass_g", "support g", 2),
    ("total_mass_g", "total g (+raft)", 2),
    ("contact_area_mm2", "contact mm2", 2),
    ("max_tree_height_mm", "max height mm", 1),
    ("max_slenderness", "max slender.", 1),
    ("errors", "errors", None),
    ("warnings", "warnings", None),
    ("time_s", "time s", 1),
]

# criterion key, human label, sort key on a ConfigStats (lower is better)
CRITERIA: list[tuple[str, str, Callable[["ConfigStats"], Any]]] = [
    ("fewest_marks", "fewest marks (contact area)", lambda s: round(s.contact_area_mm2, 4)),
    ("least_material", "least material (supports + raft)", lambda s: round(s.total_mass_g, 4)),
    ("fewest_issues", "fewest issues (errors, then warnings)", lambda s: (s.errors, s.warnings)),
]

DISCLAIMER = ("'Best' only means the lowest value of one measured criterion. It does not "
              "mean that a configuration prints better: check with a real print.")


@dataclass
class ConfigStats:
    label: str
    source: str = ""                       # profile / preset key, free text
    ok: bool = True
    failure: str = ""                      # why generate() failed (ok is False)
    contacts: int = 0
    model_contacts: int = 0
    model_feet: int = 0
    trees: int = 0
    support_volume_mm3: float = 0.0
    support_mass_g: float = 0.0
    raft_volume_mm3: float = 0.0
    total_mass_g: float = 0.0
    tip_area_mm2: float = 0.0
    model_foot_area_mm2: float = 0.0
    contact_area_mm2: float = 0.0
    max_tree_height_mm: float = 0.0
    max_slenderness: Optional[float] = None
    max_fragility: Optional[float] = None
    errors: int = 0
    warnings: int = 0
    errors_by_code: dict[str, int] = field(default_factory=dict)
    warnings_by_code: dict[str, int] = field(default_factory=dict)
    time_s: float = 0.0
    stats_source: str = "computed"         # "engine" when GenerationResult.stats was used

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompareReport:
    configs: list[ConfigStats]
    model: dict[str, Any] = field(default_factory=dict)   # name, size_mm, triangles...
    best: dict[str, list[str]] = field(default_factory=dict)

    # -- helpers -------------------------------------------------------------------
    @property
    def ok_configs(self) -> list[ConfigStats]:
        return [c for c in self.configs if c.ok]

    def compute_best(self) -> dict[str, list[str]]:
        """Labels of the configurations with the lowest value per criterion (ties: all)."""
        best: dict[str, list[str]] = {}
        ok = self.ok_configs
        for key, _, fn in CRITERIA:
            if not ok:
                best[key] = []
                continue
            low = min(fn(c) for c in ok)
            best[key] = [c.label for c in ok if fn(c) == low]
        self.best = best
        return best

    # -- serialisation -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"format": "arborfdm-compare", "version": 1,
                "pla_density_g_cm3": PLA_DENSITY_G_CM3, "model": _plain(self.model),
                "configs": [_plain(c.to_dict()) for c in self.configs],
                "best": {k: list(v) for k, v in self.best.items()},
                "best_labels": {k: lbl for k, lbl, _ in CRITERIA},
                "note": DISCLAIMER}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def _cells(self, c: ConfigStats) -> list[str]:
        if not c.ok:
            return [c.label] + ["failed"] + [""] * (len(COLUMNS) - 2)
        out = []
        for key, _, dec in COLUMNS:
            v = getattr(c, key)
            out.append("-" if v is None else f"{v:.{dec}f}" if dec is not None else str(v))
        return out

    def _best_lines(self) -> list[tuple[str, str]]:
        return [(lbl, ", ".join(self.best.get(k, [])) or "-") for k, lbl, _ in CRITERIA]

    def to_markdown(self) -> str:
        head = [h for _, h, _ in COLUMNS]
        lines = []
        if self.model.get("name"):
            lines.append(f"## Support comparison: {self.model['name']}\n")
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "|".join("---" if d is None and k == "label" else "---:"
                                    for k, _, d in COLUMNS) + "|")
        for c in self.configs:
            lines.append("| " + " | ".join(s.replace("|", "\\|") for s in self._cells(c)) + " |")
        lines.append("")
        for lbl, who in self._best_lines():
            lines.append(f"- **Best, {lbl}:** {who}")
        issues = [(c.label, c.errors_by_code, c.warnings_by_code) for c in self.configs
                  if c.errors_by_code or c.warnings_by_code]
        failed = [c for c in self.configs if not c.ok]
        if issues or failed:
            lines.append("")
            lines.append("Issues by code:")
            for lbl, e, w in issues:
                lines.append(f"- {lbl}: " + _codes_text(e, w))
            for c in failed:
                lines.append(f"- {c.label}: generation failed ({c.failure})")
        lines.append("")
        lines.append(f"*{DISCLAIMER} Contact area = tips + feet standing on the model (proxy "
                     f"of the marks); mass in PLA at {PLA_DENSITY_G_CM3} g/cm3.*")
        return "\n".join(lines) + "\n"

    def to_csv(self) -> str:
        buf = io.StringIO()
        keys = [k for k, _, _ in COLUMNS[:1]] + ["source", "ok", "failure"] + \
               [k for k, _, _ in COLUMNS[1:]] + ["raft_volume_mm3", "tip_area_mm2",
                                                 "model_foot_area_mm2", "model_feet",
                                                 "max_slenderness", "max_fragility",
                                                 "errors_by_code", "warnings_by_code",
                                                 "stats_source"] + \
               [f"best_{k}" for k, _, _ in CRITERIA]
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(keys)
        for c in self.configs:
            row = []
            for k in keys:
                if k.startswith("best_"):
                    row.append("1" if c.label in self.best.get(k[5:], []) else "0")
                    continue
                v = getattr(c, k)
                if isinstance(v, dict):
                    v = ";".join(f"{a}={b}" for a, b in sorted(v.items()))
                elif isinstance(v, bool):
                    v = "1" if v else "0"
                elif isinstance(v, float):
                    v = f"{v:.6g}"
                elif v is None:
                    v = ""
                row.append(v)
            w.writerow(row)
        return buf.getvalue()

    def to_text(self) -> str:
        """Fixed width table for a terminal."""
        head = [h for _, h, _ in COLUMNS]
        rows = [self._cells(c) for c in self.configs]
        widths = [max(len(head[i]), *(len(r[i]) for r in rows)) for i in range(len(head))]

        def fmt(cells):
            return "  ".join(cells[i].ljust(widths[i]) if i == 0 else cells[i].rjust(widths[i])
                             for i in range(len(cells))).rstrip()
        lines = [fmt(head), "  ".join("-" * w for w in widths)]
        lines += [fmt(r) for r in rows]
        lines.append("")
        lw = max(len(lbl) for _, lbl, _ in CRITERIA)
        for lbl, who in self._best_lines():
            lines.append(f"Best, {lbl:<{lw}} : {who}")
        for c in self.configs:
            if c.errors_by_code or c.warnings_by_code:
                lines.append(f"Issues {c.label}: " + _codes_text(c.errors_by_code, c.warnings_by_code))
            if not c.ok:
                lines.append(f"Failed {c.label}: {c.failure}")
        lines.append(DISCLAIMER)
        return "\n".join(lines)


def _codes_text(errors: dict[str, int], warnings: dict[str, int]) -> str:
    parts = [f"error {k} x{v}" for k, v in sorted(errors.items())]
    parts += [f"warning {k} x{v}" for k, v in sorted(warnings.items())]
    return ", ".join(parts) or "none"


def _plain(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


# ---------------------------------------------------------------------------
# measurements
# ---------------------------------------------------------------------------
def _mesh_volume(m) -> float:
    if m is None or len(getattr(m, "faces", [])) == 0:
        return 0.0
    try:
        return float(abs(m.volume))
    except Exception:
        return 0.0


def _circle(d: float) -> float:
    return math.pi * (float(d) / 2.0) ** 2


def _tree_heights(tree) -> float:
    """Highest node above the foot (root) of its tree, over all trees."""
    from arbor.engine.types import NodeKind
    nodes = tree.nodes
    best = 0.0
    root_cache: dict[int, int] = {}

    def root_of(i: int) -> int:
        path = []
        while i not in root_cache:
            n = nodes[i]
            if n.parent is None or n.parent not in nodes or len(path) > len(nodes):
                root_cache[i] = i
                break
            path.append(i)
            i = n.parent
        r = root_cache[i]
        for p in path:
            root_cache[p] = r
        return r

    for n in nodes.values():
        if n.kind != NodeKind.TIP:
            continue
        r = nodes[root_of(n.id)]
        best = max(best, float(n.position[2] - r.position[2]))
    return best


def measure(result, params, label: str, source: str = "", time_s: float = 0.0) -> ConfigStats:
    """ConfigStats of one GenerationResult (engine stats used when present)."""
    from arbor.engine.types import NodeKind
    active = [c for c in result.contacts if c.enabled]
    tstats = result.tree.stats()
    s = ConfigStats(label=label, source=source, time_s=float(time_s))
    s.contacts = len(active)
    s.trees = int(tstats.get("trees", 0))
    s.model_contacts = sum(1 for c in active if c.rests_on_model)
    s.support_volume_mm3 = _mesh_volume(result.support_mesh)
    s.raft_volume_mm3 = _mesh_volume(result.raft_mesh)
    s.tip_area_mm2 = sum(_circle(c.tip_diameter if c.tip_diameter else params.tip_diameter)
                         for c in active)
    pads = [n for n in result.tree.nodes.values() if n.kind == NodeKind.MODEL_FOOT]
    s.model_feet = len(pads)
    s.model_foot_area_mm2 = sum(math.pi * n.radius ** 2 for n in pads)
    s.contact_area_mm2 = s.tip_area_mm2 + s.model_foot_area_mm2
    s.max_tree_height_mm = _tree_heights(result.tree)
    s.support_mass_g = s.support_volume_mm3 * PLA_DENSITY_G_CM3 / 1000.0

    engine = getattr(result, "stats", None)
    if isinstance(engine, dict) and engine:
        used = False
        for key, attr, cast in (("support_volume_mm3", "support_volume_mm3", float),
                                ("support_mass_g", "support_mass_g", float),
                                ("contact_area_mm2", "contact_area_mm2", float),
                                ("raft_volume_mm3", "raft_volume_mm3", float),
                                ("model_feet", "model_feet", int),
                                ("max_tree_height_mm", "max_tree_height_mm", float),
                                ("max_slenderness", "max_slenderness", float),
                                ("max_fragility", "max_fragility", float)):
            v = engine.get(key)
            if v is None:
                continue
            try:
                v = cast(v)
            except (TypeError, ValueError):
                continue
            if isinstance(v, float) and not math.isfinite(v):
                continue
            setattr(s, attr, v)
            used = True
        if used:
            s.stats_source = "engine"
            if engine.get("support_mass_g") is None and engine.get("support_volume_mm3") is not None:
                s.support_mass_g = s.support_volume_mm3 * PLA_DENSITY_G_CM3 / 1000.0
    s.total_mass_g = s.support_mass_g + s.raft_volume_mm3 * PLA_DENSITY_G_CM3 / 1000.0

    for i in result.issues:
        if i.severity == "error":
            s.errors_by_code[i.code] = s.errors_by_code.get(i.code, 0) + 1
        elif i.severity == "warning":
            s.warnings_by_code[i.code] = s.warnings_by_code.get(i.code, 0) + 1
    s.errors = sum(s.errors_by_code.values())
    s.warnings = sum(s.warnings_by_code.values())
    return s


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _unique_labels(labels: Sequence[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for lbl in labels:
        lbl = str(lbl) or "config"
        if lbl in seen:
            seen[lbl] += 1
            lbl = f"{lbl} ({seen[lbl]})"
        else:
            seen[lbl] = 1
        out.append(lbl)
    return out


def compare_configs(mesh, configs: Sequence[tuple], progress=None,
                    model_info: Optional[dict] = None) -> CompareReport:
    """Run the engine once per (label, SupportParams) [or (label, SupportParams, source)]
    on a copy of ``mesh`` (already placed on the plate) and collect comparable figures.

    progress: an arbor.engine.types.Progress (each configuration gets an equal share);
    cancelling raises arbor.engine.types.Cancelled. A configuration whose parameters are
    rejected by the engine (ValueError) is reported as failed, the others still run.
    """
    from arbor.engine import pipeline
    from arbor.engine.types import Progress

    if not configs:
        raise ValueError("No configuration to compare")
    progress = progress or Progress()
    labels = _unique_labels([c[0] for c in configs])
    n = len(configs)
    stats: list[ConfigStats] = []
    for k, (cfg, label) in enumerate(zip(configs, labels)):
        params = cfg[1]
        source = str(cfg[2]) if len(cfg) > 2 else ""
        sub = progress.sub(k / n, (k + 1) / n)
        sub.update(0.0, f"Config {k + 1}/{n}: {label}")
        t = time.perf_counter()
        try:
            res = pipeline.generate(mesh.copy(), params, progress=sub)
        except ValueError as exc:
            stats.append(ConfigStats(label=label, source=source, ok=False, failure=str(exc),
                                     time_s=time.perf_counter() - t))
            continue
        stats.append(measure(res, params, label, source, time.perf_counter() - t))
    info = dict(model_info or {})
    info.setdefault("triangles", int(len(mesh.faces)))
    info.setdefault("size_mm", [float(v) for v in (mesh.bounds[1] - mesh.bounds[0])])
    rep = CompareReport(stats, info)
    rep.compute_best()
    progress.update(1.0, "Comparison done")
    return rep
