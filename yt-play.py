"""Backward-compat shim for the legacy `python yt-play.py <args>` invocation
path (Windows protocol handler bound to `mpv-yt://`, tampermonkey userscript,
direct CLI use). Delegates to the yz_music package.

Once the satellite is installed as a wheel, the `yt-play` console-script
on PATH (see pyproject.toml [project.scripts]) does the same thing without
needing this file. Kept here so an in-place clone of the satellite folder
still works exactly like the original /mnt/y/tools/yt-play/yt-play.py did."""
from __future__ import annotations

import sys

from yz_music.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
