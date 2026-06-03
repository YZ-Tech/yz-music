#!/usr/bin/env python3
"""Cross-platform build driver for the standalone yz-music binaries.

Replaces build.ps1. Runs on Windows / macOS / Linux. PyInstaller freezes the
HOST interpreter (it is NOT a cross-compiler), so run this ON each target OS —
the CI matrix calls the exact same command on every runner.

Outputs land in satellites/yz-music/dist/:

  Windows   yz-music-lite.exe   (browser)   +  yz-music.exe   (WebView2 window)
  macOS     yz-music-lite       (browser)   +  yz-music       (WKWebView window)
  Linux     yz-music-lite       (browser)                     [windowed skipped]

Linux windowed is deliberately not built: pywebview's GTK backend needs system
webkit2gtk on the user's box (not portable) and the Qt backend bundles a
~150 MB Chromium. Lite (system browser) is the Linux story.

A throwaway venv is used so the project's own .venv is never touched. The
yz-music wheel is taken from --wheel, else the newest match in
backend/local-index/, and the SPA must already be built into the wheel
(scripts/build-all-wheels.sh handles `npm run build:pages` before `uv build`).

Usage:
  python standalone/build.py                 # current OS, both variants where supported
  python standalone/build.py --lite-only     # skip the windowed build
  python standalone/build.py --wheel PATH     # use a specific wheel
  python standalone/build.py --keep-venv      # don't delete the throwaway venv
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent            # satellites/yz-music/standalone
MUSIC_DIR = HERE.parent                            # satellites/yz-music
REPO_ROOT = MUSIC_DIR.parent.parent                # repo root
LOCAL_INDEX = REPO_ROOT / "backend" / "local-index"
SPEC = HERE / "yz-music.spec"
DIST = MUSIC_DIR / "dist"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
EXE = ".exe" if IS_WIN else ""
# Windowed builds only where pywebview has a zero-system-dep backend.
WINDOWED_SUPPORTED = IS_WIN or IS_MAC


def find_wheel(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).resolve()
        if not p.is_file():
            sys.exit(f"wheel not found: {p}")
        return p
    matches = sorted(glob.glob(str(LOCAL_INDEX / "yz_music-*.whl")))
    if not matches:
        sys.exit(
            f"no yz_music wheel in {LOCAL_INDEX}.\n"
            f"build it first:  bash backend/scripts/build-all-wheels.sh\n"
            f"or pass         --wheel PATH"
        )
    return Path(matches[-1])


def venv_python(venv: Path) -> str:
    return str(venv / ("Scripts" if IS_WIN else "bin") / ("python.exe" if IS_WIN else "python"))


def run(cmd: list[str], *, env: dict | None = None) -> None:
    print(f"   $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=env)


# Prefer uv (fast, ships its own pip, no python3-venv dependency); fall back to
# the stdlib venv + pip when uv isn't on PATH (e.g. a bare CI runner).
HAVE_UV = shutil.which("uv") is not None


def make_venv(venv: Path) -> str:
    if HAVE_UV:
        run(["uv", "venv", "--python", "3.12", str(venv)])
    else:
        run([sys.executable, "-m", "venv", str(venv)])
    return venv_python(venv)


def pip_install(py: str, *pkgs: str) -> None:
    # uv's `--upgrade pip` is a no-op (uv has no managed pip); strip it.
    pkgs = tuple(p for p in pkgs if not (HAVE_UV and p in ("--upgrade", "pip")))
    if not pkgs:
        return
    if HAVE_UV:
        run(["uv", "pip", "install", "--python", py, *pkgs])
    else:
        run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *pkgs])


def freeze(py: str, *, windowed: bool, workname: str) -> Path:
    env = dict(os.environ)
    if windowed:
        env["YZ_WEBVIEW"] = "1"
    else:
        env.pop("YZ_WEBVIEW", None)
    # Run from MUSIC_DIR so the spec's relative paths (entry.py, pathex='.',
    # icon, dist/) resolve regardless of where build.py was invoked.
    run(
        [py, "-m", "PyInstaller", str(SPEC), "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(MUSIC_DIR / "build" / workname)],
        env=env,
    )
    name = ("yz-music" if windowed else "yz-music-lite") + EXE
    out = DIST / name
    if not out.is_file():
        sys.exit(f"expected output missing: {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the standalone yz-music binaries.")
    ap.add_argument("--wheel", help="path to the yz_music wheel (default: newest in backend/local-index)")
    ap.add_argument("--lite-only", action="store_true", help="skip the windowed build")
    ap.add_argument("--keep-venv", action="store_true", help="keep the throwaway build venv")
    args = ap.parse_args()

    wheel = find_wheel(args.wheel)
    do_windowed = WINDOWED_SUPPORTED and not args.lite_only

    print(f"==> Platform: {sys.platform}   wheel: {wheel.name}")
    print(f"==> Variants: lite{' + windowed' if do_windowed else ''}"
          f"{'  (windowed unsupported on this OS)' if not WINDOWED_SUPPORTED and not args.lite_only else ''}")

    venv = Path(tempfile.gettempdir()) / "yzmusic-build-venv"
    if venv.exists():
        shutil.rmtree(venv)
    print(f"==> Throwaway build venv: {venv}  ({'uv' if HAVE_UV else 'stdlib venv'})")
    py = make_venv(venv)

    pip_install(py, "--upgrade", "pip")
    print("==> Installing PyInstaller + yz-music wheel")
    pip_install(py, "pyinstaller", str(wheel))

    built: list[Path] = []

    # Lite FIRST — before pywebview is in the venv, so entry.py's lazy
    # `import webview` is a clean miss and the browser fallback is baked in.
    print("==> [lite] freezing browser build")
    built.append(freeze(py, windowed=False, workname="pyi-lite"))

    if do_windowed:
        print("==> Installing pywebview backend for the windowed build")
        if IS_WIN:
            pip_install(py, "pywebview", "pythonnet")
        elif IS_MAC:
            pip_install(py, "pywebview", "pyobjc-core", "pyobjc-framework-Cocoa", "pyobjc-framework-WebKit")
        print("==> [windowed] freezing native-window build")
        built.append(freeze(py, windowed=True, workname="pyi-win"))

    if not args.keep_venv:
        shutil.rmtree(venv, ignore_errors=True)

    print()
    for p in built:
        size_mb = round(p.stat().st_size / (1024 * 1024), 1)
        print(f"==> DONE: {p}  ({size_mb} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
