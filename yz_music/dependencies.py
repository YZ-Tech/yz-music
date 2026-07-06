"""Health-check for external binaries the satellite depends on.

`yt-dlp` and `mpv` aren't Python deps — they're OS-level tools the user
installs out-of-band. This module surfaces them in the UI so new users
see exactly what's missing and how to fix it, and existing users get a
nudge when their yt-dlp falls behind YouTube's latest player change.

The lookup hierarchy mirrors `yz_music.cli._yt_dlp_bin` + `_mpv_bin`:
PATH > venv-sibling > Windows WinGet hard-coded fallback.

Latest-version check hits the GitHub releases API. Cached 1 hour
in-memory per binary so reloading the Music page doesn't hammer the
60-req/hour unauthenticated limit. Cache miss + network failure → we
report `latest=None` (the UI shows "couldn't check") rather than fail
the whole probe.

Install hint dispatch is conservative:
- yt-dlp on Linux/WSL recommends `pip install -U --user yt-dlp` because
  apt is months behind YouTube's player updates.
- mpv on Linux/WSL recommends `apt install mpv` (apt is fine for mpv —
  it doesn't break with platform updates).
- Windows uses `winget install ...` (works since Windows 10 1809+).
- macOS uses `brew install ...`.

Auto-update: each binary returns `auto_update_cmd` separately. The UI
shows that command as a clickable "Run update" button. The satellite's
`/dependencies/update` endpoint invokes it if the caller opted in
(admin context — see auto_update_cmd docstring)."""
from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .cli import _mpv_bin, _yt_dlp_bin


# ────────────────────────── cache ─────────────────────────────────


# Module-singleton cache of GitHub latest-release lookups. Bounded
# trivially (2 keys). 1h TTL chosen to stay well within the 60-req/h
# unauthenticated limit even with multiple browser sessions hitting
# /dependencies on page load.
_LATEST_TTL_SECONDS = 3600.0
_latest_cache: dict[str, tuple[float, str | None]] = {}
_cache_lock = threading.Lock()


def _fetch_latest(source: str) -> str | None:
    """Resolve `latest` for the given source URI. The URI prefix selects
    the resolver:

    - `github:owner/repo` — GitHub releases API (cached 1h to stay under
      the 60-req/h unauthenticated limit). Strips a leading `v` if
      present (mpv tags are `v0.40.0`; yt-dlp tags are bare
      `2025.05.22`).
    - `apt:package` — `apt-cache policy <package>`, returns the
      Candidate line stripped of Debian -1ubuntuX suffix so it compares
      cleanly against what `<binary> --version` reports. Not cached:
      apt's metadata is local + fast.

    Returns None on any failure — UI surfaces 'couldn't check' rather
    than going red."""
    scheme, _, target = source.partition(":")
    if not target:
        return None
    if scheme == "github":
        return _fetch_latest_github(target)
    if scheme == "apt":
        return _fetch_latest_apt(target)
    return None


def _fetch_latest_github(repo: str) -> str | None:
    """Latest release tag for `owner/repo` via GitHub releases API.
    1h memo cache so reloading /dependencies doesn't burn the 60-req/h
    unauthenticated limit."""
    now = time.time()
    cache_key = f"github:{repo}"
    with _cache_lock:
        cached = _latest_cache.get(cache_key)
        if cached and now - cached[0] < _LATEST_TTL_SECONDS:
            return cached[1]
    try:
        r = httpx.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=4.0,
            follow_redirects=True,
        )
        if r.status_code != 200:
            tag = None
        else:
            tag = (r.json().get("tag_name") or "").lstrip("v") or None
    except httpx.HTTPError:
        tag = None
    with _cache_lock:
        _latest_cache[cache_key] = (now, tag)
    return tag


_APT_CANDIDATE_RE = re.compile(r"^\s*Candidate:\s*(\S+)", re.MULTILINE)
# Strip Debian-style suffix so apt's "0.34.1-1ubuntu3" matches mpv's
# self-reported "0.34.1". We keep epoch (NN:) and upstream version,
# discard everything after the first `-` (Debian revision / ubuntu tag).
_DEB_REVISION_RE = re.compile(r"-[^-]+$")


def _fetch_latest_apt(package: str) -> str | None:
    """Latest version `apt upgrade <package>` could install. Reads
    `apt-cache policy <package>` and pulls the Candidate line. Empty
    output / parse fail / apt missing → None."""
    if not shutil.which("apt-cache"):
        return None
    try:
        out = subprocess.run(
            ["apt-cache", "policy", package],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = _APT_CANDIDATE_RE.search(out.stdout or "")
    if not m:
        return None
    raw = m.group(1)
    # "(none)" → apt has no candidate (package not in repos)
    if raw == "(none)":
        return None
    # Strip Debian revision suffix (-1ubuntu3 etc.); strip optional epoch (1:).
    stripped = _DEB_REVISION_RE.sub("", raw)
    if ":" in stripped:
        stripped = stripped.split(":", 1)[1]
    return stripped or None


# ────────────────────────── version probes ────────────────────────


_YT_VERSION_RE = re.compile(r"\d{4}\.\d{2}\.\d{2}(?:\.\d+)?")
# mpv first line: "mpv 0.40.0 ..." or "mpv-player 0.x.y ..."
_MPV_VERSION_RE = re.compile(r"^mpv(?:-\S+)?\s+v?(\d+\.\d+(?:\.\d+)?)")
# ffmpeg version line varies a lot by build:
#   "ffmpeg version 7.1.1 ..."                     (clean release)
#   "ffmpeg version 7.1-full_build-www.gyan.dev…"  (Gyan/Windows)
#   "ffmpeg version 4.4.2-0ubuntu0.22.04.1 ..."    (apt)
#   "ffmpeg version N-118602-gd21ed2298e-20250303" (git master / nightly)
# Capture a clean numeric for release/apt builds (so the apt freshness check
# can compare), else the full git build id. group(1) either way.
_FFMPEG_VERSION_RE = re.compile(r"^ffmpeg version (n?\d+\.\d+(?:\.\d+)?|N-\d+\S*)")


def _probe_version(
    binary_path: Path, parser: re.Pattern[str], flag: str = "--version"
) -> str | None:
    """Run `<binary> <flag>` (default `--version`; ffmpeg wants `-version`),
    parse the first matching version. Returns None on any failure (binary
    missing, slow, prints garbage)."""
    try:
        out = subprocess.run(
            [str(binary_path), flag],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    blob = (out.stdout or "") + "\n" + (out.stderr or "")
    for line in blob.splitlines():
        m = parser.search(line)
        if m:
            return m.group(0 if parser is _YT_VERSION_RE else 1)
    return None


# ────────────────────────── version compare ───────────────────────


def _is_outdated(current: str | None, latest: str | None) -> bool | None:
    """Three-state: True (outdated), False (fresh), None (can't tell —
    one side missing, or unparseable version strings)."""
    if not current or not latest:
        return None
    try:
        cur_parts = [int(p) for p in re.split(r"\.", current)[:4]]
        lat_parts = [int(p) for p in re.split(r"\.", latest)[:4]]
    except ValueError:
        return None
    # Pad to equal length so 0.40 vs 0.40.0 compares correctly
    n = max(len(cur_parts), len(lat_parts))
    cur_parts += [0] * (n - len(cur_parts))
    lat_parts += [0] * (n - len(lat_parts))
    return cur_parts < lat_parts


# ────────────────────────── install hints ─────────────────────────
#
# Per-platform commands. Each binary has a `update_*` form (when found
# but outdated) AND an `install_*` form (when not found). For most
# package managers the commands are identical, but yt-dlp on Linux
# diverges: apt is fine for first-install but stays months behind, so
# update routes via pip.


def _platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# Each (binary, platform) → (label, install_cmd, update_cmd, docs_url).
# update_cmd is None when there's no good in-place updater (user fetches
# a new binary by hand) — the UI then hides the auto-update button.
# Each (binary, platform) entry carries:
#   label         — short description shown in the UI
#   install_cmd   — when the binary is missing
#   update_cmd    — when found but outdated relative to `source`
#   docs_url      — link rendered in the UI for manual install
#   source        — URI of what update_cmd can deliver. The `latest` field
#                   on /dependencies is resolved from THIS, not from
#                   upstream. Schemes: github:owner/repo, apt:package.
#   upstream_source (optional) — URI of upstream releases. When set AND
#                   the upstream version is newer than source, the UI
#                   surfaces a soft note ("v0.41 available upstream").
#                   Use this when the package manager you ship with
#                   typically lags upstream (apt + LTS distros). When
#                   source == upstream, omit.
#   upstream_note (optional) — extra UX text shown alongside the upstream
#                   divergence ("apt LTS is permanently behind; for newer
#                   mpv use flatpak / snap / build").
_INSTALL_HINTS: dict[str, dict[str, dict[str, Any]]] = {
    "ytdlp": {
        "windows": {
            "label": "WinGet (recommended on Windows)",
            # -e -s winget: exact id, community source ONLY. Without the
            # source pin winget also consults msstore, which demands
            # region + Terms-of-Transaction acceptance on fresh machines
            # and fails non-interactively (first bro-install, 2026-07-06).
            "install_cmd": "winget install -e -s winget yt-dlp.yt-dlp",
            "update_cmd": "winget upgrade -e -s winget yt-dlp.yt-dlp",
            "docs_url": "https://github.com/yt-dlp/yt-dlp/releases/latest",
            # WinGet tracks yt-dlp upstream releases tightly (auto-published
            # via WinGet's GitHub-watcher). Treat upstream as authoritative.
            "source": "github:yt-dlp/yt-dlp",
        },
        "linux": {
            # Linux yt-dlp has multiple viable installers — pick whichever
            # is already available, falling back through the chain.
            # `alternatives` is ordered by preference. _resolve_hint()
            # picks the first whose `requires_bin` is on PATH.
            #
            # Why not pip --user: bare Ubuntu doesn't ship python3-pip;
            # ~/.local/bin may not be on PATH; `sudo pip install --user`
            # is a broken combo (writes to /root/.local, invisible).
            # uv + pipx both fix all three (isolated venv, ensurepath,
            # no sudo). curl is the universal last resort.
            "docs_url": "https://github.com/yt-dlp/yt-dlp/wiki/Installation",
            "source": "github:yt-dlp/yt-dlp",
            "alternatives": [
                {
                    "requires_bin": "uv",
                    "label": "uv tool (user-level, no sudo)",
                    "install_cmd": "uv tool install yt-dlp",
                    # --force makes update idempotent: works whether
                    # yt-dlp is currently uv-installed or not (e.g.
                    # transitioning from apt's old yt-dlp at /usr/bin).
                    # uv tool upgrade alone would fail with "not
                    # installed via uv" in that case.
                    "update_cmd": "uv tool install --force yt-dlp",
                },
                {
                    "requires_bin": "pipx",
                    "label": "pipx (user-level, isolated venv, no sudo)",
                    "install_cmd": "pipx install yt-dlp",
                    # --force same rationale as uv. Works for fresh
                    # install AND upgrade-of-existing-pipx-install.
                    "update_cmd": "pipx install --force yt-dlp",
                },
                {
                    "requires_bin": "curl",
                    "label": "official static binary (sudo, writes to /usr/local/bin)",
                    "install_cmd": (
                        "sudo curl -fL "
                        "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp "
                        "-o /usr/local/bin/yt-dlp && "
                        "sudo chmod a+rx /usr/local/bin/yt-dlp"
                    ),
                    "update_cmd": (
                        "sudo curl -fL "
                        "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp "
                        "-o /usr/local/bin/yt-dlp && "
                        "sudo chmod a+rx /usr/local/bin/yt-dlp"
                    ),
                },
            ],
            # Fallback values when NO alternative matches (curl not even
            # available — extreme edge case). Surfaced as copy-paste only.
            "label": "no installer detected — please install uv or curl",
            "install_cmd": (
                "# Install uv first:\n"
                "curl -LsSf https://astral.sh/uv/install.sh | sh\n"
                "# Then:\n"
                "uv tool install yt-dlp"
            ),
            "update_cmd": (
                "# Install uv first:\n"
                "curl -LsSf https://astral.sh/uv/install.sh | sh\n"
                "# Then:\n"
                "uv tool upgrade yt-dlp"
            ),
        },
        "macos": {
            "label": "Homebrew",
            "install_cmd": "brew install yt-dlp",
            "update_cmd": "brew upgrade yt-dlp",
            "docs_url": "https://github.com/yt-dlp/yt-dlp/wiki/Installation",
            # brew core tracks yt-dlp fast; treat upstream as authoritative.
            "source": "github:yt-dlp/yt-dlp",
        },
    },
    "mpv": {
        "windows": {
            "label": "WinGet (mpv.net is what JarvYZ spawns on Windows)",
            "install_cmd": "winget install -e -s winget stax76.mpvnet",
            "update_cmd": "winget upgrade -e -s winget stax76.mpvnet",
            "docs_url": "https://github.com/mpvnet-player/mpv.net/releases/latest",
            # mpv.net usually tracks mpv but on its own cadence; using
            # upstream mpv-player/mpv as `latest` is close enough for
            # the "fresh enough?" check.
            "source": "github:mpv-player/mpv",
        },
        "linux": {
            "label": "apt",
            "install_cmd": "sudo apt install -y mpv",
            "update_cmd": "sudo apt upgrade -y mpv",
            "docs_url": "https://mpv.io/installation/",
            # apt's mpv on Ubuntu LTS is permanently old. Resolve `latest`
            # from apt's candidate line — that's what update_cmd can
            # actually deliver — and surface upstream's release as a
            # soft note when it's newer.
            "source": "apt:mpv",
            "upstream_source": "github:mpv-player/mpv",
            "upstream_note": (
                "apt on Ubuntu LTS is permanently behind upstream mpv. "
                "For a newer mpv, use flatpak (`flatpak install flathub io.mpv.Mpv`), "
                "snap (`snap install mpv`), or build from source."
            ),
        },
        "macos": {
            "label": "Homebrew",
            "install_cmd": "brew install mpv",
            "update_cmd": "brew upgrade mpv",
            "docs_url": "https://mpv.io/installation/",
            "source": "github:mpv-player/mpv",
        },
    },
    "ffmpeg": {
        # Needed for library thumbnail extraction (single-frame grab). Not
        # version-sensitive — any reasonably recent ffmpeg works — so only
        # linux declares a `source` (apt gives the freshness check for free);
        # win/mac omit it (no clean canonical "latest" feed) → the UI just
        # shows found / install, no outdated nudge.
        "windows": {
            "label": "WinGet",
            "install_cmd": "winget install -e -s winget Gyan.FFmpeg",
            "update_cmd": "winget upgrade -e -s winget Gyan.FFmpeg",
            "docs_url": "https://ffmpeg.org/download.html",
        },
        "linux": {
            "label": "apt",
            "install_cmd": "sudo apt install -y ffmpeg",
            "update_cmd": "sudo apt upgrade -y ffmpeg",
            "docs_url": "https://ffmpeg.org/download.html",
            "source": "apt:ffmpeg",
        },
        "macos": {
            "label": "Homebrew",
            "install_cmd": "brew install ffmpeg",
            "update_cmd": "brew upgrade ffmpeg",
            "docs_url": "https://ffmpeg.org/download.html",
        },
    },
}


# ──────────────────── hint + path resolution ────────────────────


def _resolve_hint(name: str, platform: str) -> dict[str, Any]:
    """Pick the right hint for the current environment.

    Hints may declare an `alternatives: [...]` list ordered by
    preference. Each alternative carries a `requires_bin` field;
    we walk the list, pick the first whose binary is on PATH,
    and merge it over the base. Other top-level fields (source,
    docs_url, upstream_*) flow through unchanged.

    If no alternative matches, the base's own label/install_cmd/
    update_cmd is used as-is (intended as a "please install <one of
    these>" copy-paste fallback). `alternatives` itself is stripped
    from the response — it's an internal detail."""
    hint = _INSTALL_HINTS[name][platform]
    alternatives = hint.get("alternatives", [])
    for alt in alternatives:
        req = alt.get("requires_bin")
        if not req or shutil.which(req):
            merged = {**hint, **alt}
            merged.pop("alternatives", None)
            merged.pop("requires_bin", None)
            return merged
    # No alternative matched (or hint had none) — return the base
    # as-is, sans the alternatives key.
    return {k: v for k, v in hint.items() if k != "alternatives"}


def _resolve_ytdlp_path() -> Path:
    """`yt-dlp` lookup. Prefers `~/.local/bin/yt-dlp` over the system
    PATH because that's where `uv tool install` / `pip install --user`
    land, and on an interactive shell ~/.local/bin sorts first in PATH
    anyway — but the satellite daemon may not have ~/.profile-derived
    PATH and would otherwise pick the older apt-installed binary at
    /usr/bin/yt-dlp.

    Order:
    1. ~/.local/bin/yt-dlp (user-level install — uv / pip --user)
    2. /usr/local/bin/yt-dlp (curl static-binary install)
    3. _yt_dlp_bin() — venv-sibling / PATH / WinGet fallback (cli.py)"""
    user_local = Path.home() / ".local" / "bin" / "yt-dlp"
    if user_local.exists():
        return user_local
    usr_local = Path("/usr/local/bin/yt-dlp")
    if usr_local.exists():
        return usr_local
    return _yt_dlp_bin()


def _ffmpeg_bin() -> Path:
    """ffmpeg lookup via PATH. Falls back to a bare `ffmpeg` Path (whose
    .exists() is False) so status() cleanly reports not-found. Mirrors the
    `shutil.which("ffmpeg")` the thumbnail route uses."""
    found = shutil.which("ffmpeg")
    return Path(found) if found else Path("ffmpeg")


def _bin_for(name: str) -> Path:
    """Binary path for a dependency — the shared resolver behind both
    status() and run_update()'s install-vs-update decision."""
    if name == "ytdlp":
        return _resolve_ytdlp_path()
    if name == "mpv":
        return _mpv_bin()
    if name == "ffmpeg":
        return _ffmpeg_bin()
    raise ValueError(f"unknown dependency: {name}")


# ────────────────────────── public API ────────────────────────────


def status() -> dict[str, Any]:
    """Snapshot for the UI. Cheap enough to call on every page load —
    the latest-version GitHub hits are cached 1h. The version probes
    are subprocess calls (~10–50 ms each), runs in the request thread.
    Acceptable: dependencies card is on-demand, not on the hot path."""
    return {
        "platform": _platform_key(),
        "ytdlp": _status_for("ytdlp"),
        "mpv": _status_for("mpv"),
        "ffmpeg": _status_for("ffmpeg"),
    }


def _status_for(name: str) -> dict[str, Any]:
    version_flag = "--version"
    if name == "ytdlp":
        bin_path = _resolve_ytdlp_path()
        version_re = _YT_VERSION_RE
    elif name == "mpv":
        bin_path = _mpv_bin()
        version_re = _MPV_VERSION_RE
    elif name == "ffmpeg":
        bin_path = _ffmpeg_bin()
        version_re = _FFMPEG_VERSION_RE
        version_flag = "-version"  # ffmpeg's flag is single-dash
    else:
        raise ValueError(f"unknown dependency: {name}")

    found = bin_path.exists()
    version = _probe_version(bin_path, version_re, version_flag) if found else None

    platform = _platform_key()
    hint = _resolve_hint(name, platform)

    # `latest` is resolved from what update_cmd can actually deliver.
    # For apt-backed mpv on LTS, that's "0.34.1" — the apt candidate —
    # not GitHub's "0.41.0". The chip turns green when installed matches
    # the package manager's available version, even when something newer
    # exists upstream that the package manager can't reach.
    # `source` is optional — when absent (e.g. ffmpeg on win/mac, no clean
    # canonical feed) we skip the freshness check entirely: latest/outdated
    # stay None and the UI shows found / install only.
    latest = _fetch_latest(hint["source"]) if hint.get("source") else None
    outdated = _is_outdated(version, latest)

    # Optional: when source != upstream, fetch upstream separately so the
    # UI can show a soft "newer version available via flatpak/build/..."
    # note. Only surfaced when there's an actual divergence.
    upstream_block: dict[str, Any] | None = None
    upstream_src = hint.get("upstream_source")
    if upstream_src and upstream_src != hint["source"]:
        upstream_ver = _fetch_latest(upstream_src)
        if upstream_ver and _is_outdated(latest, upstream_ver):
            # Upstream is genuinely newer than what update_cmd can deliver.
            upstream_block = {
                "version": upstream_ver,
                "source": upstream_src,
                "note": hint.get("upstream_note", ""),
            }

    return {
        "found": found,
        "path": str(bin_path) if found else None,
        "version": version,
        "latest": latest,
        # New field: where `latest` came from. UI uses this for the
        # tooltip ("via apt" / "via GitHub releases"). None when the dep
        # declares no source (no freshness check).
        "source": hint.get("source"),
        # Optional divergence note when upstream is newer than what
        # update_cmd can install. Absent when source == upstream or
        # when they happen to be in sync.
        "upstream": upstream_block,
        # outdated tri-state: True / False / None ("can't tell")
        "outdated": outdated,
        "install_hint": {
            "label": hint["label"],
            "install_cmd": hint["install_cmd"],
            "update_cmd": hint["update_cmd"],
            "docs_url": hint["docs_url"],
        },
    }


# ────────────────────────── auto-update ───────────────────────────


def run_update(name: str) -> dict[str, Any]:
    """Spawn the platform-appropriate update command for `name`,
    delegating elevation to the OS:

    - **Windows**: PowerShell `Start-Process -Verb RunAs -Wait` triggers
      UAC. User clicks Yes. Elevated child runs, we capture stdout/
      stderr via temp files. Synchronous — returns when the update
      completes (or UAC is denied).

    - **Linux / WSL with DISPLAY**: spawn a detached terminal window
      (gnome-terminal / konsole / tilix / xterm / x-terminal-emulator,
      first-found wins) running `sudo <cmd>`. sudo prompts inside the
      terminal — the terminal IS the elevation surface. The window
      stays open after the command runs ("Press Enter to close") so
      the user sees the result. Asynchronous — returns immediately
      after spawning; the UI's Re-check button picks up the new state.

    - **Linux headless / no terminal available / macOS**: fall back to
      a structured "copy this command" response. UI surfaces it with
      the install_hint.update_cmd already on the card.

    Caller (the proxy) is responsible for the user-explicit-opt-in gate
    (this is only ever called from a button click). This fn doesn't
    second-guess.

    Return shape — fields are all optional, UI reads what's present:
        ok                   → True iff the elevation flow began cleanly
        command              → the command that was / will be run
        kind                 → "sync"  (Windows; check exit_code/stderr)
                             | "async" (Linux; check spawned_terminal)
                             | "copy"  (no automation available)
        spawned_terminal     → terminal binary name (Linux async)
        exit_code            → present on sync only
        stdout / stderr      → present on sync only, capped at 4 KB
        error                → only on spawn-time failure
        message              → user-friendly status hint"""
    if name not in ("ytdlp", "mpv", "ffmpeg"):
        return {"ok": False, "kind": "copy", "error": f"unknown dependency: {name}"}
    platform = _platform_key()
    # Use the SAME resolver `status()` uses so the button's command
    # matches the hint the UI just rendered. Without this we'd execute
    # the base hint's update_cmd even when a preferred alternative
    # (uv/pipx/curl) is available.
    hint = _resolve_hint(name, platform)
    # Fresh machine → INSTALL, not upgrade. `winget upgrade` on a
    # never-installed package fails with "no installed package found"
    # (first bro-install field report, 2026-07-06).
    cmd = hint["update_cmd"] if _bin_for(name).exists() else hint["install_cmd"]

    if platform == "windows":
        return _run_update_windows(cmd)
    if platform == "linux":
        return _run_update_linux(cmd)
    # macOS + anything else falls through to copy-paste
    return {
        "ok": False,
        "kind": "copy",
        "command": cmd,
        "message": (
            f"Auto-update isn't wired for {platform} yet. "
            f"Copy the command and run it in a terminal."
        ),
    }


# ─────────────────────────── Windows: UAC ─────────────────────────


def _run_update_windows(cmd: str) -> dict[str, Any]:
    """Spawn the update command elevated via UAC. Synchronous —
    the UAC click + winget run typically completes in 5–30 s for a
    single package, so blocking the HTTP request is fine.

    We elevate `powershell.exe` (a real .exe — always resolvable, unlike
    the per-user `winget` App Execution Alias, which Start-Process can't
    find as a bare -FilePath in the elevated context) and have THAT shell
    run the update with its own `> / 2>` redirection to temp files. This
    is what makes capture work: `Start-Process -Verb RunAs` and
    `-RedirectStandardOutput/-RedirectStandardError` are mutually exclusive
    in PowerShell (the old code combined them, so the update silently
    failed before launching). The inner command goes over -EncodedCommand
    to avoid nested-quoting breakage."""
    parts = shlex.split(cmd, posix=False)
    if not parts:
        return {"ok": False, "kind": "copy", "command": cmd, "error": "empty command"}
    program, *_args = parts

    out_path: str | None = None
    err_path: str | None = None
    try:
        out_fd, out_path = tempfile.mkstemp(prefix="jarvis-upd-", suffix=".out")
        err_fd, err_path = tempfile.mkstemp(prefix="jarvis-upd-", suffix=".err")
        os.close(out_fd)
        os.close(err_fd)

        # winget needs to be told NOT to prompt — in a hidden elevated
        # window an interactive agreement/selection prompt would hang
        # until the 5-min timeout.
        full = cmd
        if program.lower().startswith("winget"):
            full += (
                " --accept-source-agreements --accept-package-agreements"
                " --disable-interactivity"
            )
        # Inner script the ELEVATED shell runs: do the update, redirect all
        # output to our temp files, and surface the real tool exit code.
        inner = f"{full} > '{out_path}' 2> '{err_path}'; exit $LASTEXITCODE"
        enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
        ps_script = (
            "$p = Start-Process -FilePath 'powershell.exe' "
            f"-ArgumentList '-NoProfile','-EncodedCommand','{enc}' "
            "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; "
            "exit $p.ExitCode"
        )

        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=300.0,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "kind": "sync",
                "command": cmd,
                "error": "update timed out after 5 min — copy the command and run it in a terminal",
            }

        # UAC denied → PowerShell exits with non-zero + a specific error
        # in stderr. Most other failure modes also surface via ps stderr.
        stdout = _read_capped(out_path, 4000)
        stderr = _read_capped(err_path, 4000)
        if proc.returncode != 0 and not stdout and not stderr:
            # Likely UAC denied or Start-Process threw before launching
            stderr = (proc.stderr or "").strip() or "PowerShell returned non-zero (possibly UAC denied)"

        return {
            "ok": proc.returncode == 0,
            "kind": "sync",
            "command": cmd,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "message": (
                "Update completed."
                if proc.returncode == 0
                else "Update failed — see output below (the UAC prompt may have been denied)."
            ),
        }
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "kind": "sync", "command": cmd, "error": str(e)}
    finally:
        for p in (out_path, err_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _read_capped(path: str, cap: int) -> str:
    """Read up to `cap` bytes from the end of `path`. Empty on missing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.read()
    except OSError:
        return ""
    return data[-cap:]


# ─────────────────────────── Linux / WSL: terminal ────────────────


# First-found wins. tilix is the default on this WSL install; gnome-terminal
# is the desktop-Linux default. Each tuple is (binary_name, argv builder).
# Builder takes the wrapped bash command and returns a Popen-ready list.
_LINUX_TERMINALS: list[tuple[str, Any]] = [
    ("gnome-terminal", lambda wrapped: ["gnome-terminal", "--", "bash", "-lc", wrapped]),
    ("konsole", lambda wrapped: ["konsole", "-e", "bash", "-lc", wrapped]),
    ("tilix", lambda wrapped: ["tilix", "-e", f"bash -lc {shlex.quote(wrapped)}"]),
    ("xterm", lambda wrapped: ["xterm", "-e", "bash", "-lc", wrapped]),
    ("x-terminal-emulator", lambda wrapped: ["x-terminal-emulator", "-e", "bash", "-lc", wrapped]),
]


def _run_update_linux(cmd: str) -> dict[str, Any]:
    """Spawn a terminal window running `sudo <cmd>`. The terminal is
    the elevation surface — sudo prompts there for the user's password.
    Asynchronous; we don't wait. The UI will tell the user to check
    the terminal window + click Re-check when done.

    Falls through to copy-paste on:
      - No DISPLAY (headless Linux server)
      - No supported terminal binary on PATH
      - Spawn-time exception"""
    if not os.environ.get("DISPLAY"):
        return {
            "ok": False,
            "kind": "copy",
            "command": cmd,
            "message": (
                "No DISPLAY available (looks like a headless box). "
                "Copy the command and run it in a terminal."
            ),
        }

    # Wrap so the terminal:
    #   1. Shows what's about to run
    #   2. Runs the command verbatim — each hint declares its own sudo
    #      (apt commands need it; uv tool / brew don't). Sudo prompts
    #      inside this terminal if the command starts with it.
    #   3. Reports the exit code
    #   4. Waits for Enter so the user can read output before window closes
    wrapped = (
        f"echo '════════════════════════════════════════════════════════════'; "
        f"echo '   JarvYZ update — {cmd}'; "
        f"echo '════════════════════════════════════════════════════════════'; "
        f"echo; "
        f"{cmd}; "
        f"status=$?; "
        f"echo; "
        f"echo \"Exit code: $status\"; "
        f"echo 'Press Enter to close this window…'; "
        f"read"
    )

    for binary, build_argv in _LINUX_TERMINALS:
        if not shutil.which(binary):
            continue
        try:
            subprocess.Popen(
                build_argv(wrapped),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Don't inherit any open FDs (e.g. uvicorn's listening socket)
                # into the detached terminal — would leak the port.
                close_fds=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # Try the next candidate
            continue
        return {
            "ok": True,
            "kind": "async",
            "command": cmd,
            "spawned_terminal": binary,
            "message": (
                f"Update launched in a new {binary} window. "
                f"Type your sudo password there; the command output stays in that "
                f"window. Click 'Re-check' when you're done to refresh the version."
            ),
        }

    return {
        "ok": False,
        "kind": "copy",
        "command": cmd,
        "message": (
            "No supported terminal emulator found "
            f"(tried: {', '.join(t[0] for t in _LINUX_TERMINALS)}). "
            "Copy the command and run it in a terminal."
        ),
    }
