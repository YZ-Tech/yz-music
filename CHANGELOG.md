# Changelog

## 0.0.1

First public release of the `yz-music` satellite.

- Standalone YouTube-to-mpv player: portable single-file binaries — Windows
  (windowed + lite) and macOS (windowed + lite), Linux (lite).
- JarvYZ dynamic-module IIFE + manifest for in-app mounting.
- Pip-installable wheel.
- Cross-platform build via `standalone/build.py`; CI matrix releaser with
  optional macOS codesign + notarization.
