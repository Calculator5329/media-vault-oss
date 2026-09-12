# Changelog

## 2026-09-12

Code-only public fork of the author's private Media Vault, prepared for distribution.

- History starts fresh; no personal catalog, corrections or evidence came along.
- `vault.py` launcher for both Windows and Linux: `scan`, `serve` (default), `enrich`,
  `places`, `doctor`, `setup-models`, `test`. Re-executes itself in `.venv`.
- Configuration moved into `vault.config.json`: sources (flat folders or a root folder with
  any hierarchy under it), optional exports folder, state and models folders, port.
- Portability layer (`src/portable.py`): file locks use `fcntl` on POSIX and `msvcrt` on
  Windows; the "derived state must stay on the local drive" guard reads its removable-media
  roots from config instead of hard-coding `/run/media`.
- Enrichment runs whichever stages have both their runtime and their model present, instead
  of refusing unless every model is installed.
- Exports folder is optional everywhere (inventory, metadata, jobs).
- `scripts/doctor.py` measures Python, packages, binaries, config, models and GPU and prints
  `READY` when a scan can run. `scripts/setup_models.py` downloads pinned models with
  checksum receipts.
- ImageMagick 7 without the `identify` shim (`magick identify`) and tessdata in the usual
  places on Windows, Debian, Arch and Homebrew.
- The local-index-kit provenance and rebuild modules are vendored under `src/localindexkit`.
- Verified on the author's CachyOS machine: 266 unit tests in a fresh venv, a scan of a
  synthetic flat folder plus a three-level hierarchy (10 files, one duplicate, one video),
  viewer served on port 8771.
