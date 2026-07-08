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
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .cli import _ffmpeg_bin, _mpv_bin, _yt_dlp_bin


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
            "install_cmd": "winget install -e -s winget mpv.net",
            "update_cmd": "winget upgrade -e -s winget mpv.net",
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
    """Install-or-update ONE dependency, delegating elevation to the OS. The
    install-vs-update decision is `_bin_for(name).exists()` — install when the
    binary is absent (a fresh machine), update when present.

    ASYNC on every platform: Windows launches ONE elevated, visible PowerShell
    (single UAC) and returns immediately; Linux spawns a terminal (sudo prompts
    there). We deliberately do NOT block on the install — the old synchronous
    Windows path (`subprocess.run(timeout=300)`) routinely blew past the 30s
    satellite proxy timeout and surfaced to the browser as a 500 even though the
    install completed. See `_spawn_elevated_windows`.

    Return shape (all optional; UI reads what's present):
        ok               → True iff the elevation surface launched cleanly
        kind             → "async" (window/terminal launched; UI re-checks)
                         | "copy"  (no automation available)
                         | "noop"  (nothing to do — batch only)
        command          → the command(s) that will run
        spawned_terminal → the launched surface's name
        error            → only on spawn-time failure
        message          → user-friendly status hint"""
    if name not in ("ytdlp", "mpv", "ffmpeg"):
        return {"ok": False, "kind": "copy", "error": f"unknown dependency: {name}"}
    platform = _platform_key()
    # Use the SAME resolver `status()` uses so the button's command matches the
    # hint the UI just rendered (a preferred uv/pipx/curl alternative, etc).
    hint = _resolve_hint(name, platform)
    # THE check: fresh machine → install, present → update. (`winget upgrade` on
    # a never-installed package fails, so this decision is load-bearing.)
    cmd = hint["update_cmd"] if _bin_for(name).exists() else hint["install_cmd"]
    return _run_commands([cmd], [name], platform)


def _pending() -> list[tuple[str, str]]:
    """(name, cmd) for each binary that needs action — install_cmd when
    missing, update_cmd when found-but-outdated. Up-to-date and can't-tell
    ('outdated' is None, e.g. the source is offline) are skipped."""
    out: list[tuple[str, str]] = []
    for name in ("ytdlp", "mpv", "ffmpeg"):
        st = _status_for(name)
        hint = st["install_hint"]
        if not st["found"]:
            out.append((name, hint["install_cmd"]))
        elif st["outdated"] is True:
            out.append((name, hint["update_cmd"]))
    return out


def run_update_all() -> dict[str, Any]:
    """Check all three deps, then install/update ONLY what's missing or
    outdated — in ONE elevated pass (a single UAC on Windows / one sudo
    terminal on Linux). No-op when everything is already current."""
    platform = _platform_key()
    pend = _pending()
    if not pend:
        return {
            "ok": True,
            "kind": "noop",
            "message": "All dependencies are installed and up to date.",
        }
    names = [n for n, _ in pend]
    cmds = [c for _, c in pend]
    return _run_commands(cmds, names, platform)


def _run_commands(cmds: list[str], names: list[str], platform: str) -> dict[str, Any]:
    """Dispatch one-or-more install/update commands to the platform runner.
    Windows + Linux each launch ONE elevation surface (window / terminal) that
    runs every command in turn, and return immediately (async)."""
    if platform == "windows":
        return _spawn_elevated_windows(cmds, names)
    if platform == "linux":
        return _run_terminal_linux(cmds, names)
    return {
        "ok": False,
        "kind": "copy",
        "command": " && ".join(cmds),
        "message": (
            f"Auto-install isn't wired for {platform} yet. "
            "Copy the command(s) and run them in a terminal."
        ),
    }


# ─────────────────────── Windows: elevated spawn ──────────────────


def _spawn_elevated_windows(cmds: list[str], names: list[str]) -> dict[str, Any]:
    """Launch ONE elevated, VISIBLE PowerShell that runs each command in turn,
    shows its output, and pauses at the end — then return immediately (a single
    UAC prompt for the whole batch).

    ASYNC by design. The old path ran `subprocess.run(timeout=300)` and blocked
    the request until UAC + winget finished; that routinely exceeded the 30s
    satellite proxy timeout (music manifest) and surfaced to the browser as a
    500 even though the install completed in the background. Returning right
    after the spawn removes the 500, and the visible window is the honest status
    surface (the user watches winget and sees any real error — e.g. a bad
    package id — instead of a fabricated 'UAC maybe denied' message).

    We elevate `powershell.exe` (a real .exe — always resolvable, unlike the
    per-user `winget` App Execution Alias) and hand it the script over
    -EncodedCommand to dodge nested-quoting breakage."""
    label = ", ".join(names) if names else "dependencies"
    lines = [
        "$ErrorActionPreference = 'Continue'",
        f"Write-Host '=== JarvYZ dependency install: {label} ==='",
    ]
    for c in cmds:
        full = c
        is_winget = c.strip().lower().startswith("winget")
        # winget must NOT prompt (agreements / interactive picker) — it would
        # hang the window waiting for input.
        if is_winget:
            full += (
                " --accept-source-agreements --accept-package-agreements"
                " --disable-interactivity"
            )
        lines += ["Write-Host ''", f"Write-Host '> {c}'", full]
        # `winget upgrade` fails when the binary is present but NOT
        # winget-managed (a portable exe / PATH copy) — the exact class the old
        # sync path retried. Bake the fallback into the elevated script so it
        # self-heals with no output parsing: on nonzero, run the install form.
        if is_winget and " upgrade " in f" {c.strip().lower()} ":
            install_variant = full.replace(" upgrade ", " install ", 1)
            lines += [
                "if ($LASTEXITCODE -ne 0) {",
                "  Write-Host '  upgrade found nothing to update; trying install...'",
                f"  {install_variant}",
                "}",
            ]
        lines.append('Write-Host "  (exit $LASTEXITCODE)"')
    lines += [
        "Write-Host ''",
        "Write-Host 'Done. Press Enter to close this window...'",
        "Read-Host",
    ]
    inner = "\n".join(lines)
    enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
    # Outer (non-elevated) shell launches the elevated VISIBLE window and exits
    # immediately — no -Wait, so this call returns fast (one UAC prompt).
    ps_script = (
        "Start-Process -FilePath 'powershell.exe' "
        f"-ArgumentList '-NoProfile','-EncodedCommand','{enc}' -Verb RunAs"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {
            "ok": False,
            "kind": "copy",
            "command": " ; ".join(cmds),
            "error": str(e),
            "message": "Could not launch the elevated installer — copy the command(s) and run them in a terminal.",
        }
    return {
        "ok": True,
        "kind": "async",
        "command": " ; ".join(cmds),
        "spawned_terminal": "an elevated PowerShell window",
        "message": (
            "Launched in an elevated window — approve the UAC prompt. It shows "
            "install progress and stays open when done; click Re-check to refresh."
        ),
    }


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


def _run_terminal_linux(cmds: list[str], names: list[str]) -> dict[str, Any]:
    """Spawn ONE terminal window that runs each command in turn. The terminal
    is the elevation surface — each hint carries its own sudo (apt needs it;
    uv tool / brew don't), prompting inside the window. Asynchronous; we don't
    wait. The UI tells the user to watch the window + click Re-check.

    Falls through to copy-paste on:
      - No DISPLAY (headless Linux server)
      - No supported terminal binary on PATH
      - Spawn-time exception"""
    joined = " && ".join(cmds)
    if not os.environ.get("DISPLAY"):
        return {
            "ok": False,
            "kind": "copy",
            "command": joined,
            "message": (
                "No DISPLAY available (looks like a headless box). "
                "Copy the command(s) and run them in a terminal."
            ),
        }

    label = ", ".join(names) if names else "dependencies"
    # Wrap so the terminal shows each command, runs it, reports its exit code,
    # then waits for Enter so the user can read output before the window closes.
    body = [
        "echo '════════════════════════════════════════════════════════════'",
        f"echo '   JarvYZ dependency install: {label}'",
        "echo '════════════════════════════════════════════════════════════'",
    ]
    for c in cmds:
        body += ["echo", f"echo '> {c}'", c, 'echo "  (exit $?)"']
    body += ["echo", "echo 'Press Enter to close this window…'", "read"]
    wrapped = "; ".join(body)

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
        except (OSError, subprocess.SubprocessError):
            # Try the next candidate
            continue
        return {
            "ok": True,
            "kind": "async",
            "command": joined,
            "spawned_terminal": binary,
            "message": (
                f"Launched in a new {binary} window. Type your sudo password "
                "there; the output stays in that window. Click Re-check when done."
            ),
        }

    return {
        "ok": False,
        "kind": "copy",
        "command": joined,
        "message": (
            "No supported terminal emulator found "
            f"(tried: {', '.join(t[0] for t in _LINUX_TERMINALS)}). "
            "Copy the command(s) and run them in a terminal."
        ),
    }
