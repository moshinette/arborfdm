"""User presets: named parameter sets saved by the user, next to the built-in profiles.

A user preset is stored as one JSON file in a writable user folder (the built-in
profiles/ folder is read-only once the application is frozen):

  * Windows: %APPDATA%\\ArborFDM\\presets
  * elsewhere: $XDG_CONFIG_HOME/arborfdm/presets (default ~/.config/arborfdm/presets)
  * ARBOR_USER_PRESET_DIR overrides both (tests, portable installs).

File format (same for the stored file and for export / import)::

    {"format": "arborfdm-preset", "version": 2, "label": "My figurines",
     "description": "", "inherits": "nozzle_04_normal",
     "values": {"overhang_angle_deg": 50.0}}

Only the values that differ from the built-in profile it was started from are stored.
Every file is validated (types, unknown names, SupportParams.validate) when it is loaded
or imported; an invalid file raises PresetError with a readable message, never another
exception. Built-in profiles are never written. No Qt here: this module is testable alone.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from arbor.engine.params import Profile, SupportParams

PRESET_FORMAT = "arborfdm-preset"
PRESET_VERSION = 2
# Format 1 (before v0.2): built-in profiles had the raft off. A format 1 preset that does
# not mention the raft keeps it off; one that turned it on keeps the raft under the feet
# (raft_coverage did not exist).
LEGACY_V1_VALUES = {"raft_enabled": False}
USER_PREFIX = "user:"
ENV_DIR = "ARBOR_USER_PRESET_DIR"
MAX_FILE_BYTES = 1_000_000
MAX_LABEL = 80

_FIELD_TYPES = {f.name: type(getattr(SupportParams(), f.name)) for f in fields(SupportParams)}


class PresetError(Exception):
    """A preset file or request is invalid; str(exc) is meant for the user."""


def is_user_key(key: Optional[str]) -> bool:
    return bool(key) and str(key).startswith(USER_PREFIX)


def user_preset_dir() -> Path:
    env = os.environ.get(ENV_DIR, "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ArborFDM" / "presets"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "arborfdm" / "presets"


def slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "_", label.strip().lower()).strip("_-")
    return (s or "preset")[:60]


def clean_label(label: Any) -> str:
    if not isinstance(label, str):
        raise PresetError("The preset name must be text")
    s = " ".join(label.split())
    if not s:
        raise PresetError("The preset name is empty")
    if len(s) > MAX_LABEL:
        raise PresetError(f"The preset name is too long (max {MAX_LABEL} characters)")
    return s


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-9
        except (TypeError, ValueError):
            return False
    return a == b


def diff_values(params: SupportParams, base: SupportParams) -> dict[str, Any]:
    """Values of params that differ from base (what a preset stores)."""
    b = base.to_dict()
    return {k: v for k, v in params.to_dict().items() if not _close(v, b[k])}


def coerce_values(values: Any) -> dict[str, Any]:
    """Check names and types of a 'values' mapping; ints are accepted for float fields."""
    if not isinstance(values, dict):
        raise PresetError("'values' must be an object of parameter: value")
    unknown = sorted(set(values) - set(_FIELD_TYPES))
    if unknown:
        raise PresetError("Unknown parameter(s): " + ", ".join(map(str, unknown)))
    out: dict[str, Any] = {}
    bad = []
    for k, v in values.items():
        t = _FIELD_TYPES[k]
        if t is bool:
            ok = isinstance(v, bool)
        elif t is int:
            ok = (isinstance(v, int) and not isinstance(v, bool)) or (
                isinstance(v, float) and math.isfinite(v) and v == int(v))
            v = int(v) if ok else v
        elif t is float:
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            v = float(v) if ok else v
        else:
            ok = isinstance(v, str)
        if not ok:
            bad.append(f"{k} = {v!r} (expected {t.__name__})")
        out[k] = v
    if bad:
        raise PresetError("Wrong value type: " + "; ".join(bad))
    return out


@dataclass
class UserPreset:
    key: str
    label: str
    inherits: Optional[str]
    values: dict[str, Any]
    params: SupportParams
    description: str = ""
    path: Optional[Path] = None

    def to_profile(self) -> Profile:
        return Profile(self.key, self.label, self.description, self.params)

    def to_dict(self) -> dict[str, Any]:
        return {"format": PRESET_FORMAT, "version": PRESET_VERSION, "label": self.label,
                "description": self.description, "inherits": self.inherits,
                "values": dict(sorted(self.values.items()))}


def parse_preset(data: Any, builtin: dict[str, Profile], source: str = "preset") -> dict:
    """Validate a decoded preset. Returns {label, description, inherits, values, params}."""
    if not isinstance(data, dict):
        raise PresetError(f"{source}: not a preset (a JSON object is expected)")
    fmt = data.get("format", PRESET_FORMAT)
    if fmt != PRESET_FORMAT:
        raise PresetError(f"{source}: not an ArborFDM preset (format {fmt!r})")
    try:
        version = int(data.get("version", PRESET_VERSION))
    except (TypeError, ValueError):
        raise PresetError(f"{source}: invalid version {data.get('version')!r}") from None
    if version > PRESET_VERSION:
        raise PresetError(f"{source}: written by a newer ArborFDM (preset format {version})")
    if "values" not in data:
        raise PresetError(f"{source}: no 'values' in the file")
    try:
        values = coerce_values(data.get("values"))
        if version < 2:
            values = {**LEGACY_V1_VALUES, **values}
            if values.get("raft_enabled") and "raft_coverage" not in values:
                values["raft_coverage"] = "feet"
        label = clean_label(data.get("label") or Path(source).stem)
    except PresetError as exc:
        raise PresetError(f"{source}: {exc}") from None
    desc = data.get("description") or ""
    if not isinstance(desc, str):
        raise PresetError(f"{source}: 'description' must be text")
    inherits = data.get("inherits") or None
    if inherits is not None:
        if not isinstance(inherits, str):
            raise PresetError(f"{source}: 'inherits' must be a profile name")
        if is_user_key(inherits) or inherits not in builtin:
            raise PresetError(f"{source}: base profile {inherits!r} is not a built-in profile "
                              "of this version (available: " + ", ".join(sorted(builtin)) + ")")
        base = builtin[inherits].params.to_dict()
    else:
        base = SupportParams().to_dict()
    try:
        params = SupportParams.from_dict({**base, **values})
        problems = params.validate()
    except Exception as exc:          # defensive: never let a bad file crash the caller
        raise PresetError(f"{source}: invalid values ({exc})") from None
    if problems:
        raise PresetError(f"{source}: invalid parameters: " + "; ".join(problems))
    return {"label": label, "description": desc, "inherits": inherits, "values": values,
            "params": params}


def read_preset_file(path, builtin: dict[str, Profile]) -> dict:
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise PresetError(f"{path.name}: file too large for a preset ({size} bytes)")
        text = path.read_bytes().decode("utf-8-sig")
    except PresetError:
        raise
    except UnicodeDecodeError:
        raise PresetError(f"{path.name}: not a text (UTF-8) file") from None
    except OSError as exc:
        raise PresetError(f"{path.name}: cannot read the file ({exc.strerror or exc})") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PresetError(f"{path.name}: invalid JSON (line {exc.lineno}, column {exc.colno}: "
                          f"{exc.msg})") from None
    return parse_preset(data, builtin, path.name)


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise PresetError(f"Cannot write {path}: {exc.strerror or exc}") from None


def load_profiles_from_dir(directory, builtin: dict[str, Profile]
                           ) -> tuple[dict[str, Profile], list[tuple[Path, str]]]:
    """Profiles of the user presets in a folder (keys 'user:<file stem>') and the files
    that were skipped with the reason."""
    store = PresetStore(builtin, directory)
    return {k: p.to_profile() for k, p in store.presets.items()}, store.errors


class PresetStore:
    """The user presets of one folder. Built-in profiles are only read."""

    def __init__(self, builtin: dict[str, Profile], directory=None) -> None:
        self.builtin = {k: v for k, v in builtin.items() if not is_user_key(k)}
        self.directory = Path(directory) if directory is not None else user_preset_dir()
        self.presets: dict[str, UserPreset] = {}
        self.errors: list[tuple[Path, str]] = []
        self.reload()

    # -- reading -------------------------------------------------------------------
    def reload(self) -> list[tuple[Path, str]]:
        self.presets, self.errors = {}, []
        try:
            files = sorted(self.directory.glob("*.json")) if self.directory.is_dir() else []
        except OSError as exc:
            self.errors.append((self.directory, f"cannot list the folder ({exc})"))
            files = []
        for f in files:
            try:
                d = read_preset_file(f, self.builtin)
            except PresetError as exc:
                self.errors.append((f, str(exc)))
                continue
            if self.find_by_label(d["label"]) is not None:
                self.errors.append((f, f"{f.name}: duplicate preset name {d['label']!r}"))
                continue
            key = USER_PREFIX + f.stem
            self.presets[key] = UserPreset(key, d["label"], d["inherits"], d["values"],
                                           d["params"], d["description"], f)
        return self.errors

    def ordered(self) -> list[UserPreset]:
        return sorted(self.presets.values(), key=lambda p: p.label.lower())

    def profiles(self) -> dict[str, Profile]:
        """Built-in profiles followed by the user presets (sorted by name)."""
        out = dict(self.builtin)
        out.update((p.key, p.to_profile()) for p in self.ordered())
        return out

    def get(self, key: str) -> UserPreset:
        if key not in self.presets:
            raise PresetError(f"No user preset {key!r}")
        return self.presets[key]

    def find_by_label(self, label: str) -> Optional[UserPreset]:
        low = " ".join(str(label).split()).lower()
        return next((p for p in self.presets.values() if p.label.lower() == low), None)

    def base_key(self, key: Optional[str]) -> Optional[str]:
        """Built-in profile a key is based on (itself for a built-in profile)."""
        if is_user_key(key):
            p = self.presets.get(key)
            return p.inherits if p is not None else None
        return key if key in self.builtin else None

    def base_params(self, base_key: Optional[str]) -> SupportParams:
        return self.builtin[base_key].params if base_key in self.builtin else SupportParams()

    # -- writing -------------------------------------------------------------------
    def _free_path(self, label: str, keep: Optional[Path] = None) -> Path:
        slug = slugify(label)
        path, n = self.directory / f"{slug}.json", 2
        while path.exists() and path != keep:
            path, n = self.directory / f"{slug}_{n}.json", n + 1
        return path

    def _make(self, label: str, params: SupportParams, base_key: Optional[str],
              description: str, source: str) -> dict:
        if is_user_key(base_key):
            base_key = self.base_key(base_key)
        if base_key is not None and base_key not in self.builtin:
            base_key = None
        values = diff_values(params, self.base_params(base_key))
        return parse_preset({"label": label, "description": description,
                             "inherits": base_key, "values": values}, self.builtin, source)

    def save(self, label: str, params: SupportParams, base_key: Optional[str],
             overwrite: bool = False, description: str = "") -> UserPreset:
        """Save params as a preset named label, based on base_key (a built-in profile, or a
        user preset whose own base is then used). Raises PresetError if the name exists
        and overwrite is False, or if the parameters are invalid."""
        label = clean_label(label)
        existing = self.find_by_label(label)
        if existing is not None and not overwrite:
            raise PresetError(f"A preset named {existing.label!r} already exists")
        d = self._make(label, params, base_key, description, label)
        path = existing.path if existing is not None and existing.path else self._free_path(label)
        key = USER_PREFIX + path.stem
        preset = UserPreset(key, d["label"], d["inherits"], d["values"], d["params"],
                            d["description"], path)
        _write_json(path, preset.to_dict())
        self.presets[key] = preset
        return preset

    def delete(self, key: str) -> None:
        p = self.get(key)
        try:
            if p.path is not None and p.path.exists():
                p.path.unlink()
        except OSError as exc:
            raise PresetError(f"Cannot delete {p.path}: {exc.strerror or exc}") from None
        del self.presets[key]

    def rename(self, key: str, new_label: str) -> UserPreset:
        """Rename a preset (its file is renamed too). Returns the preset with its new key."""
        p = self.get(key)
        label = clean_label(new_label)
        other = self.find_by_label(label)
        if other is not None and other.key != key:
            raise PresetError(f"A preset named {other.label!r} already exists")
        new_path = self._free_path(label, keep=p.path)
        new = UserPreset(USER_PREFIX + new_path.stem, label, p.inherits, dict(p.values),
                         p.params, p.description, new_path)
        _write_json(new_path, new.to_dict())
        if p.path is not None and p.path != new_path:
            try:
                p.path.unlink()
            except OSError:
                pass
        del self.presets[key]
        self.presets[new.key] = new
        return new

    def export(self, key: str, path) -> Path:
        path = Path(path)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        _write_json(path, self.get(key).to_dict())
        return path

    def import_file(self, path) -> UserPreset:
        """Validate a preset file and add it as a user preset (a name already used gets a
        ' (2)' suffix). Raises PresetError with a readable message."""
        d = read_preset_file(path, self.builtin)
        label, n = d["label"], 2
        while self.find_by_label(label) is not None:
            label = clean_label(f"{d['label'][:MAX_LABEL - 6]} ({n})")
            n += 1
        return self.save(label, d["params"], d["inherits"], description=d["description"])
