"""Frozen-exe entry point for the standalone ``yz-music.exe``.

ONE binary, THREE modes — dispatched on argv:

  yz-music.exe                       → server mode (double-click): boot the
                                       FastAPI daemon on :9002 and open the
                                       browser to the bundled SPA. No JarvYZ.
  yz-music.exe "mpv-yt://<url>"       → protocol/CLI mode: the Windows URL
  yz-music.exe "mpv-yt-n://<url>"       handler hands the clicked link here;
  yz-music.exe "mpv-yt-q://<url>"       delegate verbatim to yz_music.cli.main,
  yz-music.exe "mpv-yt-d://<ms>"        which does the mpv IPC handoff. Also
  yz-music.exe <url-or-flags...>        covers direct CLI use + tampermonkey.
  yz-music.exe --register             → write the 4 HKCU protocol keys, each
  yz-music.exe --unregister             pointing at THIS exe. Replaces the
                                       old register.ps1 (which pointed at a
                                       hardcoded C:\\Python314\\python.exe +
                                       yt-play.py). HKCU = no admin needed.

Why a dispatcher and not separate exes: the protocol handler, the CLI, and
the server are the same code today (register.ps1 binds the protocols to
`python yt-play.py "%1"`, and yt-play.py just calls yz_music.cli.main). Freezing
one multi-mode binary keeps that single-source-of-truth and means `--register`
can point every protocol at the very exe the user just ran.

Build: see yz-music.spec + build.py in this folder. Freeze ON each target OS
(PyInstaller is not a cross-compiler). The native window is per-OS: WebView2 on
Windows, WKWebView on macOS; Linux ships lite (browser) only.
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser

# Windowed PyInstaller builds (console=False) have sys.stdout/stderr = None.
# uvicorn's log formatter calls sys.stdout.isatty() at startup, which crashes
# with AttributeError on None and kills the app before the server binds. Point
# the missing streams at devnull (.isatty() → False) so stdio is harmless
# instead of fatal. MUST run before uvicorn is imported/configured. No-op for
# the console (lite) build, where stdout/stderr are real.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Importing these at module scope makes PyInstaller's static analysis collect
# them into the bundle even though server-mode hands uvicorn an import STRING
# ("yz_music.server:app"), which the analyzer can't follow on its own.
from yz_music import cli  # noqa: F401  (collected for protocol/CLI mode)
from yz_music import server  # noqa: F401  (collected so uvicorn can import it)

_PROTOCOL_PREFIXES = ("mpv-yt://", "mpv-yt-n://", "mpv-yt-q://", "mpv-yt-d://")


def _serve(use_window: bool = True) -> int:
    """Boot uvicorn on a daemon thread, then either show a native window
    (pywebview — the yz-music.exe build) or open the system browser (the
    lite yz-music-lite.exe build, or `--browser`).

    The SAME entry script frozen two ways: if pywebview is bundled, `import
    webview` succeeds → native window; if not (portable build), it raises
    ImportError → silent browser fallback. So one codebase yields both exes.
    """
    import time
    import uvicorn

    host = os.environ.get("MUSIC_HOST", "127.0.0.1")
    port = int(os.environ.get("MUSIC_PORT", "9002"))
    url = f"http://{host}:{port}/"

    # uvicorn must NOT own the main thread — pywebview's GUI loop needs it.
    # install_signal_handlers is a no-op off the main thread (else it raises).
    config = uvicorn.Config("yz_music.server:app", host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    threading.Thread(target=server.run, daemon=True).start()

    for _ in range(100):  # wait <=10s until it's actually accepting
        if server.started:
            break
        time.sleep(0.1)

    if use_window:
        try:
            import webview  # WebView2 (Windows) / WKWebView (macOS); absent on Linux builds
        except ImportError:
            pass  # portable build: pywebview not bundled → silent browser path
        else:
            try:
                webview.create_window("yz-music", url, width=1120, height=760)
                webview.start()  # blocks on main thread until the window closes
                return 0  # window closed → exit (daemon uvicorn dies with us)
            except Exception as e:  # backend present but failed → warn + browser
                print(f"[yz-music] native window failed ({e}); opening browser")

    webbrowser.open(url)
    try:  # nothing to block on in browser mode → keep the server thread alive
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def main() -> int:
    argv = sys.argv

    if len(argv) >= 2:
        first = argv[1].strip()

        if first in ("--register", "--unregister"):
            # Windows-only; the registrar guards platform itself.
            from register_win import register, unregister

            return register() if first == "--register" else unregister()

        if first in ("--browser",):
            return _serve(use_window=False)  # force browser even in the app build
        if first in ("serve", "--serve", "server"):
            return _serve()

        # A protocol URL, a bare YouTube URL, or CLI flags → CLI path.
        # yz_music.cli.main expects the full argv (prog + args), exactly as the
        # legacy `python yt-play.py "%1"` invocation passed it.
        return cli.main(argv)

    # No args → double-click → server mode.
    return _serve()


if __name__ == "__main__":
    raise SystemExit(main())
