"""Persistent settings — thin shim over yz_satellite_common.PersistentSettings.

Declares this satellite's sidecar location + mutable fields; the engine
(atomic writes, coercer-per-field, legacy migration) lives in the shared
wheel. Import-time load() keeps the original contract: consumers importing
the live `settings` object immediately see persisted state."""
from __future__ import annotations

import os
from pathlib import Path

from yz_satellite_common import PersistentSettings

from .settings import Settings, settings as _live


def _root() -> Path:
    """Where the satellite stores its state. Override via JWT_MUSIC_ROOT
    env; otherwise derives from JARVYZ_HOME (the single source of truth the
    core + every satellite share), default ~/.jarvyz."""
    env = os.environ.get("JWT_MUSIC_ROOT")
    if env:
        return Path(env)
    home = Path(os.environ.get("JARVYZ_HOME") or Path.home() / ".jarvyz")
    return home / "satellites" / "yz-music"


def _settings_path() -> Path:
    return _root() / "settings.json"


_engine = PersistentSettings(
    _live,
    tag="music",
    path=_settings_path,
    fields={
        "library_path": lambda v: Path(str(v)).expanduser(),
        "audio_only": bool,
        "audio_delay_ms": int,
        "fallback_video_ids": lambda v: [str(x) for x in v] if isinstance(v, list) else (_ for _ in ()).throw(ValueError()),
        "fallback_loop": bool,
    },
)

MUTABLE_KEYS = _engine.mutable_keys
load = _engine.load
save = _engine.save
apply_patch = _engine.apply_patch

# Read on module import so any consumer that imports `settings` immediately
# sees persisted state.
load()
