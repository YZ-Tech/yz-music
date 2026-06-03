"""`python -m yz_music <args>` entry point.

Equivalent to `python yt-play.py <args>` against the standalone script —
exists so the package can be invoked without needing the wheel's
console_script wrapper on PATH."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
