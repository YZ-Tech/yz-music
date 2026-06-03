"""music — voice + UI controlled YouTube downloader + mpv player.

Package layout (Phase 1 — copied from /mnt/y/tools/yt-play/ to seed the
satellite migration):

  yz_music.cli         — the legacy CLI (argv parser → download → mpv launch + IPC).
                      Verbatim from the standalone yt-play.py, now an importable
                      module. Entry point for tampermonkey + Windows protocol
                      handler invocations.

Future phases will split cli.py into focused modules (library, download,
ipc, launcher, observer) and add a FastAPI server.py for the daemon-mode
satellite. Until then, this package is a one-file wrapper that preserves
every behavior the original script had.

Naming: the satellite is `music` (user-altitude), the CLI shipped on PATH
is `yt-play` (preserves the existing Windows protocol-handler + tampermonkey
bindings — see pyproject.toml [project.scripts])."""

from .cli import main

__all__ = ["main"]
__version__ = "0.1.0"
