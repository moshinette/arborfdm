"""User settings (application preferences), stored as JSON in the user folder.

Same folder as the user presets (see arbor.core.presets):

  * Windows: %APPDATA%\\ArborFDM\\settings.json
  * elsewhere: $XDG_CONFIG_HOME/arborfdm/settings.json (default ~/.config/arborfdm)
  * ARBOR_USER_CONFIG_DIR overrides the folder (tests, portable installs).

Known keys (others are kept as they are, so that a newer version's settings survive):

  * ``orca_path``: path of the OrcaSlicer executable (or ``OrcaSlicer.app`` on macOS)
    chosen by the user; ``None`` = automatic detection.

:func:`load_settings` never raises: a missing, unreadable or corrupted file gives the
defaults and a :class:`SettingsWarning` (``warnings.warn``) with a readable message.
:func:`save_settings` writes atomically (temporary file + ``os.replace``) and raises
:class:`SettingsError` with a readable message if the folder is not writable.
No Qt here: this module is testable alone.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any

ENV_DIR = "ARBOR_USER_CONFIG_DIR"
SETTINGS_FILE = "settings.json"
SETTINGS_FORMAT = "arborfdm-settings"
SETTINGS_VERSION = 1
MAX_FILE_BYTES = 1_000_000

DEFAULTS: dict[str, Any] = {"orca_path": None}


class SettingsError(Exception):
    """Settings could not be saved; str(exc) is meant for the user."""


class SettingsWarning(UserWarning):
    """The settings file was ignored (unreadable / corrupted); defaults are used."""


def user_config_dir() -> Path:
    env = os.environ.get(ENV_DIR, "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ArborFDM"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "arborfdm"


def settings_path() -> Path:
    return user_config_dir() / SETTINGS_FILE


def _clean(data: dict, source: str) -> dict:
    """Defaults + valid known values + unknown keys kept; bad known values dropped."""
    out = dict(DEFAULTS)
    for k, v in data.items():
        if k in ("format", "version"):
            continue
        if k == "orca_path":
            if v is None or (isinstance(v, str) and v.strip()):
                out[k] = v.strip() if isinstance(v, str) else None
            else:
                warnings.warn(f"{source}: ignored invalid orca_path {v!r}", SettingsWarning,
                              stacklevel=3)
            continue
        out[k] = v
    return out


def load_settings() -> dict:
    """Settings as a dict (always contains every key of DEFAULTS). Never raises."""
    path = settings_path()
    try:
        if not path.exists():
            return dict(DEFAULTS)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        warnings.warn(f"Settings file {path} ignored ({exc}); default settings are used",
                      SettingsWarning, stacklevel=2)
        return dict(DEFAULTS)
    return _clean(data, str(path))


def save_settings(settings: dict) -> Path:
    """Write ``settings`` atomically; returns the file path. Raises SettingsError."""
    if not isinstance(settings, dict):
        raise SettingsError("settings must be a dict")
    data = {"format": SETTINGS_FORMAT, "version": SETTINGS_VERSION}
    data.update({k: v for k, v in settings.items() if k not in ("format", "version")})
    if isinstance(data.get("orca_path"), Path):
        data["orca_path"] = str(data["orca_path"])
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"settings cannot be saved as JSON: {exc}") from None
    path = settings_path()
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        raise SettingsError(f"cannot write the settings file {path}: {exc}") from None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return path


def update_settings(**changes: Any) -> dict:
    """Load, apply ``changes``, save; returns the new settings. Raises SettingsError."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SettingsWarning)
        s = load_settings()
    s.update(changes)
    save_settings(s)
    return s
