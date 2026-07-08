<!-- ─────────────────────────── JARVYZ SATELLITE ─────────────────────────── -->

# music


[![JarvYZ](https://img.shields.io/badge/JARVYZ-Satellite-blue.svg?logoColor=white)](../../README.md)
[![Version](https://img.shields.io/badge/VERSION-0.0.5-blue.svg?logo=git&logoColor=white)](pyproject.toml)
[![Python](https://img.shields.io/badge/PYTHON-3.10–3.12-blue.svg?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/LICENSE-MIT-blue.svg?logo=opensourceinitiative&logoColor=white)](pyproject.toml)
[![Kind](https://img.shields.io/badge/KIND-service%20%2B%20CLI-blue.svg?logoColor=white)](#)
[![Port](https://img.shields.io/badge/PORT-9002-blue.svg?logoColor=white)](#)
[![Creator](https://img.shields.io/badge/CREATOR-Yeon-blue.svg?logo=github&logoColor=white)](https://github.com/YeonV)
[![Blade](https://img.shields.io/badge/A.K.A-Blade-darkred.svg?logo=github&logoColor=white)](https://github.com/YeonV)

<p align="left">
  <img src="ui/public/logo.svg" alt="JarvYZ" width="200">
</p>

> `yz-music` — Play YouTube Video via mpv to set Audio/Video delay

### Techs

[![FastAPI](https://img.shields.io/badge/x-FastAPI-blue.svg?logo=fastapi&logoColor=white&label=)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/x-React-blue.svg?logo=react&logoColor=white&label=)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/x-TypeScript-blue.svg?logo=typescript&logoColor=white&label=)](https://www.typescriptlang.org/)
[![yt-dlp](https://img.shields.io/badge/x-yt--dlp-blue.svg?logo=youtube&logoColor=white&label=)](https://github.com/yt-dlp/yt-dlp)
[![mpv](https://img.shields.io/badge/x-mpv-blue.svg?logo=mpv&logoColor=white&label=)](https://mpv.io/)

**Run** `python -m yz_music.server` &nbsp;·&nbsp; **API** `/api/media/*` · `/api/media/events`

<!-- ───────────────────────────────────────────────────────────────────────── -->

<details>
<summary><b>Documentation</b></summary>

Standalone HTTP service that downloads YouTube videos to a local library and
controls mpv playback over IPC.

A **satellite** in the JarvYZ ecosystem — it has its own life outside JarvYZ.
You can run it on its own box, point any number of clients at it (JarvYZ, a
CLI, your own UI, a Tampermonkey userscript), and it doesn't know or care
who's calling. Same pattern as the wakeword-trainer satellite.

The satellite has **two faces**:

1. **HTTP daemon** (`python -m yz_music.server`) — long-running, serves the
   bundled React SPA at `/` + a JSON/WS API at `/play`, `/control`,
   `/now_playing`, etc.
2. **CLI shim** (`yt-play <url>` or `python -m yz_music <url>`) — one-shot
   process invoked by the Windows protocol handler (`mpv-yt://`), the
   tampermonkey userscript, or by hand. **Auto-delegates to the daemon
   when one is running**, falls back to a standalone download + mpv launch
   when not. So the legacy zero-daemon flow still works on hosts that
   never start the server.

## Run standalone

```bash
pip install -e .
python -m yz_music.server        # listens on http://127.0.0.1:9002
```

Or override via env:

```bash
MUSIC_HOST=0.0.0.0 MUSIC_PORT=9002 MUSIC_LIBRARY=/path/to/library \
  python -m yz_music.server
```

Browse to `http://127.0.0.1:9002/` — the satellite serves a self-contained
React UI (the same one JarvYZ embeds via dynamic-module). If you installed
from a built wheel, the UI is already bundled. From source: build it once:

```bash
cd ui
npm install
npm run build:pages   # outputs to ../music/static/
```

After that the satellite mounts the SPA at `/` (server.py:end checks if
`music/static/` is populated; mounts it if so, falls through to API-only if
not).

## UI build pipeline (for JarvYZ-embedded users)

The same UI also ships as an IIFE that JarvYZ loads via `@yz-dev/react-dynamic-module`:

```bash
cd ui
npm run ship          # = build:lib + copy IIFE to frontend/public/modules/
                      #   AND web/static/modules/ (JarvYZ serves both)
```

`build:lib` outputs `ui/dist-lib/yz-music.iife.js`; the install step copies
it where JarvYZ's frontend can find it. Either build mode reads the SAME
source — only the entry point + bundle shape differ.

## Building a wheel (for distribution)

```bash
bash scripts/build_wheel.sh
```

That script does the right thing: installs UI deps if missing, builds the
SPA into `music/static/`, then runs `python -m build`. Resulting wheel in
`dist/` contains the SPA — `pip install` + run gives a working UI at
`http://127.0.0.1:9002/` with no further setup.

Use `PYTHON=/path/to/python bash scripts/build_wheel.sh` to override the
python interpreter (the script auto-picks `python3` → `python` from PATH).

(Don't run `python -m build` directly — the SPA won't be built and the
wheel will be UI-less.)

## HTTP API

### Playback

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/play` | `{url, mode?}` mode = `play`\|`next`\|`queue` | `{ok, mode}` — fire-and-forget spawn |
| `POST` | `/control` | `{action, value?}` | `{ok, reason}` — IPC bridge to mpv |
| `GET` | `/now_playing` | — | live mpv snapshot (path, time_pos, pause, volume, ...) |

`/control` actions: `pause`, `play`, `resume`, `next`, `prev`, `stop`,
`mute`, `vol_up`, `vol_down`, `vol_set`, `seek`, `seek_abs`, `audio_delay`,
`shuffle`, `unshuffle`, `loop_one`, `loop_all`, `loop_off`.

### Library + downloads

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/library` | — | `[{video_id, title, channel, size_mb, mtime, path, url, is_fallback, duration_seconds}]` newest first |
| `GET` | `/downloads` | — | `{downloads: [{id, status, percent, eta, rate, title, ...}]}` |
| `POST` | `/download/progress` | yt-dlp progress shape | `{ok}` — **internal**, CLI children POST here per tick |

### Settings

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/settings` | — | `{library_path, audio_only, audio_delay_ms, fallback_video_ids, fallback_loop}` |
| `PATCH` | `/settings` | partial of above | full snapshot after apply, persisted to `<root>/settings.json` |

### Events + health

| Method | Path | Body | Returns |
|---|---|---|---|
| `WS` | `/events` | — | server-pushed `{event: 'now_playing'\|'download_progress', ...payload}` |
| `GET` | `/health` | — | `{ok, version, python, platform}` |

The initial `/events` frame is always a `now_playing` snapshot so clients
don't have to call `GET /now_playing` separately on connect.

## CLI

```
yt-play [--mode play|next|queue] [--library <path>] [--audio-delay-ms N] [--no-video] <url>
yt-play mpv-yt://<url>          # play-now URL protocol
yt-play mpv-yt-n://<url>        # play-next protocol
yt-play mpv-yt-q://<url>        # queue protocol
yt-play mpv-yt-d://<ms>         # set audio-delay in ms (negative = audio earlier)
```

When invoked, the CLI:

1. Probes the daemon at `MUSIC_SERVER_URL` (default `http://127.0.0.1:9002`).
   Timeout 0.4 s — daemon-less hosts pay almost no overhead.
2. If alive: POSTs `/play` (or `/control` for the delay protocol) and exits.
3. If down OR if `--library` was passed OR if `MUSIC_PROGRESS_URL` is set
   (we're a daemon child — skip to avoid infinite recursion): falls
   through to standalone path → yt-dlp → mpv launch with IPC server.

Both paths preserve the same protocol-handler + tampermonkey contract.

## Use with JarvYZ

JarvYZ's `web/api/music_satellite.py` is a thin proxy that forwards
`/api/media/*` to this satellite. Configure via `settings.media.satellite_url`
(default `http://127.0.0.1:9002`).

When `satellite_url` is localhost AND `satellite_auto_spawn` is True AND
the satellite isn't running, JarvYZ auto-spawns it on first hit. When
remote, JarvYZ surfaces a clear "unreachable" error.

The LLM voice tools `play_song` + `mpv_control` live in
`pipeline/yt_play.py` and call the satellite via HTTP — they no longer
spawn subprocesses or open IPC sockets directly.

## Architecture

```
HTTP client (JarvYZ / SPA / userscript / curl)
        │
        ▼
   server.py  (FastAPI on :9002)
        │  POST /play
        ▼
   subprocess.Popen([sys.executable, "-m", "music", <url>])
        │  (yt-play CLI, no daemon delegation — MUSIC_PROGRESS_URL set)
        ▼
   cli.py  ←  yt-dlp (download if not in library)
        │
        ▼  ipc_send(loadfile)
   mpv  ←──  /tmp/jarvyz-mpv-ipc.sock  (Linux Unix socket)
                                       (\\.\pipe\mpv-yt-pipe on Windows)
        │
        ▼  property-change events
   observer.py (thread inside server.py)
        │  fan-out
        ▼  WS /events
   subscribers (SPA Now Playing card, JarvYZ prompt_brief)
```

The CLI owns: argv parsing, yt-dlp invocation, mpv launch, IPC writes.
The server owns: HTTP/WS surface, observer thread, downloads dict, settings persistence.
JarvYZ owns: nothing about playback. Just talks HTTP.

## Data layout

Everything the satellite writes lives under `<music_root>` — default
`~/.jarvyz/satellites/music/`. This mirrors the source repo layout
(`satellites/yz-music/`) and keeps playback state separate from JarvYZ's own
app-data.

The **media library itself** (`.mkv` files yt-dlp downloads) lives at
`settings.library_path` (default `D:\Media\YouTube` on Windows,
`~/Media/YouTube` on Linux). That's set per-user via `PATCH /settings` or
the Music page editor.

Override the satellite root via `JWT_MUSIC_ROOT` env. Subdirs:

```
<music_root>/
└── settings.json                    # library_path, audio_only, audio_delay_ms,
                                     #   fallback_video_ids, fallback_loop
```

The library directory itself (configurable):

```
<library_path>/
├── .archive.txt                     # yt-dlp duplicate guard
├── .yt-play.log                     # CLI subprocess action log
└── <Artist>/
    └── <Title> [<video_id>].mkv     # canonical filename — video_id in []
```

## Settings cross-platform notes

- **`audio_only`** — Linux default `true` (pass `--no-video` to mpv;
  dodges the WSLg RDP video/audio channel-contention bug — audio gets
  the whole channel to itself). Windows default `false` (the user's mpv
  window habit is preserved).
- **mpv IPC endpoint** — Windows named pipe `\\.\pipe\mpv-yt-pipe`,
  Linux Unix socket `/tmp/jarvyz-mpv-ipc.sock`. Override either via
  `JARVIS_MPV_IPC_SOCKET` env (shared contract — both server.py and
  cli.py and the standalone yt-play.py read this same var).
- **Linux mpv launch args** — auto-applied by cli.py on launch:
  `--vo=x11` (Xwayland routes reliably on WSLg where Wayland-native
  hangs), `--audio-buffer=1.0 --audio-samplerate=44100 --audio-format=s16`
  (match WSLg RDPSink format end-to-end, no in-Pulse conversion),
  `--mute=no --aid=auto` (defend against stale state inheritance),
  `--no-video` if `settings.audio_only`.

## Tests

```bash
cd ui
npm install
npm run test:e2e      # Playwright smoke tests against the standalone SPA
```

8 tests cover: SPA shell rendering, `/health`, `/library` shape,
`/downloads` shape, `/settings` GET+PATCH round-trip (idempotent —
restores any mutation), UI audio-only toggle → PATCH, real library
titles in the data grid, `/events` WS initial-frame.

`playwright.config.ts` auto-spawns the satellite via `webServer` with
`reuseExistingServer: true`, so tests share a satellite that JarvYZ
auto-spawned or that you started by hand.


</details>
