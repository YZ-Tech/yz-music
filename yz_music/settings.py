"""Satellite-owned settings (Phase 2 minimal).

The Music page in JarvYZ used to read `library_path`, `audio_only`,
`audio_delay_ms`, `fallback_video_ids`, `fallback_loop` from JarvYZ's
own `settings.media`. In the post-migration world those move HERE — the
satellite owns playback state, JarvYZ is a thin client.

This module is currently a no-op shape definition. Phase 4 (JarvYZ-side
adapter) will wire actual persistence (JSON at <root>/settings.json,
PATCH endpoint, etc.) — same pattern as wakeword-trainer's settings.py.
For Phase 2 we just need defaults so the server can answer requests
without crashing."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


_IS_WIN = sys.platform == "win32"


def _default_library() -> Path:
    """Platform-appropriate default library root. Override via the
    `MUSIC_LIBRARY` env var or per-request via `--library <path>`."""
    env = os.environ.get("MUSIC_LIBRARY")
    if env:
        return Path(env)
    return Path(r"D:\Media\YouTube") if _IS_WIN else Path.home() / "Media" / "YouTube"


@dataclass
class Settings:
    """Snapshot of mutable satellite settings. Created at startup; the
    server PATCH endpoint (Phase 4) will mutate this in-place + persist."""
    library_path: Path = field(default_factory=_default_library)
    audio_only: bool = field(default_factory=lambda: not _IS_WIN)
    audio_delay_ms: int = 0
    fallback_video_ids: list[str] = field(default_factory=list)
    fallback_loop: bool = True


# Module singleton. Server boot will replace this with a persisted-from-
# disk version in Phase 4; for now the defaults are honest.
settings = Settings()
