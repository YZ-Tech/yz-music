"""mpv-IPC observer — subscribes to mpv property events and maintains a
live snapshot of playback state.

Ported from JarvYZ's pipeline/mpv_observer.py (Phase 2 of the music
satellite migration). Same behavior, three substitutions:
  - JarvYZ's `pipeline.events.emit(...)` → injectable subscriber list
    (server.py wires WS broadcast into it)
  - JarvYZ's `pipeline.settings.settings.media.*` → music.settings.settings.*
  - JarvYZ-bound IPC pipe constant → yz_music.cli._open_ipc (cross-platform)

The observer thread reconnects automatically on mpv exit/restart, so a
single instance survives across many play sessions."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import settings as _settings_mod
from .cli import _open_ipc


_VIDEO_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\](?=\.[^.]+$)")


def _video_id_from_path(path: str | None) -> str | None:
    if not path:
        return None
    m = _VIDEO_ID_RE.search(path)
    return m.group(1) if m else None


def _find_video_file(video_id: str) -> Path | None:
    lib = _settings_mod.settings.library_path
    if not lib.exists():
        return None
    needle = f"[{video_id}]"
    for path in lib.rglob("*.mkv"):
        if needle in path.name:
            return path
    return None


# Map mpv property names to snapshot keys.
_SNAPSHOT_KEYS: dict[str, str] = {
    "path": "path",
    "time-pos": "time_pos",
    "duration": "duration",
    "pause": "pause",
    "playlist-pos": "playlist_pos",
    "playlist-count": "playlist_count",
    "volume": "volume",
    "idle-active": "idle",
    "loop-file": "loop_file",
    "loop-playlist": "loop_playlist",
}


EmitFn = Callable[[str, dict[str, Any]], None]


class MpvObserver:
    """Thread-based mpv IPC listener. start() spins up the daemon."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._in_fallback_cycle: bool = False
        self._snapshot: dict[str, Any] = {
            "path": None,
            "video_id": None,
            "time_pos": None,
            "duration": None,
            "pause": None,
            "playlist_pos": None,
            "playlist_count": None,
            "volume": None,
            "idle": True,
            "loop_file": None,
            "loop_playlist": None,
        }
        self._snap_lock = threading.Lock()
        self._subscribers: list[EmitFn] = []

    # ── subscriber registration ────────────────────────────────────

    def subscribe(self, fn: EmitFn) -> None:
        """Register a callback. fn(event_name, payload_dict) is called
        on every now_playing-relevant property change."""
        self._subscribers.append(fn)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        for fn in self._subscribers:
            try:
                fn(event, payload)
            except Exception:
                pass

    # ── lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="mpv-observer", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # ── internals ──────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._session()
            except Exception as e:  # noqa: BLE001 — log + reconnect
                print(f"[mpv-observer] session error: {e}")
            self._stop.wait(2.0)

    def _session(self) -> None:
        try:
            f = _open_ipc()
        except FileNotFoundError:
            return  # mpv not running — backoff handled by _run()
        except OSError:
            return

        try:
            self._send(f, {"command": ["observe_property", 1, "idle-active"]})
            self._send(f, {"command": ["observe_property", 2, "path"]})
            self._send(f, {"command": ["observe_property", 3, "time-pos"]})
            self._send(f, {"command": ["observe_property", 4, "duration"]})
            self._send(f, {"command": ["observe_property", 5, "pause"]})
            self._send(f, {"command": ["observe_property", 6, "playlist-pos"]})
            self._send(f, {"command": ["observe_property", 7, "playlist-count"]})
            self._send(f, {"command": ["observe_property", 8, "volume"]})
            self._send(f, {"command": ["observe_property", 9, "loop-file"]})
            self._send(f, {"command": ["observe_property", 10, "loop-playlist"]})

            while not self._stop.is_set():
                line = self._read_line(f)
                if not line:
                    return  # pipe closed (mpv quit)
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self._handle(msg, f)
        finally:
            try:
                f.close()
            except OSError:
                pass

    @staticmethod
    def _send(f, cmd: dict) -> None:
        f.write((json.dumps(cmd) + "\n").encode("utf-8"))
        f.flush()

    def _read_line(self, f) -> bytes:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                b = f.read(1)
            except OSError:
                return b""
            if not b:
                return b""
            if b == b"\n":
                return bytes(buf)
            buf.extend(b)
            if len(buf) > 64 * 1024:
                return b""
        return b""

    def _handle(self, msg: dict, f) -> None:
        if msg.get("event") != "property-change":
            return
        name = msg.get("name")
        value = msg.get("data")

        if name == "path":
            self._on_path_change(value)
        elif name == "idle-active" and value is True:
            self._on_idle(f)

        snap_key = _SNAPSHOT_KEYS.get(name)
        if snap_key is None:
            return
        with self._snap_lock:
            self._snapshot[snap_key] = value
            if snap_key == "path":
                self._snapshot["video_id"] = _video_id_from_path(value)
                self._snapshot["time_pos"] = None
                self._snapshot["duration"] = None
            payload = dict(self._snapshot)
        self._emit("now_playing", payload)

    def _on_path_change(self, path: str | None) -> None:
        if not path:
            return
        s = _settings_mod.settings
        vid = _video_id_from_path(path)
        fallback_ids = list(s.fallback_video_ids or [])
        self._in_fallback_cycle = bool(vid and vid in fallback_ids)

    def _on_idle(self, f) -> None:
        s = _settings_mod.settings
        fallback_ids = list(s.fallback_video_ids or [])
        if not fallback_ids:
            return
        if self._in_fallback_cycle and not s.fallback_loop:
            return

        resolved: list[Path] = []
        for vid in fallback_ids:
            p = _find_video_file(vid)
            if p is not None:
                resolved.append(p)
        if not resolved:
            return

        self._in_fallback_cycle = True
        for i, p in enumerate(resolved):
            mode = "replace" if i == 0 else "append-play"
            try:
                self._send(f, {"command": ["loadfile", str(p), mode]})
            except OSError:
                return
            time.sleep(0.05)

    # ── public read-side ───────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        with self._snap_lock:
            return dict(self._snapshot)

    @property
    def in_fallback_cycle(self) -> bool:
        return self._in_fallback_cycle


# Module-level singleton — server.py starts/stops this.
observer = MpvObserver()
