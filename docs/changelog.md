# Changelog

## 2026-09-12, later

The viewer and its server code now come from the private vault's current UI branch, not the
older main branch the first cut was copied from.

- One search box with suggestions and result groups replaces the toolbar of date pickers,
  slideshow, highlights and filter chips. Year and month scrubber on the timeline.
- Map view (offline Natural Earth), trips with journey legs on the map, buckets, similar
  shots, fill-the-gaps proposals, favorite and hide per photo, face strips inside cards,
  export of buckets to folders, playback that streams originals and encodes in place.
- The viewer now lists every configured source, not only the first: catalogs under
  `.catalog/source-catalogs/` are read alongside `catalog.db`.
- Similar-shot hashing runs in the base environment (it needs no torch), so it is on for
  every install.
- Gone with the older branch: MUSIQ quality scoring, the additive-sources page under
  Imports, and the recovered-image provenance stages that only made sense for the author's
  damaged archive.
- Verified: 262 unit tests, a scan of the CC0 test library (56 files across two sources),
  faces, OCR and similar-shot passes, place labels, and the screenshots on the README page.

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
