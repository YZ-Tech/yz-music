"""Self-registering Windows URL-protocol handler for the frozen yz-music.exe.

Port of register.ps1 to Python, with one critical difference: instead of
binding the protocols to a hardcoded ``C:\\Python314\\python.exe yt-play.py``,
it points them at ``sys.executable`` — i.e. THIS very exe — with ``"%1"`` as
the argument. The exe's dispatcher (entry.py) then routes the protocol URL
into yz_music.cli.main. So a user who runs ``yz-music.exe --register`` once gets
the full userscript → ``mpv-yt://`` → playback loop with zero setup, no admin
(HKCU), and no dependency on a system Python install.

Protocols (mirrors register.ps1):
  mpv-yt://     play now (replace playlist)
  mpv-yt-n://   insert next
  mpv-yt-q://   append to queue
  mpv-yt-d://   set audio-delay (payload = ms, e.g. mpv-yt-d://-220)
"""
from __future__ import annotations

import sys

_PROTOCOLS = ("mpv-yt", "mpv-yt-n", "mpv-yt-q", "mpv-yt-d")


def _require_windows() -> None:
    if sys.platform != "win32":
        raise SystemExit("--register / --unregister is Windows-only.")


def _exe_path() -> str:
    # When frozen, sys.executable IS yz-music.exe. When running from source
    # (python entry.py --register), fall back to "<python> <entry.py>".
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    return f'"{sys.executable}" "{sys.argv[0]}" "%1"'


def register() -> int:
    _require_windows()
    import winreg

    command = _exe_path()
    for proto in _PROTOCOLS:
        base = rf"Software\Classes\{proto}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f"URL:{proto} protocol")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        cmd_key = rf"{base}\shell\open\command"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_key) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, command)
        print(f"Registered {proto}:// -> {command}")
    print("\nDone. Protocol links now launch this yz-music.exe.")
    return 0


def unregister() -> int:
    _require_windows()
    import winreg

    for proto in _PROTOCOLS:
        try:
            # Delete leaf-up: command, open, shell, then the protocol root.
            for sub in (
                rf"Software\Classes\{proto}\shell\open\command",
                rf"Software\Classes\{proto}\shell\open",
                rf"Software\Classes\{proto}\shell",
                rf"Software\Classes\{proto}",
            ):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                except FileNotFoundError:
                    pass
            print(f"Unregistered {proto}://")
        except OSError as e:  # pragma: no cover - defensive
            print(f"Could not fully remove {proto}://: {e}")
    return 0
