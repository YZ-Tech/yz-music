# standalone music binaries — portable builds

For people who want **just** the YouTube downloader + mpv player UI — no JarvYZ,
no Python install, no setup wizard. Two flavors, same codebase, frozen per-OS:

| Variant | Behavior | Windows | macOS | Linux |
|---|---|:--:|:--:|:--:|
| `yz-music[.exe]` | server + **native window** (pywebview) | WebView2 | WKWebView | — |
| `yz-music-lite[.exe]` | server + **system browser** | yes | yes | yes |

Each is a single portable file. The windowed build feels like a real app (own
title bar + taskbar/dock icon, no address bar, close = clean shutdown); the
`-lite` build has zero extra deps and just opens your browser.

**Linux ships lite only — by design.** pywebview's Linux backends are either a
non-portable system dep (GTK needs `libwebkit2gtk` on the user's box) or a
~150 MB Chromium bundle (Qt). Neither fits a "portable one file," so the system
browser is the Linux story.

This is a **second shell on the same package**: `pip install yz-music` (inside
the JarvYZ runtime) and `yz-music.exe` (frozen standalone) are the same code,
two faces.

## Why portable (not a setup installer)

- **Pure-Python deps** (`fastapi`/`uvicorn`/`pydantic`) — no torch/CUDA — so a
  PyInstaller onefile freezes clean and small.
- **External binaries** (`mpv`, `yt-dlp`) stay system-managed via the in-app
  Dependencies dialog (`yz_music/dependencies.py`) — a setup wouldn't install them
  either, so it buys nothing.
- The one classic reason for a setup — **registry protocol handlers** — is
  HKCU-scoped (no admin), so the exe **self-registers**: `yz-music.exe --register`.

## Build

PyInstaller freezes the host interpreter — it is **not** a cross-compiler, so
run the build **on each target OS**. One cross-platform driver:

```bash
python standalone/build.py              # current OS: lite + windowed (where supported)
python standalone/build.py --lite-only  # skip the windowed build
python standalone/build.py --wheel PATH # use a specific wheel
```

It makes a throwaway build venv (prefers `uv`, falls back to stdlib `venv`;
never touches the project `.venv`), installs the `yz_music` wheel + PyInstaller,
and runs `yz-music.spec` once (lite) or twice (lite + windowed). Lite is frozen
**first**, before pywebview is in the venv, so its lazy `import webview` is a
clean miss → browser fallback baked in. The windowed pass then adds the per-OS
backend (`pythonnet` on Windows, `pyobjc` on macOS) and freezes `yz-music`.

The wheel comes from `--wheel`, else the newest `yz_music-*.whl` in
`backend/local-index/` (build it with `bash backend/scripts/build-all-wheels.sh`).

### Releases (CI)

`.github/workflows/release.yml` (in the satellite root) builds the full matrix —
Windows + macOS (both variants) and Linux (lite) — plus the dynamic-module
`yz-music.iife.js`, its `manifest.json`, and the wheel, and attaches them all to
a GitHub Release. It is inert in the JarvYZ monorepo and activates once this
satellite is split into its own repo. Trigger: a push whose commit message
starts with `Release ` (or manual dispatch). Version flows from `pyproject.toml`.

## Run

`<exe>` = either `yz-music.exe` (native window) or `yz-music-lite.exe` (browser).

| Invocation | Mode |
|---|---|
| `<exe>` | server + native window **or** browser (double-click default) |
| `yz-music.exe --browser` | force the browser even in the windowed build |
| `<exe> "mpv-yt://<url>"` | play now (protocol/CLI) |
| `<exe> "mpv-yt-n://<url>"` | play next |
| `<exe> "mpv-yt-q://<url>"` | queue |
| `<exe> "mpv-yt-d://<ms>"` | set audio-delay |
| `<exe> --register` | wire the 4 `mpv-yt*://` protocols → this exe (HKCU) |
| `<exe> --unregister` | remove them |

After `--register`, the browser userscript's `mpv-yt://` handoff launches the
registered exe directly (replaces the old `register.ps1` → `C:\Python314\python.exe yt-play.py`).

## Files

- `entry.py` — multi-mode dispatcher (server[window|browser] / protocol-CLI / register).
- `register_win.py` — HKCU protocol registration, pointed at `sys.executable`.
- `yz-music.spec` — PyInstaller onefile spec; `YZ_WEBVIEW=1` toggles the pywebview
  bundle (and names it `yz-music`; unset → `yz-music-lite`). Per-OS windowed
  backend (pythonnet/Windows, pyobjc/macOS). Bundles `yz_music/static`; icon on
  Windows only.
- `build.py` — cross-platform build driver (lite everywhere + windowed on
  Windows/macOS). Replaces the old `build.ps1`.
