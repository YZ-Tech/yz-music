"""FastAPI daemon for the music satellite.

Routes mirror JarvYZ's current /api/media/* surface but live HERE (no
/api/media prefix — the JarvYZ-side proxy adds it). The CLI (yt-play /
python -m yz_music) is reused as the implementation for /play — server.py
fires it as a subprocess, identical to today's JarvYZ-spawn-yt-play
mechanic. Long-running concerns (mpv observation, download tracking)
run as in-process state.

Endpoints (Phase 2):
  GET  /health                      — {ok, version, gpu, python}
  GET  /now_playing                 — observer snapshot
  GET  /library                     — flat list of all <id>-tagged files
  GET  /downloads                   — current/recent yt-dlp jobs
  POST /play                        — {url, mode?} → fire CLI subprocess
  POST /control                     — {action, value?} → IPC to mpv
  POST /download/progress           — internal, CLI children POST here
  GET  /settings                    — snapshot of current settings
  WS   /events                      — server-pushed now_playing + download_progress

Phase 4 (JarvYZ-side adapter) adds the /api/media/* proxy in JarvYZ.
Phase 5 mounts ./static at / for the standalone SPA."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__, settings as _settings_mod
from . import persistent_settings as _persist  # noqa: F401 — load() runs on import
from .cli import ipc_send, IPC_NO_MPV, IPC_SENT, MODE_TO_MPV, _yt_dlp_bin
from .observer import observer


app = FastAPI(title="music", version=__version__)


# ─────────────────────────── lifecycle ────────────────────────────


@app.on_event("startup")
async def _startup() -> None:
    """Wire WS broadcast into the observer + start the observer thread.
    The observer reconnects to mpv on its own; nothing else to do."""
    observer.subscribe(_emit)
    observer.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    observer.stop()


# ──────────────────────── WS broadcast plumbing ────────────────────
# Minimal pub/sub: register WS connections, fan out events from observer
# + download tracker. asyncio queue per subscriber so a slow consumer
# doesn't block the publisher.


_ws_subscribers: set[asyncio.Queue] = set()


def _emit(event: str, payload: dict[str, Any]) -> None:
    """Observer + download tracker call into this. Pushes the message to
    every connected WS subscriber's queue. Called from the observer
    thread, so we hop onto the asyncio loop via run_coroutine_threadsafe
    when needed."""
    msg = {"event": event, **payload}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop on this thread (observer is a thread, not async).
        # Get the main loop from the global server reference.
        loop = _main_loop
    if loop is None:
        return
    for q in list(_ws_subscribers):
        try:
            loop.call_soon_threadsafe(q.put_nowait, msg)
        except Exception:
            pass


_main_loop: asyncio.AbstractEventLoop | None = None


@app.on_event("startup")
async def _capture_loop() -> None:
    """Stash the main asyncio loop so observer-thread emits can find it."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()


@app.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    """Server → client push of now_playing + download_progress events."""
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    _ws_subscribers.add(q)
    try:
        # Initial snapshot so the client doesn't wait for the first change.
        await ws.send_json({"event": "now_playing", **observer.snapshot()})
        while True:
            msg = await q.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        _ws_subscribers.discard(q)


# ─────────────────────────── settings ─────────────────────────────


def _settings_snapshot() -> dict:
    s = _settings_mod.settings
    return {
        "library_path": str(s.library_path),
        "audio_only": s.audio_only,
        "audio_delay_ms": s.audio_delay_ms,
        "fallback_video_ids": list(s.fallback_video_ids),
        "fallback_loop": s.fallback_loop,
    }


@app.get("/settings")
def get_settings() -> dict:
    return _settings_snapshot()


@app.patch("/settings")
def patch_settings(patch: dict) -> dict:
    """Mutate satellite settings + persist to disk. Accepted keys:
    library_path, audio_only, audio_delay_ms, fallback_video_ids,
    fallback_loop. Unknown keys are ignored. Returns the full
    post-merge snapshot."""
    _persist.apply_patch(patch)
    return _settings_snapshot()


# ─────────────────────────── dependencies ─────────────────────────


@app.get("/dependencies")
def get_dependencies() -> dict:
    """yt-dlp + mpv status (found / version / latest / install hint).
    Surfaced in the UI's Dependencies card so new users see exactly
    what's missing + outdated users get a nudge. Latest-version checks
    are cached 1h (GitHub releases API; 60 req/h unauth limit)."""
    from . import dependencies as deps
    return deps.status()


@app.post("/dependencies/update")
def post_dependencies_update(body: dict) -> dict:
    """Run the platform-appropriate update command for `name` (one of
    `ytdlp` / `mpv`). User opt-in only — the UI's "Run update" button
    triggers this. Returns combined-output capture so the UI can show
    success or the package-manager error verbatim. No auto-trigger from
    the satellite itself; this only runs when the human says go."""
    from . import dependencies as deps
    name = str(body.get("name", "")).strip()
    return deps.run_update(name)


@app.post("/dependencies/update_all")
def post_dependencies_update_all() -> dict:
    """Check all three deps and install/update ONLY what's missing or outdated
    in ONE elevated pass (single UAC on Windows / one sudo terminal on Linux).
    User opt-in only — the UI's 'Install / repair all' button. Returns a 'noop'
    kind when nothing needs doing."""
    from . import dependencies as deps
    return deps.run_update_all()


# ─────────────────────────── health ───────────────────────────────


@app.get("/health")
def health() -> dict:
    """Liveness probe — also surfaces python + version info for clients."""
    return {
        "ok": True,
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


# ─────────────────────── playback control ─────────────────────────


class _PlayBody(BaseModel):
    url: str
    mode: str = "play"  # play | next | queue


@app.post("/play")
def play(body: _PlayBody) -> dict:
    """Fire-and-forget: spawn the CLI to download (if needed) + load
    via IPC. Returns immediately. Progress comes via /download/progress
    POSTs from the CLI child + /events WS."""
    if body.mode not in MODE_TO_MPV:
        raise HTTPException(400, f"mode must be one of {list(MODE_TO_MPV.keys())}")
    if not body.url.strip():
        raise HTTPException(400, "url required")

    # Spawn the CLI via `python -m yz_music` — no path resolution needed,
    # the package is importable wherever this server is running.
    args = [sys.executable, "-m", "yz_music"]
    if body.mode != "play":
        args.extend(["--mode", body.mode])
    s = _settings_mod.settings
    args.extend(["--library", str(s.library_path)])
    if s.audio_delay_ms:
        args.extend(["--audio-delay-ms", str(s.audio_delay_ms)])
    if s.audio_only:
        args.append("--no-video")
    args.append(body.url)

    # Tell the CLI where to POST progress (so it lands here, not at JarvYZ).
    env = os.environ.copy()
    env["MUSIC_PROGRESS_URL"] = f"http://127.0.0.1:{_PORT}/download/progress"

    spawn_kw: dict[str, Any]
    if sys.platform == "win32":
        spawn_kw = {"creationflags": 0x00000008}  # DETACHED_PROCESS
    else:
        spawn_kw = {"start_new_session": True}
    try:
        subprocess.Popen(
            args, env=env, close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **spawn_kw,
        )
    except OSError as e:
        raise HTTPException(500, f"spawn failed: {e}")
    return {"ok": True, "mode": body.mode}


class _ControlBody(BaseModel):
    action: str
    value: int = 0


_STATIC_ACTIONS: dict[str, list[dict]] = {
    "pause":  [{"command": ["cycle", "pause"]}],
    "play":   [{"command": ["set_property", "pause", False]}],
    "resume": [{"command": ["set_property", "pause", False]}],
    "next":   [{"command": ["playlist-next"]}],
    "prev":   [{"command": ["playlist-prev"]}],
    "stop":   [{"command": ["stop"]}],
    "mute":   [{"command": ["cycle", "mute"]}],
    "loop_one": [
        {"command": ["set_property", "loop-file", "inf"]},
        {"command": ["set_property", "loop-playlist", "no"]},
    ],
    "loop_all": [
        {"command": ["set_property", "loop-file", "no"]},
        {"command": ["set_property", "loop-playlist", "inf"]},
    ],
    "loop_off": [
        {"command": ["set_property", "loop-file", "no"]},
        {"command": ["set_property", "loop-playlist", "no"]},
    ],
    "shuffle":   [{"command": ["playlist-shuffle"]}],
    "unshuffle": [{"command": ["playlist-unshuffle"]}],
}


@app.post("/control")
def control(body: _ControlBody) -> dict:
    """JSON-IPC bridge to the running mpv."""
    action = body.action
    value = body.value
    cmds = _STATIC_ACTIONS.get(action)
    if cmds is None:
        if action == "vol_up":
            cmds = [{"command": ["add", "volume",  value or 5]}]
        elif action == "vol_down":
            cmds = [{"command": ["add", "volume", -(value or 5)]}]
        elif action == "vol_set":
            cmds = [{"command": ["set_property", "volume", max(0, min(130, value))]}]
        elif action == "seek":
            cmds = [{"command": ["seek", value or 10, "relative"]}]
        elif action == "seek_abs":
            cmds = [{"command": ["seek", max(0, value), "absolute"]}]
        elif action == "audio_delay":
            ms = int(value)
            _settings_mod.settings.audio_delay_ms = ms
            cmds = [{"command": ["set_property", "audio-delay", -ms / 1000.0]}]
        else:
            raise HTTPException(400, f"unknown action {action!r}")

    # cli.ipc_send returns a single string: IPC_SENT, IPC_NO_MPV, or IPC_ERROR.
    result = ipc_send(cmds)
    return {"ok": result == IPC_SENT, "reason": result}


# ─────────────────────────── now playing ──────────────────────────


@app.get("/now_playing")
def now_playing() -> dict:
    return observer.snapshot()


# ─────────────────────────── library ──────────────────────────────


_VIDEO_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\](?=\.[^.]+$)")


@app.get("/library")
def library() -> list[dict]:
    """Flat list of <id>-tagged playable files in the library root.

    Shape matches what the Music UI's LibraryItem type expects:
    {video_id, title, channel, size_mb, mtime, path, url, duration_seconds,
    is_fallback}. The `url` field is derived from video_id (it's what the
    components pass to mediaPlay → /play). `is_fallback` is computed from
    the live fallback list. `duration_seconds` is None — populating it
    would require ffprobe per file; future work."""
    lib = _settings_mod.settings.library_path
    if not lib.exists():
        return []
    fallback_ids = set(_settings_mod.settings.fallback_video_ids or [])
    out: list[dict] = []
    for path in lib.rglob("*.mkv"):
        m = _VIDEO_ID_RE.search(path.name)
        if not m:
            continue
        st = path.stat()
        title = _VIDEO_ID_RE.sub("", path.stem).strip().rstrip("-").rstrip()
        video_id = m.group(1)
        out.append({
            "video_id": video_id,
            "title": title.removesuffix("[]").strip(),
            "channel": path.parent.name,
            "size_mb": round(st.st_size / 1024 / 1024, 1),
            "mtime": st.st_mtime,
            "path": str(path),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "duration_seconds": None,
            "is_fallback": video_id in fallback_ids,
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


# ───────────────── library mutation (parity) ──────────────────────────
# Ported from the retiring in-core media.py: delete a library item (+ its
# sidecars, archive ledger entry, cached thumbnail, and mpv watch_later
# resume points) and update the on-disk library root. The satellite runs
# as a Windows-host sibling process, so the APPDATA/LOCALAPPDATA watch_later
# cleanup works identically to the old in-core path.


def _archive_path() -> Path:
    return _settings_mod.settings.library_path / ".archive.txt"


def _clear_watch_later(file_path: Path) -> int:
    """Brute-force every plausible watch_later dir + path-string variant.
    Returns number of entries removed."""
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    dirs: list[Path] = []
    for base in (local, appdata):
        if not base:
            continue
        for sub in ("mpv", "mpv.net"):
            d = Path(base) / sub / "watch_later"
            if d.exists():
                dirs.append(d)
    if not dirs:
        return 0
    paths = {str(file_path), str(file_path.resolve())}
    paths.update({p.replace("\\", "/") for p in list(paths)})
    removed = 0
    for p in paths:
        digest = hashlib.md5(p.encode("utf-8")).hexdigest()
        for variant in (digest, digest.upper()):
            for d in dirs:
                target = d / variant
                if target.exists():
                    try:
                        target.unlink()
                        removed += 1
                    except OSError:
                        pass
    return removed


def _remove_from_archive(video_id: str) -> bool:
    archive = _archive_path()
    if not archive.exists():
        return False
    needle = f"youtube {video_id}"
    try:
        lines = archive.read_text(encoding="ascii").splitlines()
    except OSError:
        return False
    kept = [line for line in lines if line.strip() != needle]
    if len(kept) == len(lines):
        return False
    try:
        archive.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="ascii")
    except OSError:
        return False
    return True


@app.delete("/library/{video_id}")
def delete_library_item(video_id: str) -> dict:
    """Delete the .mkv (+ any sidecars sharing the [video_id]) and clean up
    the archive ledger, cached thumbnail, and mpv watch_later entries."""
    if not _VIDEO_ID_VALID.match(video_id):
        raise HTTPException(400, "invalid video id")
    lib = _settings_mod.settings.library_path
    if not lib.exists():
        raise HTTPException(404, "library not found")
    needle = f"[{video_id}]"
    # NEVER rglob(f"*{needle}*") — brackets are an fnmatch character class.
    # Walk all files and substring-check the filename instead.
    targets = [p for p in lib.rglob("*") if p.is_file() and needle in p.name]
    if not targets:
        raise HTTPException(404, "no files matched")
    deleted: list[str] = []
    watch_later_removed = 0
    for path in targets:
        watch_later_removed += _clear_watch_later(path)
        try:
            path.unlink()
            deleted.append(str(path))
        except OSError as e:
            raise HTTPException(500, f"could not delete {path.name}: {e}") from e
    archive_removed = _remove_from_archive(video_id)
    thumb = _thumbnail_path(video_id)
    if thumb.exists():
        try:
            thumb.unlink()
        except OSError:
            pass
    return {
        "ok": True,
        "deleted": deleted,
        "archive_removed": archive_removed,
        "watch_later_removed": watch_later_removed,
    }


@app.put("/library_path")
def put_library_path(body: dict = Body(...)) -> dict:
    """Update the on-disk library root. Body: {path: str}. Existence is not
    enforced — the user may point at a path they haven't created yet."""
    raw = body.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(400, "path must be a non-empty string")
    _persist.apply_patch({"library_path": raw.strip()})
    return {"ok": True, "path": str(_settings_mod.settings.library_path)}


# ───────────────── search / thumbnail / fallback (parity) ─────────────
# Ported from the retiring in-core media.py so the satellite owns the FULL
# media surface; JarvYZ then proxies /api/media/* generically. Fallback now
# lives in ONE place (the satellite settings) instead of also in core settings.

_VIDEO_ID_VALID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _find_video_file(video_id: str) -> Path | None:
    lib = _settings_mod.settings.library_path
    if not lib.exists():
        return None
    needle = f"[{video_id}]"
    for path in lib.rglob("*.mkv"):
        if needle in path.name:
            return path
    return None


def _thumbnail_path(video_id: str) -> Path:
    return _settings_mod.settings.library_path / ".thumbnails" / f"{video_id}.jpg"


def _extract_thumbnail(video: Path, out: Path) -> bool:
    from .cli import _ffmpeg_bin  # robust resolver (live PATH + winget dirs)
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg.exists():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [str(ffmpeg), "-y", "-ss", "5", "-i", str(video),
             "-vframes", "1", "-vf", "scale=320:-1", str(out)],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.exists()


@app.get("/search/youtube")
def search_youtube(q: str, limit: int = 5) -> list[dict]:
    """Search YouTube via yt-dlp ytsearch{N}. Metadata only, no download."""
    if not q.strip():
        return []
    limit = max(1, min(int(limit), 10))
    yt = _yt_dlp_bin()
    try:
        out = subprocess.run(
            [str(yt), "--default-search", f"ytsearch{limit}",
             "--print", "%(id)s\t%(title)s\t%(channel)s\t%(webpage_url)s\t%(duration)s",
             "--no-warnings", "--skip-download", "--flat-playlist", q],
            capture_output=True, text=True, timeout=25,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(502, f"yt-dlp failed: {e}") from e
    results: list[dict] = []
    for line in (out.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        vid, title, channel, url, duration = parts[:5]
        try:
            dur = int(float(duration)) if duration and duration != "NA" else None
        except ValueError:
            dur = None
        results.append({"video_id": vid, "title": title, "channel": channel,
                        "url": url, "duration_seconds": dur})
    return results


@app.get("/thumbnail/{video_id}")
def thumbnail(video_id: str) -> FileResponse:
    """JPEG thumbnail from the library .mkv (frame ~5s, 320w), cached in
    <library>/.thumbnails/."""
    if not _VIDEO_ID_VALID.match(video_id):
        raise HTTPException(400, "invalid video id")
    cached = _thumbnail_path(video_id)
    if not cached.exists():
        video = _find_video_file(video_id)
        if video is None:
            raise HTTPException(404, "video not found")
        if not _extract_thumbnail(video, cached):
            raise HTTPException(500, "thumbnail extraction failed")
    return FileResponse(cached, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


def _fallback_state() -> dict:
    s = _settings_mod.settings
    ids = list(s.fallback_video_ids or [])
    by_id = {it["video_id"]: it for it in library()}
    return {
        "ids": ids,
        "loop": bool(s.fallback_loop),
        "items": [{"video_id": v, "metadata": by_id.get(v)} for v in ids],
    }


@app.get("/fallback")
def get_fallback() -> dict:
    return _fallback_state()


@app.put("/fallback")
def put_fallback(body: dict = Body(...)) -> dict:
    patch: dict = {}
    if "ids" in body:
        ids = body["ids"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise HTTPException(400, "ids must be a list of strings")
        seen: set[str] = set(); cleaned: list[str] = []
        for v in ids:
            if _VIDEO_ID_VALID.match(v) and v not in seen:
                seen.add(v); cleaned.append(v)
        patch["fallback_video_ids"] = cleaned
    if "loop" in body:
        if not isinstance(body["loop"], bool):
            raise HTTPException(400, "loop must be boolean")
        patch["fallback_loop"] = body["loop"]
    if patch:
        _persist.apply_patch(patch)
    return _fallback_state()


@app.post("/fallback/{video_id}")
def add_fallback(video_id: str) -> dict:
    if not _VIDEO_ID_VALID.match(video_id):
        raise HTTPException(400, "invalid video id")
    ids = list(_settings_mod.settings.fallback_video_ids or [])
    if video_id not in ids:
        ids.append(video_id)
        _persist.apply_patch({"fallback_video_ids": ids})
    return _fallback_state()


@app.delete("/fallback/{video_id}")
def remove_fallback(video_id: str) -> dict:
    ids = list(_settings_mod.settings.fallback_video_ids or [])
    if video_id in ids:
        ids.remove(video_id)
        _persist.apply_patch({"fallback_video_ids": ids})
    return _fallback_state()


# ─────────────────────────── downloads ────────────────────────────


class _ProgressBody(BaseModel):
    id: str
    status: str
    url: str = ""
    video_id: str = ""
    title: str = ""
    percent: float = 0.0
    eta: str = ""
    rate: str = ""


_downloads_lock = threading.Lock()
_downloads: dict[str, dict] = {}

# Keep done/error entries visible briefly after the terminal update so the UI
# can flash a "complete"/"failed" state before the chip fades.
_GRACE_SECONDS = 5.0


def _emit_downloads_snapshot() -> None:
    """Broadcast the full active list. The UI subscriber replaces its local
    state with this on each event (simpler than reconciling deltas), so the
    WS payload is `{downloads: [...]}` — matching the now-retired in-core
    media.py contract and the satellite UI's `useSubscription` handler."""
    with _downloads_lock:
        items = list(_downloads.values())
    _emit("download_progress", {"downloads": items})


def _schedule_download_cleanup(dl_id: str) -> None:
    def _drop() -> None:
        with _downloads_lock:
            _downloads.pop(dl_id, None)
        _emit_downloads_snapshot()
    t = threading.Timer(_GRACE_SECONDS, _drop)
    t.daemon = True
    t.start()


@app.post("/download/progress")
def download_progress(body: _ProgressBody) -> dict:
    """CLI children POST here on every yt-dlp progress tick. We update the
    in-memory snapshot, broadcast the full list on the WS, and schedule
    cleanup of terminal-state entries."""
    rec = {**body.model_dump(), "updated_at": time.time()}
    with _downloads_lock:
        _downloads[body.id] = rec
    _emit_downloads_snapshot()
    if body.status in ("done", "error"):
        _schedule_download_cleanup(body.id)
    return {"ok": True}


@app.get("/downloads")
def downloads() -> dict:
    """Snapshot of all download records (active + recent terminal)."""
    with _downloads_lock:
        return {"downloads": list(_downloads.values())}


# ─────────────────────── LLM tools (JarvYZ-facing) ────────────────
# These endpoints are how this satellite contributes tools to JarvYZ's
# LLM tool catalog. Each accepts the LLM's arguments verbatim and returns
# {ok, text} where `text` is a sentence-ready confirmation suitable for
# TTS. The JarvYZ-side collector (pipeline/satellite_tools.py) is the
# only consumer.
#
# Discovery: declared in manifest.json under `tools[]`. Names + JSON
# schemas live there; this module owns only the implementation.


_PLAYABLE_EXTS = {".mkv", ".mp4", ".webm"}
_INTERMEDIATE_EXTS = {".part", ".ytdl", ".tmp"}
_FORMAT_STREAM_RE = re.compile(r"\.f\d+$")


def _find_local_by_query(query: str) -> tuple[str, str, str] | None:
    """Library-search by free-text query. Returns (video_id, title, channel)
    when exactly ONE library file matches every token (case-insensitive).
    Returns None on 0 or >1 matches — ambiguous → caller falls through to
    YouTube. Mirrors yt-play.py's strict all-tokens rule."""
    lib = _settings_mod.settings.library_path
    if not lib.exists():
        return None
    tokens = [t.lower() for t in query.split() if len(t) >= 2]
    if not tokens:
        return None
    matches: list[tuple[Path, str]] = []
    for path in lib.rglob("*.mkv"):
        m = _VIDEO_ID_RE.search(path.name)
        if not m:
            continue
        haystack = (path.stem + " " + path.parent.name).lower()
        if all(t in haystack for t in tokens):
            matches.append((path, m.group(1)))
    if len(matches) != 1:
        return None
    path, video_id = matches[0]
    title = _VIDEO_ID_RE.sub("", path.stem).strip().rstrip("-").rstrip()
    return video_id, title.removesuffix("[]").strip(), path.parent.name


def _search_youtube(query: str) -> tuple[str, str] | None:
    """Top YouTube hit for `query` → (webpage_url, title), or None."""
    from .cli import _yt_dlp_bin
    yt_dlp = _yt_dlp_bin()
    if not yt_dlp.exists():
        return None
    try:
        out = subprocess.run(
            [str(yt_dlp), "--default-search", "ytsearch1",
             "--print", "%(webpage_url)s|%(title)s",
             "--no-warnings", "--skip-download", query],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.stdout or "").strip().splitlines()[-1] if out.stdout else ""
    if "|" not in line:
        return None
    url, title = line.split("|", 1)
    return url.strip(), title.strip()


def _spawn_play(url: str, mode: str) -> None:
    """Same spawn path as POST /play — extracted so /tools/play_song can
    invoke it after resolving query → url without an HTTP self-call."""
    args = [sys.executable, "-m", "yz_music"]
    if mode != "play":
        args.extend(["--mode", mode])
    s = _settings_mod.settings
    args.extend(["--library", str(s.library_path)])
    if s.audio_delay_ms:
        args.extend(["--audio-delay-ms", str(s.audio_delay_ms)])
    if s.audio_only:
        args.append("--no-video")
    args.append(url)
    env = os.environ.copy()
    env["MUSIC_PROGRESS_URL"] = f"http://127.0.0.1:{_PORT}/download/progress"
    spawn_kw: dict[str, Any]
    if sys.platform == "win32":
        spawn_kw = {"creationflags": 0x00000008}
    else:
        spawn_kw = {"start_new_session": True}
    subprocess.Popen(
        args, env=env, close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **spawn_kw,
    )


class _PlaySongBody(BaseModel):
    query: str
    mode: str = "play"
    force_youtube: bool = False


@app.post("/tools/play_song")
def tool_play_song(body: _PlaySongBody) -> dict:
    """LLM tool: play / queue / play-next by free-text query. Library-first
    (unique-match), then YouTube search. Returns {ok, text}."""
    if body.mode not in MODE_TO_MPV:
        return {"ok": False, "text": f"Unknown mode '{body.mode}'. Use play, next, or queue."}
    query = body.query.strip()
    if not query:
        return {"ok": False, "text": "What song?"}
    verb = {"play": "Playing", "next": "Up next:", "queue": "Queued:"}[body.mode]

    if not body.force_youtube:
        local = _find_local_by_query(query)
        if local is not None:
            video_id, title, _channel = local
            try:
                _spawn_play(f"https://www.youtube.com/watch?v={video_id}", body.mode)
            except OSError as e:
                return {"ok": False, "text": f"Couldn't start playback: {e}"}
            return {"ok": True, "text": f"{verb} {title} from the library."}

    found = _search_youtube(query)
    if found is None:
        return {"ok": False, "text": f"Couldn't find anything for '{query}'."}
    url, title = found
    try:
        _spawn_play(url, body.mode)
    except OSError as e:
        return {"ok": False, "text": f"Couldn't start playback: {e}"}
    return {"ok": True, "text": f"{verb} {title}."}


_CONTROL_CONFIRM = {
    "pause":    "Paused.",
    "play":     "Resumed.",
    "resume":   "Resumed.",
    "next":     "Skipped.",
    "prev":     "Previous track.",
    "stop":     "Stopped.",
    "mute":     "Toggled mute.",
    "vol_up":   "Louder.",
    "vol_down": "Quieter.",
    "shuffle":   "Shuffled.",
    "unshuffle": "Unshuffled.",
}


@app.post("/tools/mpv_control")
def tool_mpv_control(body: _ControlBody) -> dict:
    """LLM tool: control the running mpv. Returns {ok, text} where text
    is sentence-ready. `audio_delay` persists even if mpv isn't running."""
    action = body.action
    value = body.value

    # Delegate to /control's resolution logic by calling the function
    # directly. It raises HTTPException(400) on unknown actions; treat
    # that as a clean refusal.
    try:
        result = control(body)  # {"ok": bool, "reason": str}
    except HTTPException as e:
        return {"ok": False, "text": f"Unknown mpv action '{action}'." if e.status_code == 400 else f"mpv error: {e.detail}"}

    sent = bool(result.get("ok"))
    reason = result.get("reason", "")
    if not sent and action != "audio_delay":
        return {"ok": False, "text": "No music playing." if reason == "no-mpv" else f"mpv error: {reason}"}

    if action == "vol_set":
        return {"ok": True, "text": f"Volume {value}."}
    if action == "seek":
        n = int(value) if value else 10
        return {"ok": True, "text": f"Seeked {n:+d} seconds."}
    if action == "seek_abs":
        return {"ok": True, "text": f"Seeked to {int(value)} seconds."}
    if action == "audio_delay":
        ms = int(value)
        suffix = "" if sent else " (saved — mpv not running)"
        if ms == 0:
            return {"ok": True, "text": f"Audio delay off.{suffix}"}
        return {"ok": True, "text": f"Audio delay {ms:+d} ms.{suffix}"}
    return {"ok": True, "text": _CONTROL_CONFIRM.get(action, "Done.")}


# ───────────────────────── entry point ────────────────────────────


# YZ_PORT = the port core resolved (settings.ports override) — wins so the
# bind always matches the client URL; MUSIC_PORT + default serve standalone runs.
_PORT = int(os.environ.get("YZ_PORT") or os.environ.get("MUSIC_PORT") or "9002")


def run() -> None:
    """CLI entry: `python -m yz_music.server` starts the FastAPI daemon."""
    import uvicorn
    uvicorn.run(
        "yz_music.server:app",
        host=os.environ.get("MUSIC_HOST", "127.0.0.1"),
        port=_PORT,
        log_level="info",
    )


# ─────────────────────── static UI (standalone) ───────────────────────────
# Serve the SPA from the bundled static/ dir at the root path, so
#   `pip install yz-music && python -m yz_music.server`
# gives a working UI at http://127.0.0.1:9002/ — no JarvYZ required.
#
# Built by `cd ui && npm run build:pages` (Vite outDir points here).
# Skipped if static/ doesn't exist or is empty (dev install without a UI
# build). In that case the satellite still exposes its API; users just
# hit /docs or use a client.
#
# Mount LAST: FastAPI matches routes in registration order, so all the
# JSON/WS routes above take precedence over the catch-all StaticFiles.

from fastapi.staticfiles import StaticFiles
from pathlib import Path

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.exists() and any(_static_dir.iterdir()):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")


if __name__ == "__main__":
    run()
