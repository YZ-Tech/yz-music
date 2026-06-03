"""Load + persist satellite settings to disk.

Mirrors the wakeword-trainer satellite's persistent_settings.py pattern:
on import, read <root>/settings.json into the module-level `settings`
dataclass. PATCH /settings (server.py) mutates the dataclass in-place and
calls save().

Why a JSON sidecar (not a pydantic Settings class with .dump()):
  - Keeps the dataclass shape minimal + free of pydantic dep coupling on
    the CLI hot path (CLI imports settings; we don't want CLI startup to
    pull pydantic just to read a library path)
  - Easy to hand-edit + diff."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .settings import Settings, settings as _live


def _root() -> Path:
    """Where the satellite stores its data. Override via JWT_MUSIC_ROOT
    env; otherwise derives from JARVYZ_HOME (the single source of truth the
    core + every satellite share), default ~/.jarvyz."""
    env = os.environ.get("JWT_MUSIC_ROOT")
    if env:
        return Path(env)
    home = Path(os.environ.get("JARVYZ_HOME") or Path.home() / ".jarvyz")
    return home / "satellites" / "yz-music"


def _settings_path() -> Path:
    return _root() / "settings.json"


# Keys that can be mutated via PATCH /settings (others ignored). Same
# allow-list pattern as wakeword-trainer/persistent_settings.MUTABLE_KEYS.
MUTABLE_KEYS = (
    "library_path",
    "audio_only",
    "audio_delay_ms",
    "fallback_video_ids",
    "fallback_loop",
)


def load() -> None:
    """Read settings.json into the live dataclass. No-op if file missing
    (defaults stand). Soft-fail on parse errors."""
    p = _settings_path()
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[music] settings.json parse failed: {e}", file=sys.stderr)
        return
    if "library_path" in data:
        _live.library_path = Path(str(data["library_path"]))
    if "audio_only" in data:
        _live.audio_only = bool(data["audio_only"])
    if "audio_delay_ms" in data:
        try: _live.audio_delay_ms = int(data["audio_delay_ms"])
        except (TypeError, ValueError): pass
    if "fallback_video_ids" in data and isinstance(data["fallback_video_ids"], list):
        _live.fallback_video_ids = [str(x) for x in data["fallback_video_ids"]]
    if "fallback_loop" in data:
        _live.fallback_loop = bool(data["fallback_loop"])


def save() -> None:
    """Persist the live dataclass to settings.json. Atomic via tmp+rename."""
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "library_path": str(_live.library_path),
        "audio_only": _live.audio_only,
        "audio_delay_ms": _live.audio_delay_ms,
        "fallback_video_ids": list(_live.fallback_video_ids),
        "fallback_loop": _live.fallback_loop,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)


def apply_patch(patch: dict) -> Settings:
    """Validate + apply a PATCH /settings body. Returns the post-merge
    snapshot (the live dataclass). Unknown keys are dropped silently;
    known keys are coerced into the field type."""
    if "library_path" in patch:
        _live.library_path = Path(str(patch["library_path"])).expanduser()
    if "audio_only" in patch:
        _live.audio_only = bool(patch["audio_only"])
    if "audio_delay_ms" in patch:
        try: _live.audio_delay_ms = int(patch["audio_delay_ms"])
        except (TypeError, ValueError): pass
    if "fallback_video_ids" in patch and isinstance(patch["fallback_video_ids"], list):
        _live.fallback_video_ids = [str(x) for x in patch["fallback_video_ids"]]
    if "fallback_loop" in patch:
        _live.fallback_loop = bool(patch["fallback_loop"])
    save()
    return _live


# Read on module import so any consumer (server.py, cli.py) that imports
# the `settings` singleton sees persisted state immediately.
load()
