"""yt-play: download YouTube videos to a local library, then play with mpv.

Usage:
    python yt-play.py <youtube-url>                # play now
    python yt-play.py --mode next  <youtube-url>   # insert after current
    python yt-play.py --mode queue <youtube-url>   # append to end
    python yt-play.py --library <path> <url>       # override library root
    python yt-play.py mpv-yt://<url>               # play-now protocol
    python yt-play.py mpv-yt-n://<url>             # play-next protocol
    python yt-play.py mpv-yt-q://<url>             # queue protocol
    python yt-play.py mpv-yt-d://<ms>              # set audio-delay (-3000..+3000)

If the video ID is already present in the library folder, the file is reused.
Otherwise yt-dlp fetches it first.

`--library <path>` overrides the on-disk root. Used by JarvYZ (which
reads it fresh from settings on every spawn) so the Music-page editor
takes effect without restart. When the flag is absent — tampermonkey +
protocol-handler invocations — the default below applies. yt-play.py
intentionally does NOT import any JarvYZ modules; it must remain
standalone-friendly.

Playback talks to a running mpv via its IPC pipe (configured in mpv.conf:
`input-ipc-server=\\\\.\\pipe\\mpv-yt-pipe`). If mpv isn't running, a new
instance is launched and that becomes the IPC target for subsequent clicks.
The delay protocol only sends IPC; no-op if mpv isn't running.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import sys
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

_IS_WIN = sys.platform == "win32"

# --- config -----------------------------------------------------------------

# Default library root. Overridable per-invocation via `--library <path>`
# (JarvYZ passes this on every spawn from settings.media.library_path).
# ARCHIVE and LOG_FILE are derived; if --library is supplied, main()
# rebinds all three after parse_args.
LIBRARY = Path(r"D:\Media\YouTube") if _IS_WIN else Path.home() / "Media" / "YouTube"
ARCHIVE = LIBRARY / ".archive.txt"
LOG_FILE = LIBRARY / ".yt-play.log"


def _live_path_dirs() -> list[str]:
    r"""PATH directories, refreshed from the Windows registry so a binary added
    to PATH *after* this process launched is still found without a restart.
    os.environ['PATH'] is only a snapshot from process start (what shutil.which
    reads); on Windows we additionally read the live HKLM (system) + HKCU (user)
    ``Environment\Path``. Non-Windows: just the process PATH."""
    dirs: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        for d in raw.split(os.pathsep):
            d = os.path.expandvars(d.strip().strip('"'))
            if d and d.lower() not in seen:
                seen.add(d.lower())
                dirs.append(d)

    _add(os.environ.get("PATH", ""))
    if _IS_WIN:
        try:
            import winreg
            for hive, sub in (
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                (winreg.HKEY_CURRENT_USER, "Environment"),
            ):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        val, _ = winreg.QueryValueEx(key, "Path")
                        _add(str(val))
                except OSError:
                    pass  # key/value absent — skip
        except Exception:
            pass  # winreg unavailable — process PATH only
    return dirs


def _which_live(name: str) -> Path | None:
    """Like shutil.which, but against the registry-refreshed live PATH, so a
    freshly-installed binary is found with no app restart. Windows appends .exe."""
    exe = f"{name}.exe" if _IS_WIN else name
    for d in _live_path_dirs():
        cand = Path(d) / exe
        if cand.exists():
            return cand
    return None


def _mpv_bin() -> Path:
    """Locate the mpv binary. Windows: mpv.net (the user's existing
    install). Linux/macOS: vanilla mpv via PATH (shutil.which)."""
    if _IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        return Path(local) / "Programs" / "mpv.net" / "mpvnet.exe"
    found = shutil.which("mpv")
    return Path(found) if found else Path("/usr/bin/mpv")


def _yt_dlp_bin() -> Path:
    """Locate yt-dlp. Precedence:
      1. Sibling of the current Python interpreter — if we're in a venv
         that has yt-dlp pip-installed, it's in .../bin/yt-dlp. This
         beats PATH for the common 'JarvYZ spawns yt-play via .venv-wsl'
         case where the venv's bin isn't on PATH.
      2. shutil.which — works on Windows + Linux when on PATH.
      3. Windows WinGet fallback — the user's existing install location."""
    sibling = Path(sys.executable).parent / ("yt-dlp.exe" if _IS_WIN else "yt-dlp")
    if sibling.exists():
        return sibling
    found = _which_live("yt-dlp")   # live PATH — sees a just-installed yt-dlp
    if found:
        return found
    if _IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        return (Path(local) / "Microsoft" / "WinGet" / "Packages"
                / "yt-dlp.yt-dlp_Microsoft.Winget.Source_8wekyb3d8bbwe"
                / "yt-dlp.exe")
    return Path("/usr/bin/yt-dlp")


def _ffmpeg_bin() -> Path:
    r"""Locate ffmpeg. It has no single canonical install dir (and, unlike
    mpv/yt-dlp, no obvious single fallback), so try in order: the LIVE PATH
    (registry-refreshed on Windows — the old plain ``shutil.which`` read only
    the stale process PATH, so an ffmpeg added/installed after launch stayed
    invisible until a full restart), then winget's Links + the Gyan.FFmpeg
    package dir, then common manual locations. Returns a bare ``Path("ffmpeg")``
    (whose ``.exists()`` is False) when genuinely absent, so callers can test
    ``.exists()`` uniformly."""
    found = _which_live("ffmpeg")
    if found:
        return found
    if _IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"]
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgs.is_dir():
            candidates += sorted(pkgs.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"), reverse=True)
        candidates += [
            Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
        for c in candidates:
            if c.exists():
                return c
    return Path("ffmpeg")


def _ffmpeg_dir() -> str | None:
    """Directory of a resolved ffmpeg (for yt-dlp's ``--ffmpeg-location``), or
    None when ffmpeg can't be found anywhere."""
    b = _ffmpeg_bin()
    return str(b.parent) if b.exists() else None


def _ipc_path() -> str:
    """mpv IPC endpoint. Shared contract with JarvYZ: both processes read
    JARVIS_MPV_IPC_SOCKET if set, else use platform default. Windows uses
    a named pipe (openable as a file); Linux uses a Unix domain socket."""
    env = os.environ.get("JARVIS_MPV_IPC_SOCKET")
    if env:
        return env
    return r"\\.\pipe\mpv-yt-pipe" if _IS_WIN else "/tmp/jarvyz-mpv-ipc.sock"


# Back-compat module-level handles. Resolved at import-time on Windows
# (where the user has stable WinGet paths). Recomputed via the functions
# at each call site below — see ipc_send / launch_mpv / yt-dlp spawn.
MPV = _mpv_bin()
YTDLP = _yt_dlp_bin()
IPC_PIPE = _ipc_path()


def log_to_file(msg: str) -> None:
    """Append a timestamped line to the persistent log. Never raises."""
    try:
        LIBRARY.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# --- core -------------------------------------------------------------------

VIDEO_ID_PATTERNS = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"/embed/([A-Za-z0-9_-]{11})"),
]


# Logical mode -> mpv loadfile mode string.
MODE_TO_MPV = {
    "play":  "replace",
    "next":  "insert-next-play",
    "queue": "append-play",
}

PROTOCOL_TO_MODE = {
    "mpv-yt-n://": "next",
    "mpv-yt-q://": "queue",
    "mpv-yt://":   "play",
}

DELAY_PROTOCOL = "mpv-yt-d://"


def parse_args(argv: list[str]) -> dict:
    """Return a parsed command:
        {"cmd": "play"|"next"|"queue", "url": str, "library": str | None, "audio_delay_ms": int}
        {"cmd": "delay", "seconds": float, "library": str | None}

    `--audio-delay-ms=N` (or `--audio-delay-ms N`): positive value =
    compensate for N ms of downstream speaker delay (audio plays earlier).
    Applied as `--audio-delay=<-N/1000>` when launching a fresh mpv.
    """
    mode = "play"
    library: str | None = None
    audio_delay_ms = 0
    no_video = False
    raw = None
    it = iter(argv[1:])
    for a in it:
        if a == "--mode":
            try:
                mode = next(it)
            except StopIteration:
                raise SystemExit("--mode needs an argument (play|next|queue)")
        elif a == "--library":
            try:
                library = next(it)
            except StopIteration:
                raise SystemExit("--library needs an argument (path)")
        elif a == "--audio-delay-ms":
            try:
                audio_delay_ms = int(next(it))
            except (StopIteration, ValueError):
                raise SystemExit("--audio-delay-ms needs an integer (ms)")
        elif a.startswith("--audio-delay-ms="):
            try:
                audio_delay_ms = int(a.split("=", 1)[1])
            except ValueError:
                raise SystemExit("--audio-delay-ms needs an integer (ms)")
        elif a == "--no-video":
            no_video = True
        else:
            raw = a
            break
    if raw is None:
        raise SystemExit("usage: yt-play [--mode play|next|queue] [--library <path>] [--audio-delay-ms N] [--no-video] <url-or-mpv-yt[-n|-q|-d]-url>")

    raw = urllib.parse.unquote(raw).rstrip("/")

    if raw.startswith(DELAY_PROTOCOL):
        try:
            ms = float(raw[len(DELAY_PROTOCOL):])
        except ValueError:
            raise SystemExit(f"delay protocol expects a number (ms), got: {raw}")
        return {"cmd": "delay", "seconds": ms / 1000.0, "library": library}

    for prefix, m in PROTOCOL_TO_MODE.items():
        if raw.startswith(prefix):
            mode = m
            raw = raw[len(prefix):]
            break

    if mode not in MODE_TO_MPV:
        raise SystemExit(f"unknown mode: {mode}")

    return {"cmd": mode, "url": raw, "library": library,
            "audio_delay_ms": audio_delay_ms, "no_video": no_video}


def extract_video_id(url: str) -> str | None:
    for pat in VIDEO_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


_TIME_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?$")

def parse_yt_time(s: str) -> float | None:
    """Parse YouTube-style time strings: '0', '120', '120s', '1m30s', '1h2m3s'.

    Returns 0.0 for an explicit "0" — the caller can distinguish "no t= param"
    (None) from "force start at zero" (0.0) and decide whether to honor mpv's
    saved watch-later position or override it.
    """
    if not s:
        return None
    m = _TIME_RE.fullmatch(s.strip())
    if not m or not any(m.groups()):
        return None
    h  = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    se = float(m.group(3) or 0)
    total = h * 3600 + mi * 60 + se
    return total if total >= 0 else None


def extract_start_time(url: str) -> tuple[str, float | None]:
    """Pull &t=<...> out of the URL. Returns (clean_url, start_seconds_or_None)."""
    split = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
    keep = []
    start = None
    for k, v in qs:
        if k == "t" and start is None:
            start = parse_yt_time(v)
        else:
            keep.append((k, v))
    new_query = urllib.parse.urlencode(keep)
    clean = urllib.parse.urlunsplit((split.scheme, split.netloc, split.path, new_query, ""))
    return clean, start


_PLAYABLE_EXTS = {".mkv", ".mp4", ".webm"}
_FORMAT_STREAM_RE = re.compile(r"\.f\d+$")  # `[id].f248.webm` orphan streams


def find_local(video_id: str) -> Path | None:
    """Find a PLAYABLE library file for `video_id`. Excludes yt-dlp
    intermediate artifacts (`.part`/`.ytdl`/`.tmp`) AND format-stream
    sidecars (`[id].f248.webm` / `.f251.webm`) that yt-dlp leaves behind
    when the merge step never ran — those are video-only or audio-only
    and would play wrong if treated as a complete file. Mirrors the same
    filter logic in pipeline/yt_play.py:has_local_file()."""
    if not LIBRARY.exists():
        return None
    needle = f"[{video_id}]"
    for path in LIBRARY.rglob("*"):
        if not path.is_file() or needle not in path.name:
            continue
        suf = path.suffix.lower()
        if suf in {".part", ".ytdl", ".tmp"}:
            continue
        if suf == ".webm" and _FORMAT_STREAM_RE.search(path.stem):
            continue
        if suf in _PLAYABLE_EXTS:
            return path
    return None


# Progress receiver URL. The music satellite sets MUSIC_PROGRESS_URL when
# it spawns this CLI as a child — that points back at the daemon's
# /download/progress route. Without the env var we fall back to the
# legacy JarvYZ endpoint (works pre-satellite-migration so direct CLI use
# from tampermonkey / Windows protocol handler keeps reporting to JarvYZ).
# Soft-fail: receiver down → POST raises → swallowed.
# Memory windows_localhost_quirk: 127.0.0.1 not localhost.
JARVIS_PROGRESS_URL = os.environ.get(
    "MUSIC_PROGRESS_URL",
    "http://127.0.0.1:8765/api/media/download_progress",
)
_POST_INTERVAL_S = 0.5

# yt-dlp --newline progress shape, e.g.
#   [download]  35.2% of  100.00MiB at  5.12MiB/s ETA 00:08
# Made permissive (whitespace + "~" estimate marker + optional fragments).
_PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%\s+of\s+~?\s*"
    r"(?P<total>\S+)"
    r"(?:\s+at\s+(?P<rate>\S+))?"
    r"(?:\s+ETA\s+(?P<eta>[\d:-]+))?",
    re.I,
)
_DEST_RE = re.compile(r"Destination:\s+(?P<path>.+)$")


def _post_progress(payload: dict) -> None:
    """POST one progress update to JarvYZ. Soft-fail."""
    try:
        req = urllib.request.Request(
            JARVIS_PROGRESS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=0.5).close()
    except Exception:
        pass


def download(url: str) -> int:
    LIBRARY.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(YTDLP),
        "-o", str(LIBRARY / "%(uploader)s" / "%(title)s [%(id)s].%(ext)s"),
        "--download-archive", str(ARCHIVE),
        "-f", "bv*[height<=1080]+ba/b",
        "--merge-output-format", "mkv",
        "--write-subs", "--sub-langs", "en,de",
        "--embed-subs", "--embed-metadata", "--embed-thumbnail",
        "--sponsorblock-mark", "all",
        "--no-overwrites",
        "--no-playlist",
        # --newline: one progress line per refresh instead of \r-repaint. We
        # parse + echo each line so the console keeps the friendly yt-dlp UI
        # while we also POST to JarvYZ for the Music page chip.
        "--newline",
    ]
    # Hand yt-dlp our resolved ffmpeg (live-PATH / winget aware) so the
    # video+audio merge and thumbnail/metadata embed happen even when yt-dlp's
    # own inherited PATH is stale — otherwise it leaves the streams unmerged
    # (video-only + audio + thumbnail as separate files).
    ff_dir = _ffmpeg_dir()
    if ff_dir:
        cmd += ["--ffmpeg-location", ff_dir]
    cmd.append(url)
    print(f"[yt-play] downloading: {url}", flush=True)

    video_id = extract_video_id(url) or ""
    dl_id = hashlib.md5(f"{url}-{time.time()}".encode("utf-8")).hexdigest()[:16]

    _post_progress({
        "id": dl_id, "status": "starting", "url": url,
        "video_id": video_id, "title": "", "percent": 0.0,
    })

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        # Hand UTF-8 / mojibake gracefully — yt-dlp output sometimes contains
        # non-ASCII titles. text=True alone uses the locale codepage on Win.
        encoding="utf-8",
        errors="replace",
    )

    last_post   = 0.0
    last_pct    = 0.0
    last_eta    = ""
    last_rate   = ""
    last_title  = ""

    assert proc.stdout is not None
    for line in proc.stdout:
        # Echo so the console window still shows the normal-looking yt-dlp UI.
        sys.stdout.write(line)
        sys.stdout.flush()

        m_dest = _DEST_RE.search(line)
        if m_dest:
            # Title = filename stem (which yt-dlp interpolated from
            # %(title)s + " [<id>]"). Good enough for the chip.
            try:
                last_title = Path(m_dest.group("path").strip()).stem
            except Exception:
                pass

        m = _PROGRESS_RE.search(line)
        if m:
            try:
                last_pct = float(m.group("pct"))
            except (TypeError, ValueError):
                pass
            last_eta  = (m.group("eta")  or "")
            last_rate = (m.group("rate") or "")

            now = time.monotonic()
            if now - last_post >= _POST_INTERVAL_S:
                _post_progress({
                    "id": dl_id, "status": "downloading", "url": url,
                    "video_id": video_id, "title": last_title,
                    "percent": last_pct, "eta": last_eta, "rate": last_rate,
                })
                last_post = now

    rc = proc.wait()
    _post_progress({
        "id": dl_id,
        "status": "done" if rc == 0 else "error",
        "url": url, "video_id": video_id, "title": last_title,
        "percent": 100.0 if rc == 0 else last_pct,
        "exit_code": rc,
    })
    return rc


IPC_SENT   = "sent"
IPC_NO_MPV = "no-mpv"
IPC_ERROR  = "error"


def _open_ipc():
    """Connect to the running mpv's JSON-IPC endpoint.

    Returns a binary file-like with .write/.flush/.close. Raises
    FileNotFoundError if mpv isn't there. Windows: named pipe → open() as
    a file. Linux/macOS: Unix domain socket wrapped with makefile() so the
    caller sees the same file-like interface."""
    path = _ipc_path()
    if _IS_WIN:
        return open(path, "r+b", buffering=0)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        s.close()
        raise FileNotFoundError(str(e)) from e
    except OSError:
        s.close()
        raise
    f = s.makefile("rwb", buffering=0)
    f._jarvis_sock = s  # type: ignore[attr-defined]  # keep socket alive
    return f


def ipc_send(commands: list[dict], retries: int = 4, delay: float = 0.05) -> str:
    """Send JSON-IPC commands to a running mpv.

    Returns IPC_SENT on success, IPC_NO_MPV if the endpoint doesn't exist
    (mpv isn't running — caller should launch one), or IPC_ERROR if the
    endpoint is present but unreachable (mpv is up but busy / pipe in bad
    state — caller should *not* launch a duplicate mpv; mpv.net's
    single-instance handler would forward the file as a queue item, not a
    replace, which silently breaks "play now")."""
    last_err = None
    payload = b"".join((json.dumps(c) + "\n").encode("utf-8") for c in commands)
    for _ in range(retries):
        try:
            f = _open_ipc()
        except FileNotFoundError:
            return IPC_NO_MPV
        except OSError as e:
            last_err = e
            time.sleep(delay)
            continue
        try:
            f.write(payload)
            f.flush()
            return IPC_SENT
        except OSError as e:
            last_err = e
            time.sleep(delay)
        finally:
            try: f.close()
            except OSError: pass
    print(f"[yt-play] ipc unreachable after {retries} tries: {last_err}", file=sys.stderr)
    return IPC_ERROR


def _watch_later_dirs() -> list[Path]:
    """All plausible mpv watch_later directories on this system. Windows:
    APPDATA / LOCALAPPDATA under {mpv,mpv.net}/watch_later. Linux/macOS:
    XDG_CONFIG_HOME or ~/.config under mpv/watch_later, plus the legacy
    ~/.mpv path."""
    candidates: list[Path | None] = []
    if _IS_WIN:
        appdata = os.environ.get("APPDATA", "")
        local   = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            Path(appdata) / "mpv.net" / "watch_later" if appdata else None,
            Path(appdata) / "mpv"     / "watch_later" if appdata else None,
            Path(local)   / "mpv.net" / "watch_later" if local   else None,
            Path(local)   / "mpv"     / "watch_later" if local   else None,
        ]
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"
        candidates = [
            base / "mpv" / "watch_later",
            Path.home() / ".mpv" / "watch_later",  # legacy
        ]
    return [d for d in candidates if d and d.exists()]


def clear_watch_later(file_path: Path) -> bool:
    """Delete mpv's saved-position entry. Returns True if anything was deleted.

    mpv hashes the file path with MD5 to name the watch-later file. The exact
    string it hashes depends on mpv version, platform, and how it was loaded
    (relative vs absolute, slash flavor). We brute-force every plausible form
    so we don't have to guess.
    """
    wl_dirs = _watch_later_dirs()
    if not wl_dirs:
        log_to_file("clear_watch_later: no watch_later dirs exist")
        return False

    raw_paths = {
        str(file_path),
        str(file_path.resolve()),
        str(file_path.absolute()),
    }
    paths_to_try = set(raw_paths)
    for p in raw_paths:
        paths_to_try.add(p.replace("\\", "/"))
        paths_to_try.add(p.lower())
        paths_to_try.add(p.replace("\\", "/").lower())

    log_to_file(f"clear_watch_later: dirs={[str(d) for d in wl_dirs]}")
    log_to_file(f"clear_watch_later: hashing {len(paths_to_try)} path variants")

    deleted_any = False
    for p in paths_to_try:
        digest = hashlib.md5(p.encode("utf-8")).hexdigest()
        for variant in (digest, digest.upper()):
            for wl_dir in wl_dirs:
                target = wl_dir / variant
                if target.exists():
                    try:
                        target.unlink()
                        log_to_file(f"clear_watch_later: REMOVED {target}")
                        deleted_any = True
                    except OSError as e:
                        log_to_file(f"clear_watch_later: FAIL {target}: {e}")

    if not deleted_any:
        # Diagnostic: dump what IS in the dirs, so we can reverse-engineer the hash.
        for wl_dir in wl_dirs:
            try:
                entries = sorted(p.name for p in wl_dir.iterdir())
                log_to_file(f"clear_watch_later: contents {wl_dir}: {entries[:20]}")
            except OSError:
                pass
    return deleted_any


def play(path: Path, mode: str, start: float | None = None, audio_delay_ms: int = 0, no_video: bool = False) -> None:
    mpv_mode = MODE_TO_MPV[mode]
    print(f"[yt-play] mode={mode} ({mpv_mode}) start={start}: {path}", flush=True)
    log_to_file(f"play: mode={mode} start={start} path={path}")

    cmd = ["loadfile", str(path), mpv_mode]
    if start is not None and start > 0:
        # Format whole numbers as integers ("start=120" vs "start=120.0") —
        # safer for mpv's option parser across builds.
        s = int(start) if float(start).is_integer() else start
        cmd.append(f"start={s}")

    # "Force from beginning" defense (start=0) is mode-dependent:
    # - mode="play" (replace): the file loads immediately and we have to win
    #   the same-file resume race → toggle resume-playback false around the
    #   loadfile + final seek to 0.
    # - mode="next" / "queue" (insert-next-play / append-play): the file
    #   isn't loaded right now, only queued. Toggling resume-playback or
    #   seeking would affect the *currently playing* track, not the queued
    #   one. Just clear the on-disk watch-later so when mpv eventually plays
    #   the queued file, it starts from 0.
    commands: list[dict] = []
    if start == 0:
        clear_watch_later(path)
        if mode == "play":
            commands.append({"command": ["set_property", "resume-playback", False]})
            commands.append({"command": cmd})
            commands.append({"command": ["set_property", "resume-playback", True]})
            commands.append({"command": ["seek", 0, "absolute"]})
        else:
            commands.append({"command": cmd})
    else:
        commands.append({"command": cmd})

    # Clicking play implies "actually play this" — clear any leftover
    # pause state from the previous track. mpv keeps pause=True across
    # loadfile in `replace` mode, so without this a paused mpv stays
    # paused on the new track (silent "click play and nothing happens").
    # Only on mode=play; next/queue shouldn't unpause a deliberately
    # paused current track.
    if mode == "play":
        commands.append({"command": ["set_property", "pause", False]})

    log_to_file(f"ipc commands: {commands}")
    result = ipc_send(commands)
    log_to_file(f"ipc result: {result}")

    if result == IPC_SENT:
        return
    if result == IPC_ERROR:
        # mpv IS running but the pipe is busy/broken. Don't launch a duplicate —
        # mpv.net would forward our file to the existing instance as an append,
        # silently turning "play now" into "queue".
        print("[yt-play] pipe unreachable but mpv likely running — aborting (try again)", file=sys.stderr)
        return

    # IPC_NO_MPV — launch a fresh mpv. We pass --input-ipc-server explicitly
    # so this works whether or not the user has it in mpv.conf, and so the
    # path stays in sync with what ipc_send connects to (single source of
    # truth via _ipc_path()).
    print("[yt-play] no running mpv — launching", flush=True)
    log_to_file("launching new mpv instance")
    mpv_bin = _mpv_bin()
    args = [str(mpv_bin), f"--input-ipc-server={_ipc_path()}"]
    if not _IS_WIN:
        # WSLg-on-Linux audio chain: PulseAudio default sink is the RDP
        # audio channel (44.1 kHz s16le) and mpv outputs 48 kHz float by
        # default — every frame does resample + format conversion AND rides
        # over RDP (high jitter). 1 s buffer + native sink rate + native
        # sink format = zero in-Pulse conversion, raw bytes to RDP.
        #
        # `--vo=x11`: WSLg's default Wayland surface negotiation hangs mpv's
        # video output initialization indefinitely — mpv stays "alive" but
        # never starts the playback clock, which also blocks audio (mpv
        # waits for ALL AOs+VOs to be ready). Xwayland routes reliably.
        # Skipped when --no-video is in effect (no video output to bind).
        args.extend([
            "--audio-buffer=1.0",
            "--audio-samplerate=44100",  # match RDPSink rate, skip resampling
            "--audio-format=s16",         # match RDPSink format, skip dither
            "--vo=x11",                   # WSLg-Wayland hangs; Xwayland works
            "--mute=no",                  # defend against stale mute state
            "--aid=auto",                 # defend against stale aid=no state
        ])
    if start is not None and start > 0:
        args.append(f"--start={start}")
    if audio_delay_ms:
        # POSITIVE input = compensate for downstream delay → mpv negative seconds.
        seconds = -audio_delay_ms / 1000.0
        args.append(f"--audio-delay={seconds}")
    if no_video:
        # Skip video render+ship entirely — audio gets the full RDP channel.
        # Fixes the chunked/low-FPS-feeling audio that WSLg's shared
        # audio+video RDP transport produces under load.
        args.append("--no-video")
    args.append(str(path))

    # Detach: child outlives this script. Windows = DETACHED_PROCESS flag;
    # POSIX = start_new_session (calls setsid, same effect).
    if _IS_WIN:
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(args, creationflags=DETACHED_PROCESS, close_fds=True)
    else:
        subprocess.Popen(
            args,
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def set_delay(seconds: float) -> int:
    print(f"[yt-play] set audio-delay={seconds:+.3f}s", flush=True)
    result = ipc_send([
        {"command": ["set_property", "audio-delay", seconds]},
    ])
    if result == IPC_SENT:
        return 0
    if result == IPC_NO_MPV:
        print("[yt-play] mpv not running — delay not applied", file=sys.stderr)
    return 1


def _try_daemon_delegate(parsed: dict) -> bool:
    """Try to hand this invocation off to the music daemon.

    Returns True if delegation succeeded (caller should exit cleanly).
    Returns False if no daemon is reachable OR delegation is intentionally
    skipped — caller continues with the standalone code path.

    Skipped when:
      - We're already a daemon child (MUSIC_PROGRESS_URL set) → would
        cause infinite recursion.
      - User passed an explicit --library override → respect their intent
        for a per-call library root (the daemon uses its own setting).

    Health probe timeout is intentionally short (0.4 s) so a missing
    daemon doesn't slow down standalone use (tampermonkey, manual CLI)."""
    if os.environ.get("MUSIC_PROGRESS_URL"):
        return False  # we ARE the daemon's child
    if parsed.get("library"):
        return False  # user asked for explicit library — standalone honors it
    base = os.environ.get("MUSIC_SERVER_URL", "http://127.0.0.1:9002")
    try:
        req = urllib.request.Request(f"{base}/health", method="GET")
        with urllib.request.urlopen(req, timeout=0.4) as resp:
            if resp.status != 200:
                return False
    except Exception:
        return False  # daemon not running — fall through silently

    cmd = parsed.get("cmd")
    try:
        if cmd in MODE_TO_MPV:
            body = json.dumps({
                "url": parsed.get("url", ""),
                "mode": cmd,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/play", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
        elif cmd == "delay":
            ms = int(parsed.get("seconds", 0.0) * -1000.0)  # seconds → ms (sign flip back)
            body = json.dumps({"action": "audio_delay", "value": ms}).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/control", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
        else:
            return False
        with urllib.request.urlopen(req, timeout=5) as resp:
            log_to_file(f"daemon delegate: {cmd} → {resp.status}")
            return resp.status < 400
    except Exception as e:
        print(f"[yt-play] daemon delegation failed ({e}) — falling back to standalone",
              file=sys.stderr)
        return False


def main(argv: list[str]) -> int:
    log_to_file(f"--- invocation: argv={argv[1:]}")
    parsed = parse_args(argv)

    # --library override rebinds the module-level paths so every
    # downstream call (download, find_local, log_to_file, archive
    # writes, watch_later) sees the same root. Default stays at
    # D:\Media\YouTube when the flag is absent (protocol-handler
    # invocations from tampermonkey don't pass it).
    if parsed.get("library"):
        global LIBRARY, ARCHIVE, LOG_FILE
        LIBRARY = Path(parsed["library"])
        ARCHIVE = LIBRARY / ".archive.txt"
        LOG_FILE = LIBRARY / ".yt-play.log"
        log_to_file(f"library override: {LIBRARY}")

    # Phase 3: if the music satellite daemon is up, hand off via HTTP.
    # Falls through to the standalone path if not — preserves zero-deps
    # tampermonkey + manual-CLI flows.
    if _try_daemon_delegate(parsed):
        return 0

    if parsed["cmd"] == "delay":
        return set_delay(parsed["seconds"])

    url = parsed["url"]
    mode = parsed["cmd"]

    # Pull &t=<...> off for use as mpv start position; yt-dlp ignores it anyway,
    # but the clean URL keeps yt-dlp's caching/archive logic uncluttered.
    url, start = extract_start_time(url)

    vid = extract_video_id(url)
    if not vid:
        print(f"[yt-play] could not extract video ID from: {url}", file=sys.stderr)
        return 1

    local = find_local(vid)
    if local is None:
        rc = download(url)
        if rc != 0:
            print(f"[yt-play] yt-dlp exited {rc}", file=sys.stderr)
            return rc
        local = find_local(vid)
        if local is None:
            print("[yt-play] download finished but file not found in library", file=sys.stderr)
            return 1

    play(local, mode, start,
         audio_delay_ms=parsed.get("audio_delay_ms", 0),
         no_video=parsed.get("no_video", False))
    return 0


def main_argv() -> None:
    """Console-script entry point. The `yt-play` binary installed by the
    wheel (see pyproject.toml [project.scripts]) calls this — no argument.
    `main(argv)` stays the testable form."""
    sys.exit(main(sys.argv))


if __name__ == "__main__":
    main_argv()
