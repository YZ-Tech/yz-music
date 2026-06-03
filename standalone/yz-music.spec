# PyInstaller spec for the standalone music binaries — ONEFILE, PORTABLE.
#
# ONE spec, TWO outputs, toggled by the YZ_WEBVIEW env var at build time:
#   (unset)        -> yz-music-lite[.exe]  portable; opens the system browser
#   YZ_WEBVIEW=1   -> yz-music[.exe]       bundles pywebview; native window
#
# Same entry.py drives both: if pywebview is bundled, entry._serve shows a
# native window; if not, `import webview` ImportErrors and it falls back to the
# browser. build.py runs this spec once (lite) or twice (lite + windowed).
#
# Cross-platform — PyInstaller freezes the HOST interpreter (NOT a
# cross-compiler), so run build.py ON each target OS:
#   python standalone/build.py
#
# Windowed backend is per-OS (handled below): EdgeChromium/WebView2 via
# pythonnet on Windows, Cocoa/WKWebView via pyobjc on macOS. The Linux
# windowed build is intentionally NOT produced (GTK/Qt webkit is either a
# non-portable system dep or a ~150MB Chromium bundle) — Linux ships lite only.
#
# External binaries (mpv, yt-dlp) are NOT bundled — system-managed via the
# in-app Dependencies dialog (yz_music/dependencies.py).
#
# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

WITH_WEBVIEW = os.environ.get("YZ_WEBVIEW") == "1"
NAME = "yz-music" if WITH_WEBVIEW else "yz-music-lite"

# Bundle the SPA shipped inside the installed `music` package (static/**).
datas = collect_data_files("yz_music")
binaries = []
# uvicorn loads loop/protocol/lifespan impls dynamically; the import-string app
# target ("yz_music.server:app") also hides yz_music.server from static analysis.
# httpx is lazily imported by music/dependencies.py (the Dependencies tab).
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("yz_music")
    + ["register_win", "httpx"]
)
excludes = ["tkinter", "matplotlib", "PIL"]  # unused by the server path

if WITH_WEBVIEW:
    # pywebview itself + its platform packages; collect_all grabs the backend
    # modules PyInstaller's static analysis otherwise misses.
    wv_d, wv_b, wv_h = collect_all("webview")
    datas += wv_d
    binaries += wv_b
    hiddenimports += wv_h
    if sys.platform == "win32":
        # EdgeChromium (WebView2) backend via pythonnet (clr) — collect_all
        # grabs its platform modules + the pythonnet runtime DLLs.
        clr_d, clr_b, clr_h = collect_all("clr_loader")
        datas += clr_d
        binaries += clr_b
        hiddenimports += clr_h + ["pythonnet"]
    elif sys.platform == "darwin":
        # Cocoa (WKWebView) backend via pyobjc — WKWebView ships with macOS,
        # so nothing system-level to bundle; just the pyobjc framework modules.
        for _mod in ("objc", "Foundation", "WebKit", "AppKit"):
            try:
                _d, _b, _h = collect_all(_mod)
                datas += _d
                binaries += _b
                hiddenimports += _h
            except Exception:
                pass
        hiddenimports += ["webview.platforms.cocoa"]
else:
    # Belt-and-suspenders: keep the portable binary lean even if pywebview
    # leaked into the build venv — entry.py imports `webview` lazily, so static
    # analysis would otherwise try to pull it in.
    excludes += ["webview", "clr", "clr_loader", "pythonnet", "objc"]

a = Analysis(
    ["entry.py"],
    pathex=["."],               # so `import register_win` (this folder) resolves
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Lite (browser) keeps console=True — the terminal is its only feedback +
    # crash visibility. The windowed build (pywebview) sets console=False: the
    # native window IS the UI, so a console spawned next to it is pure noise.
    # Crashes still surface via the windowed-traceback dialog below.
    console=not WITH_WEBVIEW,
    disable_windowed_traceback=False,
    # .ico icon only applies on Windows; Linux/macOS PyInstaller ignore it and
    # the backslash path would be a bogus filename there, so gate by platform.
    icon=("../yz_music/static/favicon.ico" if sys.platform == "win32" else None),
)
